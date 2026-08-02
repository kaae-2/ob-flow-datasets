from __future__ import annotations

import hashlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / 'scripts' / 'prepared_data_manifest.py'
SPEC = importlib.util.spec_from_file_location('prepared_data_manifest', MODULE_PATH)
manifest_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = manifest_module
SPEC.loader.exec_module(manifest_module)

REVISION = 'a' * 40
SAMPLE = 'prepared/cytof/FR-FCM-Z3YR/StimBlood_cytof/sample.csv.zst'


def entry(path: str, content: bytes) -> manifest_module.TreeEntry:
    return manifest_module.TreeEntry(path, len(content), hashlib.sha1(content).hexdigest())


def source(objects: dict[str, bytes]):
    return lambda path: io.BytesIO(objects[path])


def fixture(parts: list[tuple[str, bytes]], include_whole: bool = False):
    assembled = b''.join(content for _name, content in parts)
    objects = {
        f'{SAMPLE}.sha256': f'{hashlib.sha256(assembled).hexdigest()}  sample.csv.zst\n'.encode()
    }
    objects.update(parts)
    if include_whole:
        objects[SAMPLE] = assembled
    entries = [entry(path, content) for path, content in objects.items()]
    return objects, entries


class PreparedDataManifestTests(unittest.TestCase):
    def test_whole_file_is_manifested_and_verified(self) -> None:
        objects, entries = fixture([], include_whole=True)

        manifest = manifest_module.build_manifest(
            REVISION, entries, source(objects)
        )

        self.assertEqual(manifest['totals']['checksum_records'], 1)
        self.assertEqual(manifest['samples'][0]['representation'], 'whole')
        self.assertEqual(
            manifest['samples'][0]['source_objects'][0]['relative_path'], SAMPLE
        )

    def test_ordered_split_file_is_manifested_and_verified(self) -> None:
        parts = [(f'{SAMPLE}.part0000', b'abc'), (f'{SAMPLE}.part0001', b'def')]
        objects, entries = fixture(parts)

        manifest = manifest_module.build_manifest(
            REVISION, list(reversed(entries)), source(objects)
        )

        sample = manifest['samples'][0]
        self.assertEqual(sample['representation'], 'split')
        self.assertEqual(
            [item['relative_path'] for item in sample['source_objects']],
            [name for name, _content in parts],
        )
        self.assertEqual(sample['byte_size'], 6)

    def test_missing_split_part_is_rejected(self) -> None:
        objects, entries = fixture(
            [(f'{SAMPLE}.part0000', b'abc'), (f'{SAMPLE}.part0002', b'def')]
        )

        with self.assertRaisesRegex(manifest_module.ContractError, 'noncontiguous'):
            manifest_module.build_manifest(REVISION, entries, source(objects))

    def test_corrupt_assembled_checksum_is_rejected(self) -> None:
        objects, entries = fixture([(f'{SAMPLE}.part0000', b'abc')])
        objects[f'{SAMPLE}.part0000'] = b'xyz'

        with self.assertRaisesRegex(manifest_module.ContractError, 'SHA-256 mismatch'):
            manifest_module.build_manifest(REVISION, entries, source(objects))

    def test_whole_and_parts_are_rejected_as_ambiguous(self) -> None:
        objects, entries = fixture([(f'{SAMPLE}.part0000', b'abc')], include_whole=True)

        with self.assertRaisesRegex(manifest_module.ContractError, 'ambiguous'):
            manifest_module.build_manifest(REVISION, entries, source(objects))

    def test_orphan_part_is_rejected(self) -> None:
        objects = {f'{SAMPLE}.part0000': b'abc'}
        entries = [entry(path, content) for path, content in objects.items()]

        with self.assertRaisesRegex(manifest_module.ContractError, 'orphan'):
            manifest_module.build_manifest(REVISION, entries, source(objects))

    def test_mutable_and_short_revisions_are_rejected(self) -> None:
        for revision in ('main', 'f2f657a', 'A' * 40):
            with self.subTest(revision=revision):
                with self.assertRaisesRegex(manifest_module.ContractError, 'full 40-character'):
                    manifest_module.require_full_revision(revision)

    def test_actual_stimblood_naming_is_classified(self) -> None:
        sample = (
            'prepared/cytof/FR-FCM-Z3YR/StimBlood_cytof/'
            '181017_reference_tube_day1_01.csv.zst'
        )
        content = b'abcdef'
        objects = {
            f'{sample}.part0000': content[:3],
            f'{sample}.part0001': content[3:],
            f'{sample}.sha256': f'{hashlib.sha256(content).hexdigest()}  file\n'.encode(),
        }
        entries = [entry(path, value) for path, value in objects.items()]

        manifest = manifest_module.build_manifest(
            REVISION, entries, source(objects)
        )

        self.assertEqual(manifest['samples'][0]['dataset'], 'FR-FCM-Z3YR')
        self.assertEqual(manifest['samples'][0]['representation'], 'split')


if __name__ == '__main__':
    unittest.main()
