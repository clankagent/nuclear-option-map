"""Deterministic 2x terrain interpolation with bounded edge sharpening.

No generative inference or terrain geometry changes. Process overlapping tiles to
bound temporary memory; sharpening is limited to eight RGB code values. The result
is a Blender source texture, not a browser detailed-map delivery format.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from PIL import Image, ImageFilter

Image.MAX_IMAGE_PIXELS = None


def prepare_macro_tiles(path):
    """Three-by-three global-UV tiles with four-pixel cubic sampling gutters."""
    path = Path(path)
    with Image.open(path) as image:
        width, height = image.size
        if max(width, height) <= 8192:
            return None
        cx, cy = math.ceil(width / 3), math.ceil(height / 3)
        tiles = []
        for y in range(3):
            for x in range(3):
                box = [max(0, x * cx - 4), max(0, y * cy - 4),
                       min(width, (x + 1) * cx + 4), min(height, (y + 1) * cy + 4)]
                target = path.with_name(path.stem + f"-t{x}{y}.png")
                image.crop(box).save(target, compress_level=4)
                tiles.append({"x": x, "y": y, "box": box, "file": target.name})
        spec = {"size": [width, height], "xCuts": [cx, cx * 2],
                "yCuts": [cy, cy * 2], "gutter": 4, "tiles": tiles}
        path.with_suffix(".tiles.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
        return spec


def upscale(source, destination):
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as original:
        if "A" in original.getbands() and original.getchannel("A").getextrema() != (255, 255):
            raise ValueError("Terrain macro contains nonopaque alpha")
        width, height = original.size
        result = Image.new("RGB", (width * 2, height * 2))
        for y in range(0, height, 512):
            for x in range(0, width, 512):
                right, bottom = min(x + 512, width), min(y + 512, height)
                box = (max(0, x - 16), max(0, y - 16), min(width, right + 16), min(height, bottom + 16))
                tile = original.crop(box).convert("RGB")
                tile = tile.resize((tile.width * 2, tile.height * 2), Image.Resampling.LANCZOS)
                sharp = tile.filter(ImageFilter.UnsharpMask(radius=1.5, percent=90, threshold=3))
                data, sharpened = np.asarray(tile).astype(np.int16), np.asarray(sharp).astype(np.int16)
                bounded = Image.fromarray(np.clip(data + np.clip(sharpened - data, -8, 8), 0, 255).astype(np.uint8))
                inner = ((x - box[0]) * 2, (y - box[1]) * 2, (right - box[0]) * 2, (bottom - box[1]) * 2)
                result.paste(bounded.crop(inner), (x * 2, y * 2))
        result.save(destination, compress_level=4)
    return {"source": str(source.resolve()), "output": str(destination.resolve()),
            "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "sourcePixels": [width, height], "outputPixels": [width * 2, height * 2],
            "method": "2x Lanczos; overlapping tiles; unsharp radius1.5 amount90 threshold3; delta bounded +/-8/255",
            "limitation": "Interpolated and sharpened source; missing geographic detail is not recovered"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8-sig"))
    receipts = []
    for material in report["materials"]:
        if material.get("shaderName") == "Shader Graphs/TerrainShader":
            macro = next(t for t in material["textures"] if t.get("property") == "_macro_basecolor" and t.get("export"))
            target = Path(macro["export"]).parent.parent / "terrain-detail" / (macro["textureName"] + "-2x.png")
            receipts.append(upscale(macro["export"], target))
            receipts[-1]["macroTiles"] = prepare_macro_tiles(target)
            print(target, flush=True)
    target.with_name("macro-upscale-receipt.json").write_text(json.dumps(receipts, indent=2), encoding="utf-8")
