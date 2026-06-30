#!/usr/bin/env python3
"""Prepare Zenodo 15723074 donor CSVs for the benchmark importer."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path


DATASET_NAME = '15723074'
SHORTNAME = 'SpectralFlowHealthyAdults_fcm'
LABEL_SOURCE_COLUMN = 'cell_type'
LABEL_COLUMN = 'label'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    repo_root = Path(__file__).resolve().parents[2]
    parser.add_argument('--input-dir', default=repo_root / 'datasets' / 'import' / DATASET_NAME)
    parser.add_argument('--prepared-root', default=repo_root / 'datasets' / 'prepared')
    parser.add_argument('--report-root', default=repo_root / 'datasets' / 'import' / '_reports')
    return parser.parse_args()


def validate_float(value: str, path: Path, row_number: int, column: str) -> None:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f'{path}:{row_number} column {column} is not numeric: {value!r}') from error
    if not math.isfinite(parsed):
        raise ValueError(f'{path}:{row_number} column {column} is not finite: {value!r}')


def prepare_file(path: Path, output_path: Path, expected_markers: list[str] | None) -> dict[str, object]:
    label_counts: Counter[str] = Counter()
    rows = 0
    with path.open('r', newline='', encoding='utf-8') as input_handle:
        reader = csv.DictReader(input_handle)
        if not reader.fieldnames:
            raise ValueError(f'{path} has no header')
        if LABEL_SOURCE_COLUMN not in reader.fieldnames:
            raise ValueError(f'{path} missing {LABEL_SOURCE_COLUMN!r} column')
        markers = [column for column in reader.fieldnames if column != LABEL_SOURCE_COLUMN]
        if expected_markers is not None and markers != expected_markers:
            raise ValueError(f'{path} marker schema differs from previous files')

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open('w', newline='', encoding='utf-8') as output_handle:
            writer = csv.DictWriter(output_handle, fieldnames=[*markers, LABEL_COLUMN])
            writer.writeheader()
            for row_number, row in enumerate(reader, start=2):
                label = row[LABEL_SOURCE_COLUMN].strip()
                if not label:
                    raise ValueError(f'{path}:{row_number} has empty label')
                output_row = {marker: row[marker] for marker in markers}
                for marker in markers:
                    validate_float(output_row[marker], path, row_number, marker)
                output_row[LABEL_COLUMN] = label
                writer.writerow(output_row)
                label_counts[label] += 1
                rows += 1

    return {
        'input': str(path),
        'output': str(output_path),
        'rows': rows,
        'labels': dict(sorted(label_counts.items())),
        'markers': markers,
    }


def main() -> None:
    args = parse_args()
    input_dir = Path(args.input_dir)
    prepared_root = Path(args.prepared_root)
    report_root = Path(args.report_root)
    output_dir = prepared_root / 'fcm' / DATASET_NAME / SHORTNAME
    files = sorted(input_dir.glob('donor*.csv'))
    if not files:
        raise SystemExit(f'No donor CSVs found under {input_dir}')

    expected_markers: list[str] | None = None
    reports: list[dict[str, object]] = []
    total_labels: Counter[str] = Counter()
    total_rows = 0
    for path in files:
        output_path = output_dir / f'{path.stem}_annotated.csv'
        report = prepare_file(path, output_path, expected_markers)
        if expected_markers is None:
            expected_markers = list(report['markers'])
        reports.append(report)
        total_rows += int(report['rows'])
        total_labels.update(report['labels'])

    report = {
        'dataset_name': DATASET_NAME,
        'shortname': SHORTNAME,
        'platform': 'fcm',
        'source': 'https://zenodo.org/records/15723074',
        'title': 'Spectral Flow Cytometry Analysis of Seven Healthy Adults',
        'files': reports,
        'file_count': len(reports),
        'total_rows': total_rows,
        'markers': expected_markers,
        'label_column': LABEL_COLUMN,
        'labels': dict(sorted(total_labels.items())),
    }
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / 'zenodo-15723074-prep-report.json'
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(f'Wrote {len(reports)} files to {output_dir}')
    print(f'Total rows: {total_rows}')
    print(f'Report: {report_path}')


if __name__ == '__main__':
    main()
