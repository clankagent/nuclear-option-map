"""Quality-probe output bounds; no Blender or GPU is initialized."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
from render_quality_probe import probe_dimensions, probe_ortho_scale


class QualityProbeTests(unittest.TestCase):
    def test_screenshot_footprint_preserves_aspect_at_higher_resolution(self):
        self.assertEqual(probe_dimensions(6700, 3500, 4096), (4096, 2140))
        self.assertEqual(probe_ortho_scale(6700, 4096, 2140), 6700)

    def test_portrait_camera_preserves_horizontal_world_width(self):
        width, height = probe_dimensions(1000, 2000, 1024)
        self.assertEqual((width, height), (1024, 2048))
        self.assertEqual(probe_ortho_scale(1000, width, height), 2000)

    def test_unbounded_and_nonfinite_dimensions_are_rejected(self):
        for width, depth, pixels in ((6700, 3500, 8192), (6700, 3500, 255),
                                     (1000, 10000, 4096), (0, 3500, 4096),
                                     (6700, float("nan"), 4096)):
            with self.subTest(width=width, depth=depth, pixels=pixels), self.assertRaises(ValueError):
                probe_dimensions(width, depth, pixels)
