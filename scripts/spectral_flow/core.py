"""CSV preparation contract and explicit, exclusive event labels."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import zstandard as zstd


def sha256(path: Path) -> str:
    """Hash a source or output object without reading it all into memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: dict) -> None:
    """Write a deterministic, finite-valued preparation report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def split_compressed(path: Path, max_bytes: int = 100 * 1024 * 1024) -> list[dict]:
    """Package a whole object as contiguous parts only when it exceeds the limit."""
    if max_bytes <= 0:
        raise ValueError("Object size limit must be positive")
    if list(path.parent.glob(path.name + ".part*")):
        raise FileExistsError(f"Split objects already exist for {path}")
    size = path.stat().st_size
    if size <= max_bytes:
        return [{"path": str(path), "bytes": size, "sha256": sha256(path)}]
    expected = sha256(path)
    objects = []
    with path.open("rb") as source:
        while block := source.read(max_bytes):
            part = Path(f"{path}.part{len(objects):04d}")
            with part.open("xb") as target:
                target.write(block)
            objects.append(
                {"path": str(part), "bytes": len(block), "sha256": sha256(part)}
            )
    assembled = hashlib.sha256()
    for item in objects:
        with Path(item["path"]).open("rb") as source:
            while block := source.read(1024 * 1024):
                assembled.update(block)
    if assembled.hexdigest() != expected:
        raise ValueError(f"Split object assembly mismatch: {path}")
    path.unlink()
    return objects


def export_csv(
    path: Path, values: np.ndarray, markers: list[str], labels: np.ndarray
) -> dict:
    """Export and reread the marker-plus-label contract; preserve source event order."""
    if values.shape != (len(labels), len(markers)) or not np.isfinite(values).all():
        raise ValueError(
            "Features must be finite and aligned with marker names and labels"
        )
    if len(set(markers)) != len(markers) or "label" in markers:
        raise ValueError("Marker names must be unique and cannot be 'label'")
    if any(not isinstance(label, str) or not label for label in labels):
        raise ValueError("Every event must have a nonempty label")
    compressed = Path(str(path) + ".zst")
    checksum = Path(str(compressed) + ".sha256")
    if any(p.exists() for p in (path, compressed, checksum)) or list(
        path.parent.glob(compressed.name + ".part*")
    ):
        raise FileExistsError(f"Refusing to overwrite existing sample output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(values, columns=markers)
    frame["label"] = labels
    frame.to_csv(
        path,
        index=False,
        float_format="%.9g" if values.dtype == np.float32 else "%.17g",
    )
    offset = 0
    with pd.read_csv(
        path, chunksize=100_000, keep_default_na=False, float_precision="round_trip"
    ) as reader:
        for chunk in reader:
            if chunk.columns.tolist() != markers + ["label"]:
                raise ValueError(f"CSV schema mismatch: {path}")
            end = offset + len(chunk)
            if not np.array_equal(
                chunk[markers].to_numpy(dtype=values.dtype), values[offset:end]
            ):
                raise ValueError(f"CSV numeric roundtrip failed: {path}")
            if not np.array_equal(chunk.label.to_numpy(), labels[offset:end]):
                raise ValueError(f"CSV labels or event order changed: {path}")
            offset = end
    if offset != len(values):
        raise ValueError(f"CSV row count mismatch: {path}")
    with path.open("rb") as source, compressed.open("xb") as target:
        zstd.ZstdCompressor(level=6, write_checksum=True).copy_stream(source, target)
    digest = hashlib.sha256()
    with compressed.open("rb") as source, zstd.ZstdDecompressor().stream_reader(
        source
    ) as reader:
        while block := reader.read(1024 * 1024):
            digest.update(block)
    csv_hash = sha256(path)
    if digest.hexdigest() != csv_hash:
        raise ValueError(f"Compressed CSV roundtrip failed: {path}")
    compressed_hash = sha256(compressed)
    compressed_bytes = compressed.stat().st_size
    checksum.write_text(f"{compressed_hash}  {compressed.name}\n")
    objects = split_compressed(compressed)
    return {
        "csv": str(path),
        "compressed_csv": str(compressed),
        "rows": len(values),
        "markers": markers,
        "csv_sha256": csv_hash,
        "compressed_sha256": compressed_hash,
        "compressed_bytes": compressed_bytes,
        "source_objects": objects,
        "representation": "split" if len(objects) > 1 else "whole",
        "numeric_roundtrip": "exact_at_source_dtype",
        "source_dtype": str(values.dtype),
    }


def export_memberships(path: Path, masks: dict[tuple[str, ...], np.ndarray]) -> dict:
    """Keep every full-path gate membership in an event-indexed sidecar CSV."""
    names = [json.dumps(key, ensure_ascii=False) for key in masks]
    frame = pd.DataFrame(
        np.column_stack(list(masks.values())).astype(np.uint8), columns=names
    )
    frame.insert(0, "event_index", np.arange(len(frame)))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    frame.to_csv(path, index=False, compression={"method": "zstd", "level": 6})
    return {
        "path": str(path),
        "sha256": sha256(path),
        "rows": len(frame),
        "gates": len(masks),
    }


def label_events(
    configurations: list[dict[str, np.ndarray]],
) -> tuple[np.ndarray, dict]:
    """Label only exclusive memberships on which every configuration agrees."""
    if not configurations or not configurations[0]:
        raise ValueError("At least one nonempty label configuration is required")
    names = tuple(configurations[0])
    size = len(configurations[0][names[0]])
    candidates, counts = [], []
    for masks in configurations:
        if tuple(masks) != names:
            raise ValueError("Configurations must have identical ordered label names")
        if any(mask.shape != (size,) or mask.dtype != bool for mask in masks.values()):
            raise ValueError(
                "Membership masks must be aligned one-dimensional booleans"
            )
        membership = np.column_stack(list(masks.values()))
        hits = membership.sum(axis=1)
        labels = np.full(size, "unlabeled", dtype=object)
        exclusive = hits == 1
        labels[exclusive] = np.asarray(names)[membership[exclusive].argmax(axis=1)]
        candidates.append(labels)
        counts.append(hits)
    labels = candidates[0].copy()
    agreement = np.all(np.vstack(candidates) == labels, axis=0)
    labels[~agreement] = "unlabeled"
    hit_counts = np.vstack(counts)
    return labels, {
        "configuration_disagreement": int((~agreement).sum()),
        "overlap_in_any_configuration": int((hit_counts > 1).any(axis=0).sum()),
        "unmatched_in_all_configurations": int((hit_counts == 0).all(axis=0).sum()),
        "label_counts": dict(
            zip(*[a.tolist() for a in np.unique(labels, return_counts=True)])
        ),
    }
