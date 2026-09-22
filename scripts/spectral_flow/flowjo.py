"""Replay complete Z7CH workspaces and preserve their sample-level provenance."""

from __future__ import annotations

import json
import warnings
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import flowio
import flowkit as fk
import numpy as np
from lxml import etree as ET

from .core import export_csv, export_memberships, label_events, sha256, write_json

Z7_MARKERS = [
    "CD8",
    "CD3",
    "TCRgd",
    "CD62L",
    "IL-4/5",
    "CD45R",
    "NK1.1",
    "IFNg",
    "CD44",
    "CD45",
    "IL-2",
    "CD19",
    "TNF",
    "IL-17A",
    "CD4",
]


def spreadsheet_rows(path: Path) -> dict[str, dict[int, dict[str, str]]]:
    """Read this source workbook without an optional Excel runtime dependency."""
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    relationship_ns = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    )
    with zipfile.ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            strings = ["".join(n.itertext()) for n in shared.findall("s:si", ns)]
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in relations}
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets = {}
        for sheet in workbook.findall("s:sheets/s:sheet", ns):
            target = targets[sheet.get(f"{{{relationship_ns}}}id")]
            target = target.lstrip("/") if target.startswith("/") else "xl/" + target
            document = ET.fromstring(archive.read(target))
            rows = {}
            for row in document.findall("s:sheetData/s:row", ns):
                cells = {}
                for cell in row:
                    value = cell.findtext("s:v", namespaces=ns) or ""
                    if cell.get("t") == "s":
                        value = strings[int(value)]
                    elif cell.get("t") == "inlineStr":
                        value = "".join(cell.find("s:is", ns).itertext())
                    column = "".join(c for c in cell.get("r") if c.isalpha())
                    cells[column] = value.strip()
                rows[int(row.get("r"))] = cells
            sheets[sheet.get("name")] = rows
    return sheets


def sample_manifest(directory: Path) -> list[dict]:
    """Cross-check spreadsheet biological samples against their workspace records."""
    sheets = spreadsheet_rows(directory / "Supplementary_Note_1.xlsx")
    entries = []
    for strain, sheet, workspace, group in [
        ("C57BL6", "Full Panel_C57BL_6", "OMIP-ICS-C57BL_6.wsp", "ICS_C57_TS"),
        ("BALBc", "Full Panel_BALB_c", "OMIP-ICS-BALB_c.wsp", "ICS_ BALB_TS"),
    ]:
        document = ET.parse(directory / workspace)
        samples = {}
        for sample in document.findall("./SampleList/Sample"):
            dataset = sample.find("DataSet")
            name = Path(unquote(urlparse(dataset.get("uri")).path)).name
            if name in samples:
                raise ValueError(f"Duplicate workspace filename: {name}")
            samples[name] = (dataset.get("sampleID"), sample.find("SampleNode"))
        members = document.findall(
            f"./Groups/GroupNode[@name='{group}']/Group/SampleRefs/SampleRef"
        )
        if {n.get("sampleID") for n in members} != {sid for sid, _ in samples.values()}:
            raise ValueError(
                f"Workspace group membership differs from sample list: {workspace}"
            )
        panel_entries = []
        for number in range(3, 13):
            row = sheets[sheet][number]
            filename, mouse, treatment = row["B"], row["C"], row["D"]
            if filename not in samples or not (directory / filename).is_file():
                raise ValueError(
                    f"Spreadsheet sample not found in workspace or locally: {filename}"
                )
            sid, node = samples[filename]
            if node.get("name") != filename:
                raise ValueError(
                    f"Workspace sample name differs from its URI: {filename}"
                )
            panel_entries.append(
                {
                    "accession": "FR-FCM-Z7CH",
                    "panel": strain,
                    "dataset_id": f"FR-FCM-Z7CH-{strain}",
                    "shortname": f"MurineSplenocytes{strain}_fcm",
                    "filename": filename,
                    "mouse": mouse,
                    "replicate_id": f"{strain}:{mouse}",
                    "treatment": treatment,
                    "workspace": workspace,
                    "workspace_group": group,
                    "workspace_sample_id": sid,
                    "source_event_count": int(node.get("count")),
                }
            )
        if {e["filename"] for e in panel_entries} != set(samples):
            raise ValueError("Spreadsheet and workspace sample inventories differ")
        entries.extend(panel_entries)
    return entries


def verify_source(directory: Path, name: str) -> dict:
    """Require downloaded bytes to still match their recorded source checksum."""
    manifest = json.loads((directory / "download-manifest.json").read_text())
    entry = next(f for f in manifest["files"] if f["filename"] == name)
    path = directory / name
    digest = sha256(path)
    if entry["status"] != "complete" or digest != entry["sha256"]:
        raise ValueError(f"Source integrity mismatch: {path}")
    return {"filename": name, "sha256": digest, "url": entry["source_url"]}


def features(
    path: Path, markers: list[str]
) -> tuple[np.ndarray, list[int], list[dict]]:
    """Select native FCS marker values, retaining sign, precision and event order."""
    data = flowio.FlowData(path)
    mapping = {}
    for index, channel in data.channels.items():
        marker = channel.get("pns", "").split(":", 1)[0].strip()
        if marker in markers:
            if marker in mapping:
                raise ValueError(f"Duplicate biological marker {marker} in {path}")
            mapping[marker] = (int(index) - 1, channel["pnn"])
    if set(mapping) != set(markers):
        raise ValueError(
            f"Missing panel markers in {path}: {set(markers) - set(mapping)}"
        )
    indices = [mapping[marker][0] for marker in markers]
    dtype = np.float32 if data.data_type == "F" else np.float64
    values = data.as_array(preprocess=False)[:, indices].astype(dtype)
    channels = [
        {"marker": m, "channel": mapping[m][1], "index": mapping[m][0]} for m in markers
    ]
    return values, indices, channels


def saved_counts(node, path: tuple[str, ...] = ("root",)) -> dict:
    """Read counts keyed by full paths, including Boolean population nodes."""
    result = {}
    for population in node.findall("./Subpopulations/*"):
        if population.tag not in {"Population", "NotNode", "AndNode", "OrNode"}:
            continue
        key = path + (population.get("name"),)
        result[key] = int(population.get("count"))
        result.update(saved_counts(population, key))
    return result


def compare_counts(expected: dict, masks: dict) -> list[dict]:
    """Report differences without treating matching counts as identical membership."""
    if set(expected) != set(masks):
        raise ValueError(
            "Replayed gate paths differ from the saved workspace gate paths"
        )
    result = []
    for key, count in expected.items():
        actual = int(masks[key].sum())
        result.append(
            {
                "gate_path": list(key),
                "saved_count": count,
                "replayed_count": actual,
                "difference": actual - count,
                "relative_difference": (actual - count) / count if count else None,
            }
        )
    return result


def prepare_z7ch_sample(
    directory: Path, entry: dict, output: Path, reports: Path
) -> dict:
    """Replay one original workspace sample and export its coarse-lineage CSV."""
    name = entry["filename"]
    inputs = [
        verify_source(directory, file)
        for file in [
            name,
            entry["workspace"],
            "Supplementary_Note_1.xlsx",
        ]
    ]
    sample = fk.Sample(directory / name, filename_as_id=True)
    if sample.event_count != entry["source_event_count"]:
        raise ValueError(f"FCS event count differs from workspace: {name}")
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="WSP references .*, but sample was not loaded."
        )
        workspace = fk.Workspace(
            directory / entry["workspace"], fcs_samples=[sample], filename_as_id=True
        )
    workspace.analyze_samples(sample_id=name, use_mp=False)
    masks = {
        path + (gate,): workspace.get_gate_membership(name, gate, path)
        for gate, path in workspace.get_gate_ids(name)
    }
    parent = ("root", "Lymphocytes", "Singlets", "Live CD45+")
    nk = "NK1.1" if entry["panel"] == "C57BL6" else "NKp46"
    targets = {
        "B-1": parent + ("B-1 Cells",),
        "B-2": parent + ("B-2 Cells",),
        "NK": parent + ("Other", nk + "+"),
        "T": parent + ("Other", "T Cells"),
    }
    labels, audit = label_events(
        [{label: masks[path] for label, path in targets.items()}]
    )
    markers = [nk if marker == "NK1.1" else marker for marker in Z7_MARKERS]
    values, indices, channels = features(directory / name, markers)
    feature_match = np.array_equal(values, sample.get_events(source="raw")[:, indices])
    if not feature_match:
        raise ValueError(
            "Native feature values differ from the gating input; inspect FCS preprocessing"
        )
    node = ET.parse(directory / entry["workspace"]).find(
        f"./SampleList/Sample/SampleNode[@sampleID='{entry['workspace_sample_id']}']"
    )
    comparison = compare_counts(saved_counts(node), masks)
    changed = any(row["difference"] for row in comparison)
    sample_reports = reports / entry["dataset_id"]
    stem = Path(name).stem
    record = {
        **entry,
        "sources": inputs,
        "feature_values_equal_gating_input": bool(feature_match),
        "annotation_status": (
            "workspace_replay_counts_differ"
            if changed
            else "workspace_replay_counts_match"
        ),
        "reference_event_memberships_verified": False,
        "feature_processing": "native FCS fluorescence values; no additional compensation or display transform",
        "gating_processing": "original per-sample FlowJo transforms and gates via FlowKit; ellipse approximated as polygon",
        "target_gates": {label: list(path) for label, path in targets.items()},
        "channel_mapping": channels,
        "label_audit": audit,
        "count_comparison": comparison,
    }
    record["output"] = export_csv(
        output / entry["dataset_id"] / entry["shortname"] / f"{stem}_annotated.csv",
        values,
        markers,
        labels,
    )
    record["memberships"] = export_memberships(
        sample_reports / f"{stem}_memberships.csv.zst", masks
    )
    write_json(sample_reports / f"{stem}.json", record)
    return record
