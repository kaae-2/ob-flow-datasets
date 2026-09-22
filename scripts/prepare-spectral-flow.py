#!/usr/bin/env python3
"""Export biological Z7CH samples with workspace-derived coarse-lineage labels."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from spectral_flow.core import sha256, write_json
from spectral_flow.flowjo import prepare_z7ch_sample, sample_manifest
from spectral_flow.publication import write_publication_audits

DATASETS = Path(__file__).resolve().parents[1]


def main() -> None:
    """Prepare separate panels, preserving every event and source provenance."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["Z7CH"], default="Z7CH")
    parser.add_argument("--import-root", type=Path, default=DATASETS / "import")
    parser.add_argument("--output-root", type=Path, default=DATASETS / "prepared/fcm")
    parser.add_argument(
        "--report-root", type=Path, default=DATASETS / "import/_reports/spectral-flow"
    )
    parser.add_argument(
        "--sample", action="append", help="Exact Z7CH filename; repeat for a pilot"
    )
    args = parser.parse_args()
    report_path = args.report_root / "preparation.json"
    if report_path.exists():
        parser.error(
            f"Report already exists: {report_path}; select a fresh --report-root"
        )
    scripts = [
        Path(__file__).resolve(),
        *sorted((DATASETS / "scripts/spectral_flow").glob("*.py")),
    ]
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "versions": {
            name: version(name)
            for name in [
                "flowkit",
                "flowio",
                "flowutils",
                "numpy",
                "pandas",
                "lxml",
                "zstandard",
            ]
        },
        "code_sha256": {
            str(path.relative_to(DATASETS)): sha256(path) for path in scripts
        },
        "samples": [],
        "requested_dataset": args.dataset,
        "sample_filter": args.sample,
        "benchmark_configuration_changed": False,
    }
    write_json(report_path, report)
    try:
        directory = args.import_root / "FR-FCM-Z7CH"
        entries = sample_manifest(directory)
        if args.sample:
            if set(args.sample) - {entry["filename"] for entry in entries}:
                raise ValueError(
                    "Requested samples do not exist in the biological sample manifest"
                )
            entries = [entry for entry in entries if entry["filename"] in args.sample]
        schemas = {}
        for index, entry in enumerate(entries, 1):
            print(f"Z7CH {index}/{len(entries)}: {entry['filename']}", flush=True)
            result = prepare_z7ch_sample(
                directory, entry, args.output_root, args.report_root
            )
            schema = result["output"]["markers"]
            if schemas.setdefault(entry["panel"], schema) != schema:
                raise ValueError("Marker schemas differ within a strain")
            report["samples"].append(result)
            write_json(report_path, report)
            print(
                f"  {result['output']['rows']:,} rows; {result['label_audit']['label_counts']}",
                flush=True,
            )
        if not args.sample:
            report["publication_audits"] = write_publication_audits(
                report["samples"], args.output_root, report["versions"]
            )
        report["status"] = "complete_with_annotation_limitations"
        report["total_rows"] = sum(
            sample["output"]["rows"] for sample in report["samples"]
        )
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(report_path, report)
        print(
            f"Completed {len(report['samples'])} CSVs, {report['total_rows']:,} rows. Report: {report_path}",
            flush=True,
        )
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        write_json(report_path, report)
        raise


if __name__ == "__main__":
    main()
