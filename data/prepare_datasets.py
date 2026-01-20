#!/usr/bin/env python
"""
Data preparation utility for the `data` submodule.

- Accepts a directory, tar archive, or single FCS file (gzipped or not).
- When multiple FCS samples are present: chooses one sample at random (seeded) as
  the training sample and treats the remaining samples as tests.
- Writes gzipped CSV matrices and labels for training and test splits, along
  with a dataset-level label_key mapping from integer ids to label strings.

Usage (example):
    python prepare_datasets.py \
        --data.raw "datasets/data/covid" \
        --output_dir "preprocessing/out/data/data_import/preprocessing/data_preprocessing/default" \
        --name data_import

This script reuses helper functions from `preprocessing/data_preprocessing.py`.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import tarfile
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Import helpers from the preprocessing module
# Import helpers by adding repo root to sys.path at runtime
import sys
from pathlib import Path
repo_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo_root))
# Workaround for numpy 2.4+: fcsparser expects ndarray.newbyteorder to exist
import numpy as _np
# numpy.ndarray is a C-implemented type; instead monkeypatch at the instance-method level by
# providing a compat function in the module that fcsparser expects. Some versions of fcsparser
# call `.newbyteorder()` on arrays. We provide a small helper fallback function to be injected
# into the fcsparser module after it is imported. We'll import fcsparser lazily where needed.

from preprocessing.data_preprocessing import (
    build_label_key,
    extract_labels_from_dataframe,
    is_flowjo_workspace,
    label_samples_from_flowjo_workspace_by_sample,
    map_labels_to_ints,
    parse_fcs_to_dataframe,
    prepared_fcs_inputs,
    split_train_test,
    workspace_materialized,
)


def _sanitize_sample_id(p: Path) -> str:
    """Return a safe sample id derived from the path name (remove suffixes and spaces)."""
    name = p.name
    # remove common suffixes repeatedly (e.g. .gz, .fcs)
    lowered = name.lower()
    while lowered.endswith(".gz") or lowered.endswith(".fcs"):
        if lowered.endswith(".gz"):
            name = name[: -3]
            lowered = name.lower()
        if lowered.endswith(".fcs"):
            name = name[: -4]
            lowered = name.lower()
    # replace whitespace with underscore and remove problematic chars
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"[^A-Za-z0-9_.-]", "", name)
    return name


def write_gz_csv(df: pd.DataFrame, path: Path, header: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # pandas supports compression='gzip'
    df.to_csv(path, index=False, header=header, compression="gzip")


def write_gz_series(s: pd.Series, path: Path, header: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    s.to_csv(path, index=False, header=header, compression="gzip")


def _write_label_key(out_dir: Path, name: str, id_to_label: Dict[int, str]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id_to_label": {str(k): v for k, v in id_to_label.items()}}
    key_path = out_dir / f"{name}.label_key.json.gz"
    with gzip.open(key_path, "wt") as handle:
        json.dump(payload, handle, indent=2)


def _write_test_archives(
    out_dir: Path, name: str, test_samples: Dict[str, Tuple[pd.DataFrame, pd.Series]]
) -> None:
    matrices_path = out_dir / f"{name}.test.matrices.tar.gz"
    labels_path = out_dir / f"{name}.test.labels.tar.gz"
    with tempfile.TemporaryDirectory() as tmpdir:
        matrix_files: List[Path] = []
        label_files: List[Path] = []
        for sid, (features, labels) in test_samples.items():
            matrix_file = Path(tmpdir) / f"{sid}.matrix.csv.gz"
            label_file = Path(tmpdir) / f"{sid}.labels.csv.gz"
            write_gz_csv(features, matrix_file)
            write_gz_series(labels, label_file)
            matrix_files.append(matrix_file)
            label_files.append(label_file)

        with tarfile.open(matrices_path, "w:gz") as tar:
            for path in sorted(matrix_files, key=lambda p: p.name):
                tar.add(path, arcname=path.name)

        with tarfile.open(labels_path, "w:gz") as tar:
            for path in sorted(label_files, key=lambda p: p.name):
                tar.add(path, arcname=path.name)


def prepare_from_fcs(
    raw_input: str,
    output_dir: str,
    name: str,
    seed: int = 42,
    train_sample: Optional[str] = None,
    label_input: Optional[str] = None,
    test_sample_limit: Optional[int] = None,
) -> None:
    """Main entrypoint: read FCS inputs and write training + test archives.

    If only one FCS sample is provided, fall back to an internal train/test split
    of rows within that sample.
    """
    out_dir = Path(output_dir)
    name = str(name)

    per_sample: Dict[str, Tuple[pd.DataFrame, pd.Series]] = {}

    with prepared_fcs_inputs(raw_input) as ready_fcs:
        if len(ready_fcs) == 0:
            raise FileNotFoundError(f"No Fcs inputs found at {raw_input}")

        if label_input and is_flowjo_workspace(label_input):
            with workspace_materialized(label_input) as workspace_path:
                flowjo_samples = label_samples_from_flowjo_workspace_by_sample(
                    workspace_path, ready_fcs
                )
            for sample_id, (features, labels) in flowjo_samples.items():
                per_sample[_sanitize_sample_id(Path(sample_id))] = (features, labels)
        else:
            for p in ready_fcs:
                try:
                    df = parse_fcs_to_dataframe(str(p))
                except Exception as exc:
                    print(
                        f"Warning: failed to parse {p}: {exc}; emitting empty placeholder dataframe"
                    )
                    df = pd.DataFrame({"f1": []})
                features, labels = extract_labels_from_dataframe(df)
                sid = _sanitize_sample_id(p)
                per_sample[sid] = (features, labels)

    samples = sorted(per_sample.keys())

    label_series = [labels for _, labels in per_sample.values()]
    id_to_label = build_label_key(label_series)

    mapped_samples: Dict[str, Tuple[pd.DataFrame, pd.Series]] = {}
    for sid, (features, labels) in per_sample.items():
        mapped_samples[sid] = (features, map_labels_to_ints(labels, id_to_label))

    if len(samples) == 1:
        feats, labs = mapped_samples[samples[0]]
        (train_feats, train_labels), (test_feats, test_labels) = split_train_test(
            feats, labs, method="default", seed=seed
        )

        if train_labels is None or test_labels is None:
            raise ValueError("Expected labels for single-sample split.")

        write_gz_csv(train_feats, out_dir / f"{name}.train.matrix.csv.gz")
        write_gz_series(train_labels, out_dir / f"{name}.train.labels.csv.gz")
        _write_test_archives(
            out_dir,
            name,
            {samples[0]: (test_feats, test_labels)},
        )
        _write_label_key(out_dir, name, id_to_label)
        print(f"Wrote training and test splits for single sample {samples[0]}")
        return

    # Multiple samples: choose one sample to be the training sample
    chosen_train = None
    if train_sample is not None:
        if train_sample not in per_sample:
            raise ValueError(f"Requested train-sample '{train_sample}' not found. Available: {samples}")
        chosen_train = train_sample
    else:
        rng = np.random.default_rng(seed)
        chosen_train = rng.choice(samples)

    test_samples: Dict[str, Tuple[pd.DataFrame, pd.Series]] = {}

    remaining = [sid for sid in samples if sid != chosen_train]
    if test_sample_limit is not None:
        if test_sample_limit <= 0:
            raise ValueError("test-sample-limit must be a positive integer.")
        if len(remaining) > test_sample_limit:
            rng = np.random.default_rng(seed)
            remaining = sorted(rng.choice(remaining, size=test_sample_limit, replace=False))

    for sid in remaining:
        feats, labs = mapped_samples[sid]
        test_samples[sid] = (feats, labs)

    train_feats, train_labels = mapped_samples[chosen_train]
    write_gz_csv(train_feats, out_dir / f"{name}.train.matrix.csv.gz")
    write_gz_series(train_labels, out_dir / f"{name}.train.labels.csv.gz")

    if not test_samples:
        test_samples = {"empty": (pd.DataFrame(), pd.Series(dtype=int))}

    _write_test_archives(out_dir, name, test_samples)

    _write_label_key(out_dir, name, id_to_label)

    print(f"Selected training sample: {chosen_train}")
    print(f"Wrote training outputs and test archives to: {out_dir}")


def parse_args():
    p = argparse.ArgumentParser(description="Prepare datasets from FCS inputs into train/test files")
    p.add_argument("--data.raw", type=str, required=True, dest='data_raw', help="Path to FCS file, directory, or tar archive")
    p.add_argument("--data.labels", type=str, default=None, dest="data_labels", help="Optional FlowJo workspace (.wsp/.wps) to derive labels")
    p.add_argument("--output_dir", type=str, required=True, help="Directory to write output matrix/label gz files")
    p.add_argument("--name", type=str, required=True, help="Base dataset name for output files")
    p.add_argument("--seed", type=int, default=42, help="Random seed for deterministic train sample selection")
    p.add_argument("--train-sample", type=str, default=None, help="(Optional) sample id to use as training sample (overrides random selection)")
    p.add_argument("--test-sample-limit", type=int, default=None, help="Limit number of test samples (random subset)")
    return p.parse_args()


def main():
    args = parse_args()
    prepare_from_fcs(
        args.data_raw,
        args.output_dir,
        args.name,
        seed=args.seed,
        train_sample=args.train_sample,
        label_input=args.data_labels,
        test_sample_limit=args.test_sample_limit,
    )


if __name__ == "__main__":
    main()
