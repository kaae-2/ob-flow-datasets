#!/usr/bin/env python3

"""Prepare selected FlowRepository gated FCS datasets as benchmark CSVs.

The benchmark data importer consumes one CSV per sample with marker columns and a
single `label` column. This script materializes that contract from the local
FlowRepository downloads in `datasets/import/`.

Currently supported:
- FR-FCM-ZY34 / experiment 1124 FACS validation files using the Cytobank-style
  `Krieg_FACS_gatingstrategy.xml` gate tree.
- FR-FCM-ZYVT / experiment 2045 Berlin samples with paired Kaluza `.analysis`
  workspaces and `.30.fcs` files. Other experiment 2045 laboratory exports are
  probed but not materialized.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import flowkit as fk
import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IMPORT_ROOT = REPO_ROOT / "datasets" / "import"
DEFAULT_PREPARED_ROOT = REPO_ROOT / "datasets" / "prepared" / "fcm"
DEFAULT_REPORT_ROOT = DEFAULT_IMPORT_ROOT / "_reports"
DEFAULT_PHD_ROOT = REPO_ROOT.parent / "phd"


@dataclass
class Gate:
    kind: str
    dimensions: list[str]
    mins: list[float | None] = field(default_factory=list)
    maxes: list[float | None] = field(default_factory=list)
    vertices: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class Population:
    name: str
    gate: Gate | None
    children: list["Population"] = field(default_factory=list)


@dataclass
class KaluzaGate:
    name: str
    parent: str | None
    kind: str
    x_measure: str
    y_measure: str
    x_scale: str
    y_scale: str
    points: list[tuple[float, float]]


@dataclass
class KaluzaMeasurement:
    name: str
    detector: str
    marker: str


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def namespaced_attr(element: ET.Element, suffix: str) -> str | None:
    for key, value in element.attrib.items():
        if key == suffix or key.endswith(f"}}{suffix}") or key.endswith(f":{suffix}"):
            return value
    return None


def parse_krieg_gate_tree(path: Path) -> list[Population]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    wrapped = (
        '<root xmlns:gating="urn:gating" xmlns:data-type="urn:data-type">'
        + text
        + "</root>"
    )
    root = ET.fromstring(wrapped)
    return [parse_population(child) for child in root if local_name(child.tag) == "Population"]


def parse_population(element: ET.Element) -> Population:
    gate_element = next((child for child in element if local_name(child.tag) == "Gate"), None)
    subpops = next(
        (child for child in element if local_name(child.tag) == "Subpopulations"), None
    )
    return Population(
        name=element.attrib.get("name", "unlabeled").strip() or "unlabeled",
        gate=parse_gate(gate_element) if gate_element is not None else None,
        children=[
            parse_population(child)
            for child in (list(subpops) if subpops is not None else [])
            if local_name(child.tag) == "Population"
        ],
    )


def parse_gate(gate_element: ET.Element) -> Gate | None:
    for child in gate_element.iter():
        name = local_name(child.tag)
        if name == "RectangleGate":
            return parse_rectangle_gate(child)
        if name == "PolygonGate":
            return parse_polygon_gate(child)
    return None


def parse_rectangle_gate(element: ET.Element) -> Gate:
    dimensions: list[str] = []
    mins: list[float | None] = []
    maxes: list[float | None] = []
    for dim in direct_children(element, "dimension"):
        dimensions.append(parse_dimension_name(dim))
        mins.append(parse_optional_float(namespaced_attr(dim, "min")))
        maxes.append(parse_optional_float(namespaced_attr(dim, "max")))
    return Gate(kind="rectangle", dimensions=dimensions, mins=mins, maxes=maxes)


def parse_polygon_gate(element: ET.Element) -> Gate:
    dimensions = [parse_dimension_name(dim) for dim in direct_children(element, "dimension")]
    vertices: list[tuple[float, float]] = []
    for vertex in direct_children(element, "vertex"):
        coords = [
            float(namespaced_attr(coord, "value"))
            for coord in direct_children(vertex, "coordinate")
        ]
        if len(coords) == 2:
            vertices.append((coords[0], coords[1]))
    return Gate(kind="polygon", dimensions=dimensions, vertices=vertices)


def direct_children(element: ET.Element, tag_name: str) -> list[ET.Element]:
    return [child for child in list(element) if local_name(child.tag) == tag_name]


def parse_dimension_name(element: ET.Element) -> str:
    for child in element.iter():
        if local_name(child.tag) == "fcs-dimension":
            value = namespaced_attr(child, "name")
            if value:
                return value
    raise ValueError("Gate dimension is missing data-type:name")


def parse_optional_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def parse_spill_matrix(sample: fk.Sample) -> fk.Matrix | None:
    spill = sample.metadata.get("spill") or sample.metadata.get("spillover")
    if not spill:
        return None
    parts = [part.strip() for part in str(spill).split(",")]
    n = int(parts[0])
    detectors = parts[1 : 1 + n]
    values = np.array([float(value) for value in parts[1 + n :]], dtype=float).reshape(n, n)
    return fk.Matrix(values, detectors)


def load_facs_sample(path: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str]]:
    sample = fk.Sample(str(path))
    compensation = parse_spill_matrix(sample)
    source = "raw"
    if compensation is not None:
        sample.apply_compensation(compensation)
        source = "comp"

    events = sample.as_dataframe(source=source)
    gate_df = pd.DataFrame(index=events.index)
    marker_df = pd.DataFrame(index=events.index)
    marker_names: dict[str, str] = {}

    for column in events.columns:
        detector = str(column[0]).strip()
        marker = str(column[1]).strip()
        gate_df[detector] = events[column].to_numpy()
        gate_df[f"Comp-{detector}"] = events[column].to_numpy()

        if keep_marker(detector, marker):
            clean_marker = clean_column_name(marker)
            marker_df[unique_column_name(marker_df.columns, clean_marker)] = events[column].to_numpy()
            marker_names[detector] = clean_marker

    return gate_df, marker_df, marker_names


def keep_marker(detector: str, marker: str) -> bool:
    detector_l = detector.lower()
    marker_l = marker.lower()
    if not marker or marker in {"-", ""}:
        return False
    if detector_l == "time" or detector_l.startswith(("fsc", "ssc")):
        return False
    if marker_l in {"nir", "live/dead", "livedead", "dead", "viability"}:
        return False
    return True


def clean_column_name(value: str) -> str:
    return re.sub(r"\s+", "_", value.strip())


def unique_column_name(existing: pd.Index, base: str) -> str:
    if base not in existing:
        return base
    suffix = 2
    while f"{base}_{suffix}" in existing:
        suffix += 1
    return f"{base}_{suffix}"


def read_kaluza_analysis_xml(path: Path) -> ET.Element:
    with zipfile.ZipFile(path) as zf:
        xml_name = next(name for name in zf.namelist() if name.lower().endswith(".xml"))
        return ET.fromstring(zf.read(xml_name))


def parse_kaluza_measurements(root: ET.Element) -> dict[str, KaluzaMeasurement]:
    measurements: dict[str, KaluzaMeasurement] = {}
    for element in root.findall(".//Protocol/Measurements/Measurement"):
        name = (element.attrib.get("N") or "").strip()
        detector = (element.findtext("Detector") or "").strip()
        marker = (element.findtext("Description") or name).strip()
        if name and detector:
            measurements[name] = KaluzaMeasurement(name=name, detector=detector, marker=marker)
    return measurements


def parse_kaluza_plot_bindings(root: ET.Element) -> dict[tuple[str | None, str, str], tuple[str, str, str, str]]:
    bindings: dict[tuple[str | None, str, str], tuple[str, str, str, str]] = {}
    for node in root.findall(".//ReportSheet/Node"):
        sheet = node.find("S")
        if sheet is None:
            continue
        x_axis = sheet.find("X")
        y_axis = sheet.find("Y")
        if x_axis is None or y_axis is None:
            continue
        x_measure = x_axis.attrib.get("M")
        y_measure = y_axis.attrib.get("M")
        if not x_measure or not y_measure:
            continue
        parent = sheet.attrib.get("G")
        parent_key = parent.strip() if parent and parent.strip() else None
        x_scale = x_axis.attrib.get("S", "I")
        y_scale = y_axis.attrib.get("S", "I")
        for gate in sheet.findall("./G/Gate"):
            name = (gate.attrib.get("G") or "").strip()
            discriminator = gate.attrib.get("J", "0")
            if name:
                bindings[(parent_key, name, discriminator)] = (
                    x_measure,
                    y_measure,
                    x_scale,
                    y_scale,
                )
    return bindings


def parse_kaluza_points(element: ET.Element) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for point in element.findall("./P/P"):
        raw = point.attrib.get("O")
        if not raw:
            continue
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) != 2:
            continue
        points.append((float(parts[0]), float(parts[1])))
    return points


def parse_kaluza_gates(root: ET.Element) -> list[KaluzaGate]:
    bindings = parse_kaluza_plot_bindings(root)
    gates: list[KaluzaGate] = []
    gates_element = root.find(".//Protocol/Gates")
    if gates_element is None:
        return gates
    for element in list(gates_element):
        if element.tag not in {"P", "R"}:
            continue
        name = (element.attrib.get("N") or "").strip()
        if not name:
            continue
        parent = element.attrib.get("G")
        parent_key = parent.strip() if parent and parent.strip() else None
        discriminator = element.attrib.get("J", "0")
        binding = bindings.get((parent_key, name, discriminator))
        if binding is None:
            continue
        points = parse_kaluza_points(element)
        if element.tag == "P" and len(points) < 3:
            continue
        if element.tag == "R" and len(points) != 2:
            continue
        x_measure, y_measure, x_scale, y_scale = binding
        gates.append(
            KaluzaGate(
                name=name,
                parent=parent_key,
                kind="polygon" if element.tag == "P" else "rectangle",
                x_measure=x_measure,
                y_measure=y_measure,
                x_scale=x_scale,
                y_scale=y_scale,
                points=points,
            )
        )
    return gates


def kaluza_measure_to_fcs_channel(measurement_name: str, measurements: dict[str, KaluzaMeasurement]) -> str:
    measurement = measurements.get(measurement_name)
    if measurement is None:
        raise KeyError(f"Kaluza measurement {measurement_name!r} is not defined")
    detector = measurement.detector.strip()
    measurement_type = measurement.name.rsplit(" ", 1)[-1].upper()
    if detector.lower() == "time" or measurement.name.upper() == "TIME":
        return "TIME"
    suffix = {"INT": "A", "TOF": "W"}.get(measurement_type, measurement_type)
    return f"{detector}-{suffix}"


def parse_kaluza_compensation(root: ET.Element) -> tuple[list[str], np.ndarray] | None:
    detectors = [
        (element.text or "").strip()
        for element in root.findall(".//Protocol/Compensation/CompensatedDetectors/D")
        if (element.text or "").strip()
    ]
    if not detectors:
        return None
    spill = np.eye(len(detectors), dtype=float)
    index = {name: idx for idx, name in enumerate(detectors)}
    for sv in root.findall(".//Protocol/Compensation/S/SV"):
        source = sv.attrib.get("S")
        channel = sv.attrib.get("C")
        value = sv.attrib.get("V")
        if source in index and channel in index and value is not None:
            spill[index[channel], index[source]] = float(value)
    return detectors, np.linalg.inv(spill)


def load_kaluza_sample(
    fcs_path: Path, root: ET.Element
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, KaluzaMeasurement]]:
    measurements = parse_kaluza_measurements(root)
    sample = fk.Sample(str(fcs_path))
    events = sample.as_dataframe(source="raw")
    gate_df = pd.DataFrame(index=events.index)
    marker_df = pd.DataFrame(index=events.index)

    raw_columns: dict[str, np.ndarray] = {}
    for column in events.columns:
        detector = str(column[0]).strip()
        values = events[column].to_numpy(dtype=float, copy=False)
        raw_columns[detector] = values
        if detector.endswith("-A"):
            raw_columns[detector[:-2]] = values
        gate_df[detector] = values

    compensation = parse_kaluza_compensation(root)
    if compensation is not None:
        detectors, inverse_spill = compensation
        available = [detector for detector in detectors if detector in raw_columns]
        if available:
            raw_matrix = np.column_stack([raw_columns[detector] for detector in available])
            sub_indices = [detectors.index(detector) for detector in available]
            sub_inverse = inverse_spill[np.ix_(sub_indices, sub_indices)]
            compensated = raw_matrix @ sub_inverse.T
            for idx, detector in enumerate(available):
                gate_df[f"comp:{detector}-A"] = compensated[:, idx]

    for measurement in measurements.values():
        channel = kaluza_measure_to_fcs_channel(measurement.name, measurements)
        if channel in gate_df:
            gate_df[measurement.name] = gate_df[channel].to_numpy()
        comp_channel = f"comp:{channel}"
        if comp_channel in gate_df:
            gate_df[f"comp:{measurement.name}"] = gate_df[comp_channel].to_numpy()
        if keep_kaluza_marker(measurement):
            clean_marker = clean_column_name(canonical_kaluza_marker(measurement.marker))
            marker_df[unique_column_name(marker_df.columns, clean_marker)] = gate_df[channel].to_numpy()

    return gate_df, marker_df, measurements


def keep_kaluza_marker(measurement: KaluzaMeasurement) -> bool:
    detector_l = measurement.detector.lower()
    marker_l = measurement.marker.lower()
    if detector_l == "time" or detector_l in {"fs", "ss"}:
        return False
    if not marker_l or marker_l in {"fl1", "fl2", "fl4", "fl5", "fl6", "fl8", "fl9", "fl10"}:
        return False
    if canonical_kaluza_marker(measurement.marker) == "SYTO41":
        return False
    return True


def canonical_kaluza_marker(marker: str) -> str:
    if marker.strip().upper() in {"SY41", "SYTO41"}:
        return "SYTO41"
    return marker


def kaluza_axis_values(gate_df: pd.DataFrame, measurement_name: str, scale: str) -> np.ndarray:
    key = f"comp:{measurement_name}" if scale == "C" else measurement_name
    if key in gate_df.columns:
        return gate_df[key].to_numpy(dtype=float, copy=False)
    if measurement_name in gate_df.columns:
        return gate_df[measurement_name].to_numpy(dtype=float, copy=False)
    raise KeyError(f"Kaluza gate axis {measurement_name!r} is not present in FCS data")


def evaluate_kaluza_gate(gate: KaluzaGate, gate_df: pd.DataFrame) -> np.ndarray:
    x = kaluza_axis_values(gate_df, gate.x_measure, gate.x_scale)
    y = kaluza_axis_values(gate_df, gate.y_measure, gate.y_scale)
    if gate.kind == "rectangle":
        (x0, y0), (x1, y1) = gate.points
        return (x >= min(x0, x1)) & (x <= max(x0, x1)) & (y >= min(y0, y1)) & (y <= max(y0, y1))
    return points_in_polygon(x, y, gate.points)


def label_kaluza_events_with_audit(
    gate_df: pd.DataFrame, gates: list[KaluzaGate]
) -> tuple[pd.Series, dict[str, object]]:
    labels = pd.Series(["unlabeled"] * len(gate_df), index=gate_df.index, dtype="object")
    masks: dict[str, np.ndarray] = {}
    for gate in gates:
        if gate.parent is not None and gate.parent not in masks:
            raise ValueError(f"Kaluza gate {gate.name!r} has unresolved parent {gate.parent!r}")
        parent_mask = masks.get(gate.parent, np.ones(len(gate_df), dtype=bool))
        mask = parent_mask & evaluate_kaluza_gate(gate, gate_df)
        masks[gate.name] = mask

    parent_names = {gate.parent for gate in gates if gate.parent is not None}
    terminal_names = [gate.name for gate in gates if gate.name not in parent_names]
    membership_count = np.zeros(len(gate_df), dtype=np.uint8)
    for name in terminal_names:
        membership_count += masks[name]
    for name in terminal_names:
        exclusive_mask = masks[name] & (membership_count == 1)
        labels.iloc[np.flatnonzero(exclusive_mask)] = name
    labels = labels.rename("label")
    return labels, {
        "target_gates": terminal_names,
        "target_mask_counts": {name: int(masks[name].sum()) for name in terminal_names},
        "exclusive_target_counts": {
            name: int((masks[name] & (membership_count == 1)).sum())
            for name in terminal_names
        },
        "ambiguous_terminal_memberships": int((membership_count > 1).sum()),
        "unlabeled_count": int((labels == "unlabeled").sum()),
    }


def label_kaluza_events(gate_df: pd.DataFrame, gates: list[KaluzaGate]) -> pd.Series:
    labels, _audit = label_kaluza_events_with_audit(gate_df, gates)
    return labels


def label_events(gate_df: pd.DataFrame, populations: list[Population]) -> pd.Series:
    labels = pd.Series(["unlabeled"] * len(gate_df), index=gate_df.index, dtype="object")
    parent = np.ones(len(gate_df), dtype=bool)
    for population in populations:
        apply_population(population, gate_df, parent, labels)
    return labels.rename("label")


def apply_population(
    population: Population,
    gate_df: pd.DataFrame,
    parent_mask: np.ndarray,
    labels: pd.Series,
) -> None:
    gate_mask = evaluate_gate(population.gate, gate_df) if population.gate else parent_mask
    mask = parent_mask & gate_mask
    labels.iloc[np.flatnonzero(mask)] = population.name
    for child in population.children:
        apply_population(child, gate_df, mask, labels)


def evaluate_gate(gate: Gate | None, gate_df: pd.DataFrame) -> np.ndarray:
    if gate is None:
        return np.ones(len(gate_df), dtype=bool)
    if gate.kind == "rectangle":
        mask = np.ones(len(gate_df), dtype=bool)
        for dim, min_value, max_value in zip(gate.dimensions, gate.mins, gate.maxes):
            values = gate_values(gate_df, dim)
            if min_value is not None:
                mask &= values >= min_value
            if max_value is not None:
                mask &= values <= max_value
        return mask
    if gate.kind == "polygon":
        if len(gate.dimensions) != 2 or len(gate.vertices) < 3:
            return np.zeros(len(gate_df), dtype=bool)
        x = gate_values(gate_df, gate.dimensions[0])
        y = gate_values(gate_df, gate.dimensions[1])
        return points_in_polygon(x, y, gate.vertices)
    return np.zeros(len(gate_df), dtype=bool)


def gate_values(gate_df: pd.DataFrame, dimension: str) -> np.ndarray:
    if dimension in gate_df.columns:
        return gate_df[dimension].to_numpy(dtype=float, copy=False)
    if dimension.startswith("Comp-") and dimension[5:] in gate_df.columns:
        return gate_df[dimension[5:]].to_numpy(dtype=float, copy=False)
    raise KeyError(f"Gate dimension {dimension!r} is not present in FCS data")


def points_in_polygon(
    x: np.ndarray, y: np.ndarray, vertices: list[tuple[float, float]]
) -> np.ndarray:
    inside = np.zeros(len(x), dtype=bool)
    j = len(vertices) - 1
    for i, (xi, yi) in enumerate(vertices):
        xj, yj = vertices[j]
        intersects = ((yi > y) != (yj > y)) & (
            x < (xj - xi) * (y - yi) / ((yj - yi) if not math.isclose(yj, yi) else 1e-12) + xi
        )
        inside ^= intersects
        j = i
    return inside


def prepare_1124(input_dir: Path, output_dir: Path, limit: int | None) -> dict[str, object]:
    gate_path = input_dir / "Krieg_FACS_gatingstrategy.xml"
    populations = parse_krieg_gate_tree(gate_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    fcs_paths = sorted(input_dir.glob("facs_*.fcs"))
    if limit is not None:
        fcs_paths = fcs_paths[:limit]

    written = []
    for index, fcs_path in enumerate(fcs_paths, start=1):
        gate_df, marker_df, _marker_names = load_facs_sample(fcs_path)
        labels = label_events(gate_df, populations)
        out_df = marker_df.copy()
        out_df["label"] = labels.to_numpy()
        output_path = output_dir / f"{fcs_path.stem}_annotated.csv"
        out_df.to_csv(output_path, index=False)
        written.append(
            {
                "sample": fcs_path.name,
                "output": str(output_path),
                "rows": int(len(out_df)),
                "labels": sorted(set(labels.astype(str))),
            }
        )
        print(f"[1124] {index}/{len(fcs_paths)} wrote {output_path} rows={len(out_df)}")

    return {"dataset": "FR-FCM-ZY34", "written": written}


def bln_fcs_path_for_analysis(input_dir: Path, analysis_path: Path) -> Path:
    prefix = analysis_path.name.split("_d15", 1)[0]
    return input_dir / f"{prefix}.30.fcs"


def prepare_2045_kaluza(input_dir: Path, output_dir: Path, limit: int | None) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    analysis_paths = sorted(input_dir.glob("Bln*.analysis"))
    if limit is not None:
        analysis_paths = analysis_paths[:limit]

    written = []
    skipped = []
    for index, analysis_path in enumerate(analysis_paths, start=1):
        fcs_path = bln_fcs_path_for_analysis(input_dir, analysis_path)
        if not fcs_path.exists():
            skipped.append({"analysis": analysis_path.name, "reason": "missing matching .30.fcs"})
            continue
        try:
            root = read_kaluza_analysis_xml(analysis_path)
            gates = parse_kaluza_gates(root)
            if not gates:
                skipped.append({"analysis": analysis_path.name, "reason": "no parsed Kaluza gates"})
                continue
            gate_df, marker_df, measurements = load_kaluza_sample(fcs_path, root)
            labels, label_audit = label_kaluza_events_with_audit(gate_df, gates)
            out_df = marker_df.copy()
            out_df["label"] = labels.to_numpy()
            output_path = output_dir / f"{fcs_path.stem}_annotated.csv"
            out_df.to_csv(output_path, index=False)
            written.append(
                {
                    "analysis": analysis_path.name,
                    "sample": fcs_path.name,
                    "output": str(output_path),
                    "rows": int(len(out_df)),
                    "columns": list(out_df.columns),
                    "gate_hierarchy": [
                        {"parent": gate.parent, "name": gate.name} for gate in gates
                    ],
                    "model_features": list(marker_df.columns),
                    "gating_only_measurements": sorted(
                        {
                            canonical_kaluza_marker(measurement.marker)
                            for measurement in measurements.values()
                            if not keep_kaluza_marker(measurement)
                            and canonical_kaluza_marker(measurement.marker) == "SYTO41"
                        }
                    ),
                    "labels": sorted(set(labels.astype(str))),
                    **label_audit,
                }
            )
            print(f"[2045-kaluza] {index}/{len(analysis_paths)} wrote {output_path} rows={len(out_df)}")
        except Exception as exc:
            skipped.append({"analysis": analysis_path.name, "reason": str(exc)})

    return {
        "dataset": "FR-FCM-ZYVT",
        "source": "Kaluza .analysis Bln subset",
        "written": written,
        "skipped": skipped,
    }


def probe_2045(input_dir: Path, phd_root: Path) -> dict[str, object]:
    exact_pairs = []
    for xml_path in sorted(input_dir.glob("*.xml")):
        text = xml_path.read_text(encoding="utf-8", errors="ignore")
        for data_filename in sorted(set(re.findall(r"<data_filename>([^<]+)", text))):
            fcs_path = input_dir / data_filename
            if fcs_path.exists():
                exact_pairs.append((xml_path, fcs_path))
                break

    probes = []
    for xml_path, fcs_path in exact_pairs[:3]:
        output_path = phd_root / "tmp" / "ob-flow-test" / f"{fcs_path.stem}.csv"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "cargo",
            "run",
            "--example",
            "export_single_tube_csv",
            "--",
            "--experiment-xml",
            str(xml_path),
            "--fcs",
            str(fcs_path),
            "--output-csv",
            str(output_path),
        ]
        result = subprocess.run(
            cmd,
            cwd=phd_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        probes.append(
            {
                "xml": xml_path.name,
                "fcs": fcs_path.name,
                "returncode": result.returncode,
                "output_tail": result.stdout[-1200:],
            }
        )

    return {
        "dataset": "FR-FCM-ZYVT",
        "exact_xml_fcs_pairs": len(exact_pairs),
        "probe_results": probes,
        "status": "blocked_until_2045_xml_to_fcs_and_gate_resolution_is_added",
    }


def public_kaluza_audit(
    report: dict[str, object], limit: int | None
) -> dict[str, object]:
    if limit is not None:
        raise ValueError(
            "cannot publish FR-FCM-ZYVT source-mask audit from a limited run"
        )
    skipped = report.get("skipped", [])
    if skipped:
        raise ValueError("cannot publish FR-FCM-ZYVT source-mask audit with skipped samples")

    written = []
    for record in report.get("written", []):
        public_record = {
            key: value for key, value in record.items() if key != "output"
        }
        sample_stem = Path(str(public_record["sample"])).stem
        public_record["artifact"] = (
            f"PediatricBALL_fcm/{sample_stem}_annotated.csv.zst"
        )
        written.append(public_record)

    written.sort(key=lambda record: str(record["sample"]))
    return {
        "schema_version": 1,
        "dataset": report["dataset"],
        "source": report["source"],
        "written": written,
        "skipped": [],
    }


def write_public_kaluza_audit(
    report: dict[str, object], path: Path, limit: int | None
) -> None:
    payload = public_kaluza_audit(report, limit)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=["FR-FCM-ZY34", "FR-FCM-ZYVT", "all"], default="all")
    parser.add_argument("--import-root", type=Path, default=DEFAULT_IMPORT_ROOT)
    parser.add_argument("--prepared-root", type=Path, default=DEFAULT_PREPARED_ROOT)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--phd-root", type=Path, default=DEFAULT_PHD_ROOT)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    reports = []
    if args.dataset in {"FR-FCM-ZY34", "all"}:
        reports.append(
            prepare_1124(
                args.import_root / "1124",
                args.prepared_root / "FR-FCM-ZY34" / "KriegFACS_fcm",
                args.limit,
            )
        )
    if args.dataset in {"FR-FCM-ZYVT", "all"}:
        kaluza_report = prepare_2045_kaluza(
            args.import_root / "2045",
            args.prepared_root / "FR-FCM-ZYVT" / "PediatricBALL_fcm",
            args.limit,
        )
        reports.append(kaluza_report)
        if args.limit is None:
            write_public_kaluza_audit(
                kaluza_report,
                args.prepared_root / "FR-FCM-ZYVT" / "source-mask-audit.json",
                args.limit,
            )
        reports.append(probe_2045(args.import_root / "2045", args.phd_root))

    report_path = args.report_root / "flowrepository-gated-prep-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(reports, indent=2), encoding="utf-8")
    print(f"wrote report {report_path}")


if __name__ == "__main__":
    main()
