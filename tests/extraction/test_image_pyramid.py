import json
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from tile_image import build


class ImagePyramidTests(unittest.TestCase):
    def test_native_resolution_alpha_and_shared_gutters(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            source=Image.new("RGBA", (128,64))
            source.putdata([(x, y, 40, 100 if x>=64 else 255) for y in range(64) for x in range(128)])
            source.save(root/"source.png")
            result=build(root/"source.png", root/"tiles", 64,32,64,2)
            self.assertEqual([(l["width"],l["height"]) for l in result["tiles"]["levels"]],[(64,32),(128,64)])
            with Image.open(root/"tiles/1/0/0.webp") as left, Image.open(root/"tiles/1/1/0.webp") as right:
                self.assertEqual(left.size,(68,68))
                self.assertEqual(right.getpixel((2,12)),source.getpixel((64,10)))
                self.assertEqual(left.getpixel((66,12)),right.getpixel((2,12)))
                self.assertEqual(right.getpixel((1,12)),left.getpixel((65,12)))
                self.assertEqual(left.getpixel((0,0)),source.getpixel((0,0)))
            self.assertTrue((root/"tiles/overview.webp").is_file())

    def test_aspect_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            Image.new("RGB", (128,64)).save(root/"source.png")
            with self.assertRaisesRegex(ValueError,"aspect"):
                build(root/"source.png",root/"tiles",64,64)

    def test_base_cannot_invent_resolution(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            Image.new("RGB", (128,64)).save(root/"source.png")
            with self.assertRaisesRegex(ValueError,"enlarge"):
                build(root/"source.png",root/"tiles",256,128)
