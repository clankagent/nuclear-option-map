"""Strict eligibility and source-byte reuse, without a Blender/GPU process."""
import copy
import importlib.util
import tempfile
import unittest
import sys
from unittest import mock
from types import SimpleNamespace as Obj
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("uniform_water", Path(__file__).parents[1] / "tools/uniform_water.py")
water = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(water)


def snapshot():
    return {"geometry": [{"mesh": True, "horizontalQuad": True, "convexQuad": True, "axisAligned": True,
                           "constantOpaqueMaterial": True, "bounds": [-10000, -10000, 10000, 10000], "height": -1}],
            "frame": [-5000, -5000, 5000, 5000], "volume": False, "uniformWorld": True,
            "orthoDown": True, "sceneTop": 1500, "shadowMargin": 2048,
            "lights": [{"type": "SUN", "constant": True, "direction": [.5, 0, -.86]}]}


class UniformWaterTests(unittest.TestCase):
    def test_snapshot_rejects_coastal_and_large_meshes_without_vertex_traversal(self):
        spec = importlib.util.spec_from_file_location("snapshot_renderer", Path(__file__).parents[1] / "tools/render_satellite_tiles.py")
        renderer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(renderer)
        class HugeMesh:
            def __len__(self):
                return 1000000
            def __iter__(self):
                raise AssertionError("Large terrain vertices were traversed")
        with mock.patch.dict(sys.modules, {"mathutils": Obj(Vector=lambda value: value)}):
            self.assertFalse(water.eligible(renderer._water_snapshot(None, [None, None], [], 2048, 1500)))
            terrain = Obj(type="MESH", data=Obj(vertices=HugeMesh()))
            self.assertFalse(water.eligible(renderer._water_snapshot(None, [terrain], [], 2048, 1500)))

    def test_only_proven_uniform_plane_is_eligible(self):
        self.assertTrue(water.eligible(snapshot()))

    def test_coast_geometry_and_uncertain_materials_always_render(self):
        base = snapshot()
        for key in ("horizontalQuad", "convexQuad", "axisAligned", "constantOpaqueMaterial"):
            candidate = copy.deepcopy(base)
            candidate["geometry"][0][key] = False
            self.assertFalse(water.eligible(candidate), key)
        candidate = copy.deepcopy(base)
        candidate["geometry"].append(copy.deepcopy(base["geometry"][0]))
        self.assertFalse(water.eligible(candidate))

    def test_volumes_nonuniform_world_and_camera_are_rejected(self):
        for key, value in (("volume", True), ("uniformWorld", False), ("orthoDown", False),
                           ("materialOverride", True), ("holdout", True)):
            candidate = snapshot()
            candidate[key] = value
            self.assertFalse(water.eligible(candidate), key)

    def test_local_light_low_sun_and_unbounded_shadows_are_rejected(self):
        for update in ({"type": "POINT"}, {"constant": False}, {"direction": [1, 0, -.01]}):
            candidate = snapshot()
            candidate["lights"][0].update(update)
            self.assertFalse(water.eligible(candidate))
        candidate = snapshot()
        candidate["shadowMargin"] = 100
        self.assertFalse(water.eligible(candidate))

    def test_plane_must_cover_whole_chunk_and_gutters(self):
        candidate = snapshot()
        candidate["geometry"][0]["bounds"][2] = 4999
        self.assertFalse(water.eligible(candidate))

    def test_copy_reuses_genuine_render_bytes_only_with_same_render_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            source, dest = Path(directory) / "genuine.png", Path(directory) / "tile.png"
            source.write_bytes(b"actual rendered chunk bytes")
            self.assertFalse(water.copy_verified_chunk(source, dest, snapshot()))
            self.assertFalse(water.copy_verified_chunk(source, dest, snapshot(), cached_signature="cpu/516/4", render_signature="cpu/2052/4"))
            self.assertFalse(dest.exists())
            self.assertTrue(water.copy_verified_chunk(source, dest, snapshot(), cached_signature="cpu/2052/4", render_signature="cpu/2052/4"))
            self.assertEqual(source.read_bytes(), dest.read_bytes())
            invalid = snapshot()
            invalid["geometry"][0]["constantOpaqueMaterial"] = False
            self.assertFalse(water.copy_verified_chunk(source, dest, invalid, cached_signature="cpu/2052/4", render_signature="cpu/2052/4"))


if __name__ == "__main__":
    unittest.main()
