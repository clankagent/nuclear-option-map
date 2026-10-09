import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from terrain_selection import terrain_record


class SelectionTests(unittest.TestCase):
    def test_base_surfaces_selected(self):
        for name in ("terrain2_tile0", "terrain2_tile255", "terrain_naval_island18"):
            self.assertTrue(terrain_record({"name": name}))

    def test_separate_overlays_are_excluded(self):
        for name in ("terrain_naval_island1_surf", "terrain2_roads205", "terrain_naval_island5_blocks", "TreeRow1"):
            self.assertFalse(terrain_record({"name": name}))
