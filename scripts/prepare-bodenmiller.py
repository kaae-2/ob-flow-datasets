#!/usr/bin/env python3
"""Canonicalize Bodenmiller population labels and rebuild prepared archives."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path


DATASET_DIR = (
    Path(__file__).parents[1]
    / 'prepared'
    / 'cytof'
    / 'BodenmillerXL'
    / 'PBMC_cytof'
)
# Nowicka et al. define the expert-merged population as "surface negative
# cells" while their machine-readable factor uses the shorthand "surface-".
# The existing prepared archives incorrectly converted that shorthand to
# "ungated", which the importer excludes from target populations.
SOURCE_LABELS = {b'surface-', b'ungated'}
CANONICAL_LABEL = b'surface negative cells'
EXPECTED_ARCHIVES = 16
EXPECTED_ROWS = 172_791
EXPECTED_SURFACE_NEGATIVE_ROWS = 3_901


def canonicalize_csv(content: bytes, archive_name: str) -> tuple[bytes, int, int]:
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].rstrip(b'\r\n').split(b',')[-1] != b'label':
        raise ValueError(f'{archive_name}: expected final column named label')

    output = bytearray(lines[0])
    mapped = 0
    canonical = 0
    for line in lines[1:]:
        if line.endswith(b'\r\n'):
            ending = b'\r\n'
        elif line.endswith(b'\n'):
            ending = b'\n'
        else:
            ending = b''
        row = line[: -len(ending)] if ending else line
        label = row.rsplit(b',', 1)[-1]
        if label in SOURCE_LABELS:
            row = row[: -len(label)] + CANONICAL_LABEL
            mapped += 1
            canonical += 1
        elif label == CANONICAL_LABEL:
            canonical += 1
        output.extend(row + ending)
    return bytes(output), mapped, canonical


def rebuild(dataset_dir: Path) -> None:
    archives = sorted(dataset_dir.glob('*.csv.zst'))
    if len(archives) != EXPECTED_ARCHIVES:
        raise ValueError(
            f'expected {EXPECTED_ARCHIVES} Bodenmiller archives, found {len(archives)}'
        )

    total_rows = 0
    total_mapped = 0
    total_canonical = 0
    with tempfile.TemporaryDirectory(prefix='.bodenmiller-', dir=dataset_dir) as temp:
        staging = Path(temp)
        replacements: list[tuple[Path, Path]] = []
        for archive in archives:
            decompressed = subprocess.run(
                ['zstd', '--decompress', '--stdout', str(archive)],
                check=True,
                capture_output=True,
            ).stdout
            corrected, mapped, canonical = canonicalize_csv(
                decompressed, archive.name
            )
            rows = len(corrected.splitlines()) - 1
            total_rows += rows
            total_mapped += mapped
            total_canonical += canonical

            csv_path = staging / archive.name.removesuffix('.zst')
            staged_archive = staging / archive.name
            csv_path.write_bytes(corrected)
            subprocess.run(
                [
                    'zstd',
                    '-12',
                    '--threads=1',
                    '--force',
                    '--quiet',
                    str(csv_path),
                    '-o',
                    str(staged_archive),
                ],
                check=True,
            )
            digest = hashlib.sha256(staged_archive.read_bytes()).hexdigest()
            staged_checksum = staging / f'{archive.name}.sha256'
            staged_checksum.write_text(f'{digest}  {archive.name}\n')
            replacements.extend(
                [
                    (staged_archive, archive),
                    (staged_checksum, archive.with_suffix('.zst.sha256')),
                ]
            )

        if total_rows != EXPECTED_ROWS:
            raise ValueError(f'expected {EXPECTED_ROWS} rows, found {total_rows}')
        if total_canonical != EXPECTED_SURFACE_NEGATIVE_ROWS:
            raise ValueError(
                'expected '
                f'{EXPECTED_SURFACE_NEGATIVE_ROWS} surface-negative rows, '
                f'found {total_canonical}'
            )

        for staged, destination in replacements:
            os.replace(staged, destination)

    print(
        f'Rebuilt {len(archives)} archives: {total_rows} rows, '
        f'{total_mapped} labels canonicalized, '
        f'{total_canonical} surface-negative rows validated'
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-dir', type=Path, default=DATASET_DIR)
    args = parser.parse_args()
    rebuild(args.dataset_dir.resolve())


if __name__ == '__main__':
    main()
