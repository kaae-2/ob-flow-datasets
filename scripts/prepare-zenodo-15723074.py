#!/usr/bin/env python3
"""Prepare Zenodo 15723074 donor CSVs for the benchmark importer."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path

DATASET_NAME = "15723074"
SHORTNAME = "SpectralFlowHealthyAdults_fcm"
LABEL_SOURCE_COLUMN = "cell_type"
LABEL_COLUMN = "label"
REFERENCE_COFACTOR = 150.0
PUBLICATION_COFACTORS = {
    "CD14": 10000.0,
    "CD19": 1000.0,
    "CD3": 3000.0,
    "CD56": 2000.0,
    "CD45RA": 4000.0,
    "CD8": 3000.0,
    "CD4": 5000.0,
    "CCR7": 6000.0,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    repo_root = Path(__file__).resolve().parents[2]
    parser.add_argument(
        "--input-dir", default=repo_root / "datasets" / "import" / DATASET_NAME
    )
    parser.add_argument("--prepared-root", default=repo_root / "datasets" / "prepared")
    parser.add_argument(
        "--report-root", default=repo_root / "datasets" / "import" / "_reports"
    )
    return parser.parse_args()


def validate_float(value: str, path: Path, row_number: int, column: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(
            f"{path}:{row_number} column {column} is not numeric: {value!r}"
        ) from error
    if not math.isfinite(parsed):
        raise ValueError(
            f"{path}:{row_number} column {column} is not finite: {value!r}"
        )
    return parsed


def prepare_file(path: Path, output_path: Path) -> dict[str, object]:
    label_counts: Counter[str] = Counter()
    rows = 0
    with path.open("r", newline="", encoding="utf-8") as input_handle:
        reader = csv.DictReader(input_handle)
        if not reader.fieldnames:
            raise ValueError(f"{path} has no header")
        if LABEL_SOURCE_COLUMN not in reader.fieldnames:
            raise ValueError(f"{path} missing {LABEL_SOURCE_COLUMN!r} column")
        missing_markers = [
            marker
            for marker in PUBLICATION_COFACTORS
            if marker not in reader.fieldnames
        ]
        if missing_markers:
            raise ValueError(
                f'{path} missing publication markers: {", ".join(missing_markers)}'
            )
        markers = list(PUBLICATION_COFACTORS)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="", encoding="utf-8") as output_handle:
            writer = csv.DictWriter(output_handle, fieldnames=[*markers, LABEL_COLUMN])
            writer.writeheader()
            for row_number, row in enumerate(reader, start=2):
                label = row[LABEL_SOURCE_COLUMN].strip()
                if not label:
                    raise ValueError(f"{path}:{row_number} has empty label")
                output_row = {
                    marker: format(
                        validate_float(row[marker], path, row_number, marker)
                        * REFERENCE_COFACTOR
                        / source_cofactor,
                        ".17g",
                    )
                    for marker, source_cofactor in PUBLICATION_COFACTORS.items()
                }
                output_row[LABEL_COLUMN] = label
                writer.writerow(output_row)
                label_counts[label] += 1
                rows += 1

    return {
        "input": str(path),
        "output": str(output_path),
        "rows": rows,
        "labels": dict(sorted(label_counts.items())),
        "markers": markers,
        "reference_cofactor": REFERENCE_COFACTOR,
        "source_cofactors": PUBLICATION_COFACTORS,
    }


def main() -> None:
    args = parse_args()
    input_dir = Path(args.input_dir)
    prepared_root = Path(args.prepared_root)
    report_root = Path(args.report_root)
    output_dir = prepared_root / "fcm" / DATASET_NAME / SHORTNAME
    files = sorted(input_dir.glob("donor*.csv"))
    if not files:
        raise SystemExit(f"No donor CSVs found under {input_dir}")

    reports: list[dict[str, object]] = []
    total_labels: Counter[str] = Counter()
    total_rows = 0
    for path in files:
        output_path = output_dir / f"{path.stem}_annotated.csv"
        report = prepare_file(path, output_path)
        reports.append(report)
        total_rows += int(report["rows"])
        total_labels.update(report["labels"])

    report = {
        "dataset_name": DATASET_NAME,
        "shortname": SHORTNAME,
        "platform": "fcm",
        "source": "https://zenodo.org/records/15723074",
        "title": "Spectral Flow Cytometry Analysis of Seven Healthy Adults",
        "files": reports,
        "file_count": len(reports),
        "total_rows": total_rows,
        "markers": list(PUBLICATION_COFACTORS),
        "reference_cofactor": REFERENCE_COFACTOR,
        "source_cofactors": PUBLICATION_COFACTORS,
        "column_scaling": "stored_value = source_value * reference_cofactor / source_cofactor",
        "label_column": LABEL_COLUMN,
        "labels": dict(sorted(total_labels.items())),
    }
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / "zenodo-15723074-prep-report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(reports)} files to {output_dir}")
    print(f"Total rows: {total_rows}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
