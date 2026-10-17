import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[2] / "tools"))
from source_vertex_colors import color_binding, load_color_binding, validate_rgba8


class SourceColorTests(unittest.TestCase):
    def test_source_index_binding_and_changed_obj_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            obj = Path(folder) / "mesh.obj"
            obj.write_text("v -1 2 3\nv 4 5 6\n")
            colors = [[255, 0, 12, 128], [0, 255, 70, 255]]
            bound = color_binding(obj, [[1, 2, 3], [-4, 5, 6]], colors)
            sidecar = obj.with_suffix(".colors.json")
            sidecar.write_text(json.dumps(bound))
            self.assertEqual(load_color_binding(sidecar, obj, 2), colors)
            obj.write_text("v -1 2 4\nv 4 5 6\n")
            with self.assertRaisesRegex(ValueError, "does not match"):
                load_color_binding(sidecar, obj, 2)

    def test_wrong_source_order_and_unorm_values_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            obj = Path(folder) / "mesh.obj"
            obj.write_text("v -1 2 3\n")
            with self.assertRaisesRegex(ValueError, "source-index order"):
                color_binding(obj, [[2, 2, 3]], [[255, 0, 0, 255]])
        for colors in ([[256, 0, 0, 0]], [[True, 0, 0, 0]], [[0.5, 0, 0, 0]], []):
            with self.assertRaises(ValueError):
                validate_rgba8(colors, 1)

    def test_normalized_float_stream_is_not_quantized_or_divided_again(self):
        with tempfile.TemporaryDirectory() as folder:
            obj = Path(folder) / "mesh.obj"
            obj.write_text("v -1 2 3\n")
            colors = [[0.5, 0.123456789, 1.0, 0.0]]
            bound = color_binding(obj, [[1, 2, 3]], colors)
            self.assertNotIn("rgba8", bound)
            self.assertEqual(bound["rgbaLinear"], colors)
            sidecar = obj.with_suffix(".colors.json")
            sidecar.write_text(json.dumps(bound))
            self.assertEqual(load_color_binding(sidecar, obj, 1), colors)
            bound["rgba8"] = [[255, 0, 0, 255]]
            sidecar.write_text(json.dumps(bound))
            with self.assertRaisesRegex(ValueError, "one channel encoding"):
                load_color_binding(sidecar, obj, 1)


if __name__ == "__main__":
    unittest.main()
