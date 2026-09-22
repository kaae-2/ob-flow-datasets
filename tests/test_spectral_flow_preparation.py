"""Behavioral checks for event-aligned spectral-flow CSV preparation."""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from spectral_flow.core import export_csv, label_events, split_compressed
from spectral_flow.flowjo import prepare_z7ch_sample, sample_manifest


class SpectralPreparationTests(unittest.TestCase):
    def test_published_panels_have_portable_provenance_and_checked_objects(self):
        root = Path(__file__).parents[1] / "prepared/fcm"
        expected = {
            "C57BL6": (2113422, "NK1.1"),
            "BALBc": (1594429, "NKp46"),
        }
        for strain, (events, nk_marker) in expected.items():
            directory = root / f"FR-FCM-Z7CH-{strain}"
            with self.subTest(strain=strain):
                audit = json.loads((directory / "preparation-audit.json").read_text())
                self.assertEqual(audit["shortname"], f"MurineSplenocytes{strain}_fcm")
                self.assertEqual(audit["total_events"], events)
                self.assertEqual(audit["sample_count"], 10)
                self.assertEqual(audit["biological_replicates"], 5)
                self.assertEqual(len(audit["markers"]), 15)
                self.assertIn(nk_marker, audit["markers"])
                self.assertEqual(len({s["filename"] for s in audit["samples"]}), 10)
                self.assertNotIn("/home/", json.dumps(audit))
                for sample in audit["samples"]:
                    self.assertEqual(sample["rows"], sample["source_event_count"])
                    for obj in sample["source_objects"]:
                        relative = Path(obj["path"])
                        self.assertFalse(relative.is_absolute())
                        self.assertEqual(relative.parent.name, audit["shortname"])
                        with (directory / relative).open("rb") as stream:
                            self.assertEqual(
                                hashlib.file_digest(stream, "sha256").hexdigest(),
                                obj["sha256"],
                            )

    def test_split_objects_preserve_the_whole_object_and_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.csv.zst"
            payload = bytes(range(100))
            path.write_bytes(payload)
            objects = split_compressed(path, max_bytes=40)
            self.assertEqual(
                [Path(o["path"]).name for o in objects],
                [
                    "sample.csv.zst.part0000",
                    "sample.csv.zst.part0001",
                    "sample.csv.zst.part0002",
                ],
            )
            self.assertEqual(
                b"".join(Path(o["path"]).read_bytes() for o in objects), payload
            )
            self.assertFalse(path.exists())
            self.assertTrue(all(o["bytes"] <= 40 for o in objects))

    @unittest.skipUnless(
        (
            Path(__file__).parents[1] / "import/FR-FCM-Z7CH/Supplementary_Note_1.xlsx"
        ).exists(),
        "Local FlowRepository source downloads are required",
    )
    def test_real_workspace_exports_one_biological_sample_with_source_row_count(self):
        source = Path(__file__).parents[1] / "import/FR-FCM-Z7CH"
        required = [
            "OMIP-ICS-C57BL_6.wsp",
            "OMIP-ICS-BALB_c.wsp",
            "download-manifest.json",
        ]
        for strain, first in [("C57", 2), ("BALB", 7)]:
            for mouse in range(1, 6):
                position = first + mouse - 1
                for row, treatment in [("E", "BFA"), ("F", "Stim")]:
                    required.append(
                        f"{row}{position:02d} {strain}_M{mouse}_{treatment}_TS WLSM.fcs"
                    )
        if not all((source / name).is_file() for name in required):
            self.skipTest("Complete biological source download is required")
        entries = sample_manifest(source)
        self.assertEqual(len(entries), 20)
        self.assertEqual(len({e["replicate_id"] for e in entries}), 10)
        entry = next(
            e for e in entries if e["filename"] == "E02 C57_M1_BFA_TS WLSM.fcs"
        )
        self.assertEqual(entry["shortname"], "MurineSplenocytesC57BL6_fcm")
        self.assertEqual(
            {e["shortname"] for e in entries},
            {"MurineSplenocytesC57BL6_fcm", "MurineSplenocytesBALBc_fcm"},
        )
        with tempfile.TemporaryDirectory() as directory:
            report = prepare_z7ch_sample(
                source, entry, Path(directory) / "prepared", Path(directory) / "reports"
            )
            self.assertEqual(report["output"]["rows"], 214345)
            self.assertEqual(
                Path(report["output"]["csv"]).parent.name, entry["shortname"]
            )
            self.assertIn("NK1.1", report["output"]["markers"])
            self.assertNotIn("NKp46", report["output"]["markers"])
            self.assertEqual(len(report["output"]["markers"]), 15)
            self.assertEqual(
                sum(report["label_audit"]["label_counts"].values()), 214345
            )
            self.assertTrue(report["feature_values_equal_gating_input"])
            self.assertEqual(report["count_comparison"][0]["saved_count"], 117479)

    def test_overlapping_unmatched_and_disputed_events_remain_unlabeled(self):
        first = {
            "T": np.array([True, False, True, False, True]),
            "B": np.array([False, True, True, False, False]),
        }
        second = {
            "T": np.array([True, False, True, False, False]),
            "B": np.array([False, True, True, False, True]),
        }
        labels, audit = label_events([first, second])
        self.assertEqual(
            labels.tolist(), ["T", "B", "unlabeled", "unlabeled", "unlabeled"]
        )
        self.assertEqual(audit["configuration_disagreement"], 1)
        self.assertEqual(audit["overlap_in_any_configuration"], 1)
        self.assertEqual(audit["unmatched_in_all_configurations"], 1)

    def test_csv_roundtrip_preserves_negative_values_order_and_labels(self):
        values = np.array([[3.5, -17.125], [-0.125, 123456.78]], dtype=np.float32)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample_annotated.csv"
            report = export_csv(
                path, values, ["CD3", "NKp46"], np.array(["T", "unlabeled"])
            )
            frame = pd.read_csv(path)
            self.assertEqual(frame.columns.tolist(), ["CD3", "NKp46", "label"])
            np.testing.assert_array_equal(
                frame.iloc[:, :-1].to_numpy(dtype=np.float32), values
            )
            self.assertEqual(frame.label.tolist(), ["T", "unlabeled"])
            self.assertEqual(pd.read_csv(str(path) + ".zst").to_dict(), frame.to_dict())
            self.assertEqual(report["rows"], 2)
            with self.assertRaises(FileExistsError):
                export_csv(path, values, ["CD3", "NKp46"], np.array(["T", "unlabeled"]))


if __name__ == "__main__":
    unittest.main()
