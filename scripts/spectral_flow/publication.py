"""Portable preparation audits accompanying the two published murine panels."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from .core import sha256, write_json


def write_publication_audits(
    records: list[dict], output_root: Path, versions: dict
) -> list[str]:
    """Publish complete panel inventories with relative paths and source provenance."""
    groups = defaultdict(list)
    for record in records:
        if record["accession"] != "FR-FCM-Z7CH":
            raise ValueError("Only the selected Z7CH panels may be published")
        groups[record["dataset_id"]].append(record)
    paths = []
    for dataset_id, samples in sorted(groups.items()):
        if len(samples) != 10 or len({s["filename"] for s in samples}) != 10:
            raise ValueError(
                f"A complete panel requires ten unique samples: {dataset_id}"
            )
        replicates = Counter(sample["replicate_id"] for sample in samples)
        if len(replicates) != 5 or set(replicates.values()) != {2}:
            raise ValueError(
                "Each strain must have five mice with two paired conditions"
            )
        for replicate in replicates:
            treatments = {
                s["treatment"] for s in samples if s["replicate_id"] == replicate
            }
            if len(treatments) != 2:
                raise ValueError(f"Paired conditions are not distinct: {replicate}")
        first = samples[0]
        shortname = first["shortname"]
        directory = output_root / dataset_id
        labels = Counter()
        portable_samples = []
        expected_objects = set()
        for sample in sorted(samples, key=lambda item: item["filename"]):
            output = sample["output"]
            if (
                sample["shortname"] != shortname
                or output["markers"] != first["output"]["markers"]
            ):
                raise ValueError(
                    "Sample shortnames or marker schemas differ within a panel"
                )
            if output["rows"] != sample["source_event_count"]:
                raise ValueError("Exported row count differs from its source")
            objects = []
            for source in output["source_objects"]:
                path = Path(source["path"])
                relative = path.resolve().relative_to(directory.resolve())
                if any(character.isspace() for character in str(relative)):
                    raise ValueError(
                        f"Prepared object path must be URL-safe: {relative}"
                    )
                if (
                    relative.parent != Path(shortname)
                    or sha256(path) != source["sha256"]
                ):
                    raise ValueError(
                        f"Prepared object path or checksum mismatch: {path}"
                    )
                objects.append({**source, "path": str(relative)})
                expected_objects.add(path.resolve())
            labels.update(sample["label_audit"]["label_counts"])
            portable_samples.append(
                {
                    key: sample[key]
                    for key in [
                        "filename",
                        "mouse",
                        "replicate_id",
                        "treatment",
                        "workspace",
                        "workspace_group",
                        "workspace_sample_id",
                        "source_event_count",
                        "sources",
                        "annotation_status",
                        "target_gates",
                        "channel_mapping",
                        "label_audit",
                        "count_comparison",
                    ]
                }
                | {
                    "prepared_csv": str(Path(shortname) / Path(output["csv"]).name),
                    "rows": output["rows"],
                    "csv_sha256": output["csv_sha256"],
                    "compressed_sha256": output["compressed_sha256"],
                    "compressed_bytes": output["compressed_bytes"],
                    "source_objects": objects,
                    "representation": output["representation"],
                }
            )
        actual_objects = {
            p.resolve()
            for p in (directory / shortname).glob("*.csv.zst*")
            if not p.name.endswith(".sha256")
        }
        if actual_objects != expected_objects:
            raise ValueError(
                f"Prepared-object inventory differs from sample records: {dataset_id}"
            )
        display_strain = "C57BL/6" if first["panel"] == "C57BL6" else "BALB/c"
        document = {
            "schema_version": "1.0.0",
            "dataset_id": dataset_id,
            "shortname": shortname,
            "display_name": f"Murine splenocytes {display_strain}",
            "flowrepository_accession": "FR-FCM-Z7CH",
            "source_url": "https://flowrepository.org/id/FR-FCM-Z7CH",
            "publication_doi": "10.1002/cyto.a.24926",
            "preparation_script": "scripts/prepare-spectral-flow.py",
            "versions": versions,
            "sample_count": 10,
            "biological_replicates": 5,
            "total_events": sum(sample["output"]["rows"] for sample in samples),
            "markers": first["output"]["markers"],
            "label_counts": dict(sorted(labels.items())),
            "label_policy": "Exactly one selected lineage gate; otherwise unlabeled. Retain all events in original order.",
            "feature_processing": first["feature_processing"],
            "gating_processing": first["gating_processing"],
            "reference_event_memberships_verified": False,
            "annotation_caveat": "Replayed gate counts differ from saved FlowJo counts; per-gate differences follow. No parameters were fitted to force agreement.",
            "split_constraint": "Keep both treatments from the same strain-qualified mouse together.",
            "license_status": "unknown/not-recorded",
            "samples": portable_samples,
        }
        path = directory / "preparation-audit.json"
        write_json(path, document)
        paths.append(str(path))
    return paths
