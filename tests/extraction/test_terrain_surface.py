import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from terrain_detail import overlay_channel, decode_normal, texture_color_space, build_surface


class TerrainSurfaceTests(unittest.TestCase):
    def test_overlay_neutral_detail_preserves_macro_at_all_brightnesses(self):
        for macro in (0, .02, .25, .5, .75, .98, 1):
            self.assertAlmostEqual(overlay_channel(macro, .5), macro)

    def test_overlay_respects_dark_and_bright_geography(self):
        self.assertEqual(overlay_channel(0, 1), 0)
        self.assertEqual(overlay_channel(1, 0), 1)
        self.assertAlmostEqual(overlay_channel(.25, .8), .4)
        self.assertAlmostEqual(overlay_channel(.75, .2), .6)

    def test_normal_decode_uses_red_times_alpha_not_rgb_blue(self):
        self.assertEqual(decode_normal(1, .5, .5), (0, 0, 1))
        self.assertEqual(decode_normal(.5, .5, 1), (0, 0, 1))
        self.assertEqual(decode_normal(1, .5, 1), (1, 0, 0))
        self.assertEqual(decode_normal(1, 1, 1), (1, 1, 0))

    def test_serialized_color_space_distinguishes_linear_detail_and_srgb_masks(self):
        self.assertEqual(texture_color_space({'colorSpace': 0}), 'Non-Color')
        self.assertEqual(texture_color_space({'colorSpace': 1}), 'sRGB')
        with self.assertRaises(ValueError): texture_color_space({'colorSpace': 7})

    def test_incomplete_source_returns_original_macro_without_constructing_nodes(self):
        class NoNodes:
            def new(self, kind): raise AssertionError('Incomplete assets must not produce a partial graph')
        macro = object()
        color, normal = build_surface(NoNodes(), None, {'textures': []}, None, macro, None)
        self.assertIs(color, macro)
        self.assertIsNone(normal)

if __name__ == '__main__': unittest.main()
