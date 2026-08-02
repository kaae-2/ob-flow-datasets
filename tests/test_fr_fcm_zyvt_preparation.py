from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import pandas as pd


MODULE_PATH = Path(__file__).parents[1] / 'scripts' / 'prepare-flowrepository-gated.py'
DATASET_DIR = (
    Path(__file__).parents[1]
    / 'prepared'
    / 'fcm'
    / 'FR-FCM-ZYVT'
    / 'PediatricBALL_fcm'
)
SPEC = importlib.util.spec_from_file_location('prepare_flowrepository_gated', MODULE_PATH)
preparation = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = preparation
SPEC.loader.exec_module(preparation)


def _rectangle(name: str, parent: str | None, x0: float, x1: float):
    return preparation.KaluzaGate(
        name=name,
        parent=parent,
        kind='rectangle',
        x_measure='x',
        y_measure='y',
        x_scale='I',
        y_scale='I',
        points=[(x0, 0.0), (x1, 1.0)],
    )


class PediatricBallPreparationTests(unittest.TestCase):
    def test_only_exclusive_terminal_gate_memberships_become_targets(self) -> None:
        gate_df = pd.DataFrame(
            {
                'x': [0.5, 1.5, 2.5, 3.5],
                'y': [0.5, 0.5, 0.5, 0.5],
            }
        )
        gates = [
            _rectangle('singlets', None, 0.0, 4.0),
            _rectangle('CD19+', 'singlets', 0.0, 3.0),
            _rectangle('Blasts', 'CD19+', 0.0, 2.0),
            _rectangle('mature B-cells', 'CD19+', 1.0, 3.0),
        ]

        labels = preparation.label_kaluza_events(gate_df, gates)

        self.assertEqual(
            labels.tolist(),
            ['Blasts', 'unlabeled', 'mature B-cells', 'unlabeled'],
        )

    def test_syto41_is_gating_only_not_a_model_feature(self) -> None:
        cd19 = preparation.KaluzaMeasurement('FL5 INT', 'FL5', 'CD19')

        for marker in ('SY41', 'SYTO41'):
            with self.subTest(marker=marker):
                measurement = preparation.KaluzaMeasurement('FL9 INT', 'FL9', marker)
                self.assertFalse(preparation.keep_kaluza_marker(measurement))
        self.assertTrue(preparation.keep_kaluza_marker(cd19))

    def test_prepared_bln010_matches_exclusive_source_masks(self) -> None:
        prepared = pd.read_csv(DATASET_DIR / 'Bln010.30_annotated.csv.zst')

        self.assertEqual(
            prepared.columns.tolist(),
            ['CD58', 'CD10', 'CD34', 'CD19', 'CD38', 'CD20', 'CD45', 'label'],
        )
        self.assertEqual(
            prepared['label'].value_counts().to_dict(),
            {
                'unlabeled': 440_718,
                'Erythroblasts': 49_601,
                'mature B-cells': 9_205,
                'Blasts': 475,
                'plasmacells': 1,
            },
        )


if __name__ == '__main__':
    unittest.main()
