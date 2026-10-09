import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from map_assets import metadata, safe_id, resolve


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.tree = {"MapSize": {"x": 64000, "y": 48000}, "GridSizeX": 8,
                     "GridSizeY": 6, "OffsetX": -2, "OffsetY": 3}

    def test_serialized_meter_dimensions_and_signed_grid_offsets(self):
        item = metadata(self.tree, "Island", 21)
        self.assertEqual((item["widthMeters"], item["heightMeters"]), (64000, 48000))
        self.assertEqual(item["grid"], {"columns": 8, "rows": 6, "offsetX": -2, "offsetY": 3})

    def test_rejects_invalid_dimensions(self):
        for value in (0, -1, float("nan"), float("inf")):
            self.tree["MapSize"]["x"] = value
            with self.assertRaises(ValueError):
                metadata(self.tree, "Island", 21)

    def test_filename_is_local_and_nonempty(self):
        for name in ("../../secret", "C:\\Maps\\file", "", "海"):
            ident = safe_id(name, 12)
            self.assertEqual(Path(ident).name, ident)
            self.assertNotIn("..", ident)
            self.assertTrue(ident.endswith("-12"))
        self.assertNotEqual(safe_id("asset", 12), safe_id("asset", -12))

    def test_null_pointer_is_rejected_before_dereference(self):
        with self.assertRaises(ValueError):
            resolve(None, {"m_FileID": 0, "m_PathID": 0})

    def test_invalid_file_reference_is_rejected(self):
        for reference in ({"m_FileID": -1, "m_PathID": 4}, {"m_PathID": 4}):
            with self.assertRaises(ValueError):
                resolve(None, reference)


if __name__ == "__main__":
    unittest.main()
