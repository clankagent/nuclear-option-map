import importlib.util
from pathlib import Path
import unittest
from copy import deepcopy
import numpy as np

path = Path(__file__).resolve().parents[1] / 'tools/render_satellite_tiles.py'
spec = importlib.util.spec_from_file_location('linear_tiles', path)
tiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tiles)


class LinearCrop(unittest.TestCase):
    def test_symmetric_crop_preserves_row_order_and_channels(self):
        pixels = np.arange(8 * 8 * 4, dtype=np.float32).reshape((8, 8, 4))
        result = tiles.crop_linear_pixels(pixels, 4, {'renderEdge': 8, 'padding': 2})
        np.testing.assert_array_equal(result, pixels[2:6, 2:6])
        self.assertTrue(result.flags.c_contiguous)
        self.assertEqual(result.dtype, np.float32)
        np.testing.assert_array_equal(result[0, 0], pixels[2, 2])
        np.testing.assert_array_equal(result[-1, -1], pixels[5, 5])

    def test_dimension_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            tiles.crop_linear_pixels(np.zeros((8, 8, 4)), 5, {'renderEdge': 8, 'padding': 2})

    def test_water_cache_invalidates_for_pipeline_changes(self):
        arguments = [dict(samples=4, threads=2), 2272, 0, 1, 'surface-proof',
                     dict(linear=True, profile=dict(hdr=True)), ['AgX', 'None', 0, 1]]
        baseline = tiles.water_render_signature(*arguments)
        for index, replacement in ((0, dict(samples=16, threads=2)), (1, 1248),
                                   (2, 1), (3, 2), (4, 'changed-water'),
                                   (5, dict(linear=True, profile=dict(hdr=False))),
                                   (6, ['AgX', 'None', 1, 1])):
            changed = deepcopy(arguments)
            changed[index] = replacement
            self.assertNotEqual(baseline, tiles.water_render_signature(*changed))
        reordered = deepcopy(arguments)
        reordered[0] = dict(threads=2, samples=4)
        self.assertEqual(baseline, tiles.water_render_signature(*reordered))


if __name__ == '__main__':
    unittest.main()
