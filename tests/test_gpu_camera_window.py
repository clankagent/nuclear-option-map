import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from render_cropped_gpu_trial import camera_window


class CameraWindowTests(unittest.TestCase):
    manifest = dict(centerX=10570, centerZ=8150, footprintMeters=419.84)

    def test_original_and_nested_windows(self):
        self.assertEqual(camera_window(self.manifest), (10570, 8150, 419.84))
        self.assertEqual(camera_window(self.manifest, 10570, 8150, 360), (10570, 8150, 360))
        self.assertEqual(camera_window(self.manifest, 10690, 8270, 120), (10690, 8270, 120))

    def test_cannot_expose_geometry_outside_prepared_view(self):
        for values in ((10570, 8150, 420), (10750, 8150, 120),
                       (10570, 7960, 120), (10570, None, 360),
                       (math.nan, 8150, 360), (10570, 8150, 0)):
            with self.assertRaises(ValueError):
                camera_window(self.manifest, *values)
