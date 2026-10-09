"""Build a spatial raster pyramid from a local map image without enlarging it.

uv run python tools/tile_image.py INPUT OUTPUT --base-width 512 --base-height 512
"""
import argparse
import json
import math
from pathlib import Path
from PIL import Image


def build(source, output, base_width=512, base_height=512, tile_size=512, gutter=2):
    if not all(isinstance(v, int) and v > 0 for v in (base_width, base_height, tile_size)):
        raise ValueError("Base and tile dimensions must be positive integers")
    if tile_size > 1024 or gutter < 0 or gutter > 8:
        raise ValueError("Tile size must be at most 1024; gutter must be 0..8")
    output.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image.load()
        if base_width > image.width or base_height > image.height:
            raise ValueError("Base dimensions must not enlarge the source")
        if abs(image.width / image.height - base_width / base_height) > .001:
            raise ValueError("Base dimensions must preserve the source aspect ratio")
        max_level = max(0, math.floor(math.log2(min(image.width/base_width, image.height/base_height))))
        mode = "RGBA" if image.mode in ("RGBA", "LA") or "transparency" in image.info else "RGB"
        overview = image.resize((base_width, base_height), Image.Resampling.LANCZOS).convert(mode)
        overview.save(output / "overview.webp", lossless=True)
        levels = []
        for level in range(max_level+1):
            width, height = base_width * 2**level, base_height * 2**level
            raster = image.resize((width, height), Image.Resampling.LANCZOS).convert(mode)
            cols, rows = math.ceil(width/tile_size), math.ceil(height/tile_size)
            for x in range(cols):
                directory = output / str(level) / str(x)
                directory.mkdir(parents=True, exist_ok=True)
                for y in range(rows):
                    x0, y0 = x*tile_size, y*tile_size
                    w, h = min(tile_size, width-x0), min(tile_size, height-y0)
                    # Clamp the duplicated edge gutters to the image bounds.
                    patch = raster.crop((max(0,x0-gutter), max(0,y0-gutter), min(width,x0+w+gutter), min(height,y0+h+gutter)))
                    tile = Image.new(mode, (w+2*gutter, h+2*gutter))
                    left, top = min(gutter,x0), min(gutter,y0)
                    tile.paste(patch,(gutter-left,gutter-top))
                    if x0 < gutter: tile.paste(tile.crop((gutter,0,gutter+1,tile.height)).resize((gutter,tile.height)),(0,0))
                    if y0 < gutter: tile.paste(tile.crop((0,gutter,tile.width,gutter+1)).resize((tile.width,gutter)),(0,0))
                    if x0+w+gutter > width: tile.paste(tile.crop((gutter+w-1,0,gutter+w,tile.height)).resize((gutter,tile.height)),(gutter+w,0))
                    if y0+h+gutter > height: tile.paste(tile.crop((0,gutter+h-1,tile.width,gutter+h)).resize((tile.width,gutter)),(0,gutter+h))
                    tile.save(directory / f"{y}.webp", lossless=True)
                    tile.close()
                    patch.close()
            levels.append({"level":level,"width":width,"height":height,"columns":cols,"rows":rows,"urlTemplate":"{z}/{x}/{y}.webp"})
            raster.close()
            print(f"Level {level}: {width}x{height}, {cols*rows} tiles", flush=True)
        overview.close()
    result = {"schemaVersion":1,"image":"overview.webp","tiles":{"tileSize":tile_size,"gutter":gutter,"levels":levels}}
    (output / "pyramid.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    return result


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source",type=Path)
    parser.add_argument("output",type=Path)
    parser.add_argument("--base-width",type=int,default=512)
    parser.add_argument("--base-height",type=int,default=512)
    args=parser.parse_args()
    build(args.source,args.output,args.base_width,args.base_height)
