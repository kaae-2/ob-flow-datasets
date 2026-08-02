from __future__ import annotations

import csv
import subprocess
import unittest
from collections import Counter
from pathlib import Path


DATASET_DIR = (
    Path(__file__).parents[1]
    / 'prepared'
    / 'cytof'
    / 'BodenmillerXL'
    / 'PBMC_cytof'
)


class BodenmillerPreparationTests(unittest.TestCase):
    def test_surface_negative_population_has_canonical_label(self) -> None:
        archives = sorted(DATASET_DIR.glob('*.csv.zst'))
        label_counts: Counter[str] = Counter()
        row_count = 0

        self.assertEqual(len(archives), 16)
        for archive in archives:
            process = subprocess.Popen(
                ['zstd', '--decompress', '--stdout', str(archive)],
                stdout=subprocess.PIPE,
                text=True,
            )
            assert process.stdout is not None
            with process.stdout:
                rows = csv.DictReader(process.stdout)
                self.assertEqual(rows.fieldnames[-1], 'label')
                for row in rows:
                    row_count += 1
                    label_counts[row['label']] += 1
            self.assertEqual(process.wait(), 0)

        self.assertEqual(row_count, 172_791)
        self.assertEqual(label_counts['ungated'], 0)
        self.assertEqual(label_counts['surface negative cells'], 3_901)
        self.assertNotIn('surface-', label_counts)


if __name__ == '__main__':
    unittest.main()
