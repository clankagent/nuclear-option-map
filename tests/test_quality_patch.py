"""Precomputed sparse packaging; no Blender, game assets, or renderer."""
import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
from prepare_quality_patch import prepare


class QualityPatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "raw.png"
        self.proof = self.root / "proof.json"
        self.manifest = self.root / "pyramid.json"
        image = Image.new("RGB", (600, 600))
        image.putdata([(x*255//600, y*255//600, 80) for y in range(600) for x in range(600)])
        image.save(self.source)
        self.record = dict(width=600, height=600, centerX=0, centerZ=0, worldWidth=300, worldDepth=300,
                           metersPerPixel=.5, northUpFlip="Y", rawDenoised=False, seconds=1)
        self.proof.write_text(json.dumps(self.record))
        self.base = {"schemaVersion": 1, "image": "overview.webp?v=old", "tiles": {"tileSize": 64, "gutter": 2, "levels": [
            {"level": z, "width": 64*2**z, "height": 64*2**z, "columns": 2**z, "rows": 2**z,
             "urlTemplate": f"{z}/{{x}}/{{y}}.webp?v=old"} for z in range(2)]}}
        self.manifest.write_text(json.dumps(self.base))

    def run_patch(self):
        return prepare(self.source, self.proof, self.manifest, "https://test/maps/base/pyramid.json", self.root/"candidate", 512, 512)

    def test_alignment_north_up_lossless_gutters_and_existing_addresses(self):
        candidate, receipt = self.run_patch()
        self.assertEqual(candidate["tiles"]["levels"][2]["coverage"], [{"x": 1, "y": 1, "columns": 2, "rows": 2}])
        self.assertEqual(receipt["tileCount"], 4)
        self.assertEqual(candidate["tiles"]["levels"][1]["urlTemplate"], "https://test/maps/base/1/{x}/{y}.webp?v=old")
        self.assertEqual(candidate["image"], "https://test/maps/base/overview.webp?v=old")
        self.assertEqual(json.loads(self.manifest.read_text()), self.base)
        self.assertFalse((self.root/"candidate"/"0").exists())
        with Image.open(self.root/"candidate/2/1/1.webp") as tile, Image.open(self.root/"candidate/2/2/1.webp") as neighbor:
            self.assertEqual(tile.size, (68, 68))
            self.assertLess(tile.getpixel((2, 2))[1], tile.getpixel((2, 65))[1])
            # First interior pixel is map x129m, source x46px, not a tile-local origin.
            self.assertAlmostEqual(tile.getpixel((2, 2))[0], 46*255/600, delta=1)
            self.assertEqual(tile.crop((64, 0, 68, 68)).tobytes(), neighbor.crop((0, 0, 4, 68)).tobytes())
        self.assertFalse((self.root/"candidate/2/0").exists())

    def test_excludes_tiles_without_full_gutter_and_filter_support(self):
        self.record.update(worldWidth=256, worldDepth=256, metersPerPixel=256/600)
        self.proof.write_text(json.dumps(self.record))
        # Source ends exactly on candidate tile edges: neither edge tile has its gutter.
        with self.assertRaisesRegex(ValueError, "fully covers no"):
            self.run_patch()
        self.assertFalse((self.root/"candidate").exists())

    def test_rejects_insufficient_resolution_or_wrong_orientation(self):
        self.record.update(worldWidth=1800, worldDepth=1800, metersPerPixel=3)
        self.proof.write_text(json.dumps(self.record))
        with self.assertRaisesRegex(ValueError, "insufficient native"):
            self.run_patch()
        self.record["northUpFlip"] = "X"
        self.proof.write_text(json.dumps(self.record))
        with self.assertRaisesRegex(ValueError, "north-up"):
            self.run_patch()

    def test_rejects_dimension_mismatch_and_unfinished_proof(self):
        self.record["width"] = 601
        self.proof.write_text(json.dumps(self.record))
        with self.assertRaisesRegex(ValueError, "dimensions"):
            self.run_patch()
        del self.record["seconds"]
        self.proof.write_text(json.dumps(self.record))
        with self.assertRaisesRegex(ValueError, "completed"):
            self.run_patch()

    def test_rejects_contradictory_optional_camera_evidence(self):
        for field, value, message in [("cameraRotation", [0, .2, 0], "rotation"),
                                      ("pixelAspect", [1, 2], "aspect"),
                                      ("cameraLocation", [10, 0, 8000], "location"),
                                      ("cameraLocation", [0, 10, 8000], "location"),
                                      ("cameraScale", 150, "scale")]:
            with self.subTest(field=field, value=value):
                record = {**self.record, field: value}
                self.proof.write_text(json.dumps(record))
                with self.assertRaisesRegex(ValueError, message):
                    self.run_patch()
        self.record.update(cameraRotation=[0, 0, 0], pixelAspect=[1, 1], cameraLocation=[0, 0, 8000], cameraScale=300)
        self.proof.write_text(json.dumps(self.record))
        self.assertEqual(self.run_patch()[1]["tileCount"], 4)

    def test_binds_optional_file_size_and_sha256_to_source(self):
        size = self.source.stat().st_size
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.record["outputBytes"] = size+1
        self.proof.write_text(json.dumps(self.record))
        with self.assertRaisesRegex(ValueError, "byte count"):
            self.run_patch()
        self.record.update(outputBytes=size, outputSha256="0"*64)
        self.proof.write_text(json.dumps(self.record))
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.run_patch()
        self.record["outputSha256"] = digest
        self.proof.write_text(json.dumps(self.record))
        self.assertEqual(self.run_patch()[1]["tileCount"], 4)


if __name__ == "__main__":
    unittest.main()
