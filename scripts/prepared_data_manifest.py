#!/usr/bin/env python3
"""Validate a pinned prepared-data tree and emit its deterministic manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable


REPOSITORY_URL = 'https://github.com/kaae-2/ob-flow-datasets'
RAW_REPOSITORY_URL = 'https://raw.githubusercontent.com/kaae-2/ob-flow-datasets'
FULL_COMMIT_RE = re.compile(r'^[0-9a-f]{40}$')
PART_RE = re.compile(r'^(?P<whole>.+\.csv\.zst)\.part(?P<number>[0-9]+)$')
CHECKSUM_SUFFIX = '.csv.zst.sha256'
WHOLE_SUFFIX = '.csv.zst'
READ_CHUNK_SIZE = 8 * 1024 * 1024
DOWNLOAD_RETRIES = 4


class ContractError(ValueError):
    """Raised when the prepared tree violates the publication contract."""


@dataclass(frozen=True)
class TreeEntry:
    path: str
    size: int
    blob_sha: str


class LocalGitSource:
    def __init__(self, repository: Path, revision: str) -> None:
        self.repository = repository.resolve()
        self.revision = revision

    def entries(self) -> list[TreeEntry]:
        proc = subprocess.run(
            [
                'git',
                'ls-tree',
                '-r',
                '-l',
                '-z',
                self.revision,
                '--',
                'prepared',
            ],
            cwd=self.repository,
            check=True,
            capture_output=True,
        )
        entries: list[TreeEntry] = []
        for record in proc.stdout.split(b'\0'):
            if not record:
                continue
            metadata, raw_path = record.split(b'\t', 1)
            _mode, object_type, blob_sha, size = metadata.decode().split()
            if object_type == 'blob':
                entries.append(
                    TreeEntry(raw_path.decode(), int(size), blob_sha)
                )
        return entries

    def open(self, path: str) -> BinaryIO:
        proc = subprocess.Popen(
            ['git', 'cat-file', 'blob', f'{self.revision}:{path}'],
            cwd=self.repository,
            stdout=subprocess.PIPE,
        )
        if proc.stdout is None:
            proc.kill()
            raise RuntimeError(f'Could not read {path} from Git')
        return _CheckedProcessStream(proc, path)


class _CheckedProcessStream:
    def __init__(self, process: subprocess.Popen[bytes], path: str) -> None:
        self.process = process
        self.path = path
        assert process.stdout is not None
        self.stdout = process.stdout

    def read(self, size: int = -1) -> bytes:
        return self.stdout.read(size)

    def __enter__(self) -> '_CheckedProcessStream':
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stdout.close()
        returncode = self.process.wait()
        if exc_type is None and returncode != 0:
            raise RuntimeError(f'Git failed while reading {self.path}')


class GitHubSource:
    def __init__(self, revision: str) -> None:
        self.revision = revision

    def entries(self) -> list[TreeEntry]:
        url = (
            'https://api.github.com/repos/kaae-2/ob-flow-datasets/git/trees/'
            f'{self.revision}?recursive=1'
        )
        with _urlopen_with_retries(url) as response:
            payload = json.load(response)
        if payload.get('truncated'):
            raise ContractError('GitHub returned a truncated prepared-data tree')
        return [
            TreeEntry(item['path'], int(item['size']), item['sha'])
            for item in payload.get('tree', [])
            if item.get('type') == 'blob'
            and isinstance(item.get('path'), str)
            and item['path'].startswith('prepared/')
        ]

    def open(self, path: str) -> BinaryIO:
        url = f'{RAW_REPOSITORY_URL}/{self.revision}/{path}'
        return _urlopen_with_retries(url)


def _urlopen_with_retries(url: str) -> BinaryIO:
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            return urllib.request.urlopen(url)
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == DOWNLOAD_RETRIES:
                raise
        except urllib.error.URLError:
            if attempt == DOWNLOAD_RETRIES:
                raise
        time.sleep(2 ** (attempt - 1))
    raise RuntimeError(f'Could not read {url}')


def require_full_revision(revision: str) -> str:
    normalized = revision.strip()
    if not FULL_COMMIT_RE.fullmatch(normalized):
        raise ContractError(
            'dataset revision must be a full 40-character lowercase Git commit SHA'
        )
    return normalized


def _prepared_entries(entries: list[TreeEntry]) -> list[TreeEntry]:
    return sorted(
        [
            entry
            for entry in entries
            if entry.path.endswith(CHECKSUM_SUFFIX)
            or entry.path.endswith(WHOLE_SUFFIX)
            or PART_RE.fullmatch(entry.path)
        ],
        key=lambda entry: entry.path,
    )


def _tree_identity(entries: list[TreeEntry]) -> str:
    facts = [
        {'path': entry.path, 'size': entry.size, 'blob_sha': entry.blob_sha}
        for entry in entries
    ]
    encoded = json.dumps(
        facts, sort_keys=True, separators=(',', ':'), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _sample_coordinates(whole_path: str) -> tuple[str, str]:
    parts = whole_path.split('/')
    if len(parts) != 5 or parts[0] != 'prepared':
        raise ContractError(
            f'prepared sample does not match prepared/<platform>/<dataset>/<shortname>/<file>: {whole_path}'
        )
    return parts[2], '/'.join(parts[1:])


def classify_samples(entries: list[TreeEntry]) -> list[dict]:
    relevant = _prepared_entries(entries)
    checksums: dict[str, TreeEntry] = {}
    wholes: dict[str, TreeEntry] = {}
    parts: dict[str, list[tuple[int, TreeEntry]]] = {}

    for entry in relevant:
        if entry.path.endswith(CHECKSUM_SUFFIX):
            whole_path = entry.path[: -len('.sha256')]
            checksums[whole_path] = entry
            continue
        match = PART_RE.fullmatch(entry.path)
        if match:
            parts.setdefault(match.group('whole'), []).append(
                (int(match.group('number')), entry)
            )
            continue
        wholes[entry.path] = entry

    logical_paths = sorted(set(checksums) | set(wholes) | set(parts))
    samples: list[dict] = []
    for whole_path in logical_paths:
        checksum = checksums.get(whole_path)
        whole = wholes.get(whole_path)
        numbered_parts = parts.get(whole_path, [])
        if checksum is None:
            raise ContractError(f'orphan data object without checksum: {whole_path}')
        if whole is not None and numbered_parts:
            raise ContractError(f'ambiguous whole and split representations: {whole_path}')
        if whole is None and not numbered_parts:
            raise ContractError(f'checksum has no data representation: {checksum.path}')

        if whole is not None:
            source_entries = [whole]
            representation = 'whole'
        else:
            ordered = sorted(numbered_parts, key=lambda item: (item[0], item[1].path))
            numbers = [number for number, _entry in ordered]
            if len(numbers) != len(set(numbers)):
                raise ContractError(f'duplicate split-part number: {whole_path}')
            expected = list(range(len(numbers)))
            if numbers != expected:
                raise ContractError(
                    f'noncontiguous split parts for {whole_path}: expected {expected}, got {numbers}'
                )
            source_entries = [entry for _number, entry in ordered]
            representation = 'split'

        dataset, sample_relative_path = _sample_coordinates(whole_path)
        samples.append(
            {
                'dataset': dataset,
                'sample_relative_path': sample_relative_path,
                'whole_path': whole_path,
                'checksum_path': checksum.path,
                'representation': representation,
                'source_entries': source_entries,
                'byte_size': sum(entry.size for entry in source_entries),
            }
        )
    return samples


def _read_all(open_object: Callable[[str], BinaryIO], path: str) -> bytes:
    chunks: list[bytes] = []
    with open_object(path) as stream:
        while chunk := stream.read(READ_CHUNK_SIZE):
            chunks.append(chunk)
    return b''.join(chunks)


def _expected_sha256(open_object: Callable[[str], BinaryIO], path: str) -> str:
    text = _read_all(open_object, path).decode('utf-8').strip()
    if not text:
        raise ContractError(f'empty checksum file: {path}')
    checksum = text.split()[0].lower()
    if not re.fullmatch(r'[0-9a-f]{64}', checksum):
        raise ContractError(f'invalid SHA-256 record: {path}')
    return checksum


def _stream_sha256(
    open_object: Callable[[str], BinaryIO], entries: list[TreeEntry]
) -> str:
    digest = hashlib.sha256()
    for entry in entries:
        read_size = 0
        with open_object(entry.path) as stream:
            while chunk := stream.read(READ_CHUNK_SIZE):
                read_size += len(chunk)
                digest.update(chunk)
        if read_size != entry.size:
            raise ContractError(
                f'object size mismatch for {entry.path}: expected {entry.size}, got {read_size}'
            )
    return digest.hexdigest()


def build_manifest(
    revision: str,
    entries: list[TreeEntry],
    open_object: Callable[[str], BinaryIO],
    verify_bytes: bool = True,
) -> dict:
    revision = require_full_revision(revision)
    relevant = _prepared_entries(entries)
    samples = classify_samples(relevant)
    records: list[dict] = []
    for sample in samples:
        expected = _expected_sha256(open_object, sample['checksum_path'])
        source_entries = sample.pop('source_entries')
        if verify_bytes:
            actual = _stream_sha256(open_object, source_entries)
            if actual != expected:
                raise ContractError(
                    f"SHA-256 mismatch for {sample['whole_path']}: expected {expected}, got {actual}"
                )
        source_objects = [
            {
                'relative_path': entry.path,
                'byte_size': entry.size,
                'git_blob_sha': entry.blob_sha,
                'url': f'{REPOSITORY_URL}/raw/{revision}/{entry.path}',
            }
            for entry in source_entries
        ]
        sample_reference = sample['whole_path'].rsplit('/', 1)[0]
        records.append(
            {
                **sample,
                'whole_sha256': expected,
                'source_objects': source_objects,
                'source_reference': f'{REPOSITORY_URL}/tree/{revision}/{sample_reference}',
                'license': {'status': 'unknown/not-recorded', 'value': None},
                'preparation': {
                    'status': 'not-recorded-per-sample',
                    'method_reference': f'{REPOSITORY_URL}/tree/{revision}/scripts',
                },
            }
        )

    split_count = sum(record['representation'] == 'split' for record in records)
    dataset_identities: dict[str, str] = {}
    for dataset in sorted({record['dataset'] for record in records}):
        dataset_prefixes = {
            f'prepared/{record["sample_relative_path"]}'
            for record in records
            if record['dataset'] == dataset
        }
        dataset_entries = [
            entry
            for entry in relevant
            if any(
                entry.path == prefix
                or entry.path == f'{prefix}.sha256'
                or entry.path.startswith(f'{prefix}.part')
                for prefix in dataset_prefixes
            )
        ]
        dataset_identities[dataset] = _tree_identity(dataset_entries)

    return {
        'schema_version': '1.0.0',
        'dataset_repository': REPOSITORY_URL,
        'dataset_revision': revision,
        'manifest_generator': f'{REPOSITORY_URL}/blob/{revision}/scripts/prepared_data_manifest.py',
        'source_manifest_identity': {
            'algorithm': 'sha256',
            'scope': 'prepared-tree-path-size-git-blob-sha',
            'value': _tree_identity(relevant),
            'dataset_values': dataset_identities,
        },
        'license_caveat': 'Prepared sample licenses are unknown/not-recorded in this repository.',
        'totals': {
            'checksum_records': len(records),
            'whole_samples': len(records) - split_count,
            'split_samples': split_count,
        },
        'samples': records,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True, help='Full immutable dataset commit SHA')
    parser.add_argument(
        '--repository',
        type=Path,
        help='Read objects from this local Git repository instead of GitHub',
    )
    parser.add_argument('--output', type=Path, help='Write manifest JSON to this path')
    parser.add_argument(
        '--no-verify-bytes',
        action='store_true',
        help='Validate structure and checksums but do not stream-hash data objects',
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    revision = require_full_revision(args.revision)
    source = (
        LocalGitSource(args.repository, revision)
        if args.repository
        else GitHubSource(revision)
    )
    manifest = build_manifest(
        revision,
        source.entries(),
        source.open,
        verify_bytes=not args.no_verify_bytes,
    )
    output = json.dumps(manifest, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding='utf-8')
    else:
        sys.stdout.write(output)
    totals = manifest['totals']
    print(
        'Validated prepared data at '
        f'{revision}: {totals["checksum_records"]} checksums, '
        f'{totals["whole_samples"]} whole, {totals["split_samples"]} split.',
        file=sys.stderr,
    )
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ContractError, OSError, subprocess.SubprocessError, urllib.error.URLError) as exc:
        print(f'Prepared-data validation failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
