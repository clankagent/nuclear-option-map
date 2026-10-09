import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from unity_normal_map import decode_dxt5nm_rg


class Dxt5nmTests(unittest.TestCase):
    def test_flat_normal_decodes_to_positive_z(self):
        self.assertEqual(decode_dxt5nm_rg(0.5, 0.5), (0.5, 0.5, 1.0))

    def test_alpha_and_green_are_x_and_y(self):
        self.assertEqual(decode_dxt5nm_rg(1.0, 0.5), (1.0, 0.5, 0.5))
        self.assertEqual(decode_dxt5nm_rg(0.5, 1.0), (0.5, 1.0, 0.5))

    def test_compression_noise_is_clamped_and_output_is_bounded(self):
        for channels in ((-0.5, 0.5), (1.5, 1.5), (0.0, 0.0), (1.0, 1.0)):
            result = decode_dxt5nm_rg(*channels)
            self.assertTrue(all(0.0 <= component <= 1.0 for component in result))


if __name__ == "__main__":
    unittest.main()
