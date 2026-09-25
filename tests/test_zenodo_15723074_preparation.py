import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "prepare-zenodo-15723074.py"
SPEC = importlib.util.spec_from_file_location("prepare_zenodo_15723074", MODULE_PATH)
preparation = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(preparation)


class Zenodo15723074PreparationTests(unittest.TestCase):
    def test_preparation_selects_and_scales_publication_markers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "donor1.csv"
            output = root / "donor1_annotated.csv"
            source.write_text(
                "extra,CD14,CD19,CD3,CD56,CD45RA,CD8,CD4,CCR7,cell_type\n"
                "99,10000,1000,3000,2000,4000,3000,5000,6000,B cells\n",
                encoding="utf-8",
            )

            report = preparation.prepare_file(source, output)

            with output.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(
                list(rows[0]),
                [*preparation.PUBLICATION_COFACTORS, preparation.LABEL_COLUMN],
            )
            self.assertEqual(
                [
                    float(rows[0][marker])
                    for marker in preparation.PUBLICATION_COFACTORS
                ],
                [150.0] * len(preparation.PUBLICATION_COFACTORS),
            )
            self.assertEqual(rows[0][preparation.LABEL_COLUMN], "B cells")
            self.assertEqual(report["markers"], list(preparation.PUBLICATION_COFACTORS))
            self.assertEqual(report["reference_cofactor"], 150.0)
            self.assertEqual(
                report["source_cofactors"], preparation.PUBLICATION_COFACTORS
            )


if __name__ == "__main__":
    unittest.main()
