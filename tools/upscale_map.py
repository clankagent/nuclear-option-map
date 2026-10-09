"""Create deterministic 2x Lanczos previews from extracted map images.

This is an interpolation aid for zooming. It does not reconstruct missing terrain detail.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def load_maps(manifest_path, source_dir, selected_ids=None):
    root = source_dir.resolve(strict=True)
    data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    entries = data.get("maps") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ValueError("Map manifest must contain a maps array")
    known, maps = set(), []
    for item in entries:
        if not isinstance(item, dict):
            continue
        map_id, image_name = item.get("id"), item.get("image")
        if not isinstance(map_id, str) or not map_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in map_id):
            raise ValueError(f"Unsafe or missing map id: {map_id!r}")
        if map_id in known:
            raise ValueError(f"Duplicate map id in manifest: {map_id}")
        known.add(map_id)
        if not isinstance(image_name, str) or not image_name:
            raise ValueError(f"{map_id} has no local image name")
        input_path = (root / image_name).resolve(strict=True)
        if not input_path.is_relative_to(root) or not input_path.is_file():
            raise ValueError(f"{map_id} image must resolve to a local file under {root}")
        width_m, height_m = item.get("widthMeters"), item.get("heightMeters")
        if not isinstance(width_m, (int, float)) or not isinstance(height_m, (int, float)) or min(width_m, height_m) <= 0:
            raise ValueError(f"{map_id} must have positive widthMeters and heightMeters")
        maps.append({"id": map_id, "name": str(item.get("name", map_id)), "image": image_name,
                     "path": input_path, "widthMeters": float(width_m), "heightMeters": float(height_m)})
    if selected_ids:
        missing = [value for value in selected_ids if value not in known]
        if missing:
            raise ValueError(f"Unknown map id(s): {', '.join(missing)}")
        wanted = set(selected_ids)
        maps = [item for item in maps if item["id"] in wanted]
    if not maps:
        raise ValueError("No maps selected")
    return root, maps


def crop_box(size, requested):
    width, height = size
    if requested is not None:
        x0, y0, x1, y1 = requested
        if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0 or x1 > width or y1 > height:
            raise ValueError(f"Crop {requested} is outside source image {width}x{height}")
        return tuple(requested)
    side = min(512, width, height)
    x0, y0 = (width - side) // 2, (height - side) // 2
    return x0, y0, x0 + side, y0 + side


def comparison_image(maps, source_root, output_path, requested_crop=None):
    panels = []
    filters = (("Nearest", Image.Resampling.NEAREST),
               ("Bicubic", Image.Resampling.BICUBIC),
               ("Lanczos", Image.Resampling.LANCZOS))
    for item in maps:
        with Image.open(item["path"]) as source:
            box = crop_box(source.size, requested_crop)
            rgba = source.convert("RGBA")
            crop = rgba.crop(box)
            rgba.close()
            background = Image.new("RGBA", crop.size, "#111a1a")
            background.alpha_composite(crop)
            rgb = background.convert("RGB")
            crop.close()
            background.close()
            for label, method in filters:
                scaled = rgb.resize((rgb.width * 2, rgb.height * 2), method)
                panels.append((f"{item['name']} · {label}", scaled))
            rgb.close()

    columns, margin, label_height = 3, 18, 28
    cell_w = max(panel.width for _, panel in panels)
    cell_h = max(panel.height for _, panel in panels)
    rows = math.ceil(len(panels) / columns)
    sheet = Image.new("RGB", (margin + columns * (cell_w + margin),
                               margin + rows * (cell_h + label_height + margin)), "#121719")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except OSError:
        font = ImageFont.load_default()
    for index, (label, panel) in enumerate(panels):
        row, col = divmod(index, columns)
        x, y = margin + col * (cell_w + margin), margin + row * (cell_h + label_height + margin)
        draw.text((x, y), label, fill="#f2f4f1", font=font)
        sheet.paste(panel, (x, y + label_height))
        panel.close()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, format="PNG", optimize=False, compress_level=6)
    sheet.close()


def merge_variant_catalog(path, entries):
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(existing, dict) or existing.get("schemaVersion") != 1 or not isinstance(existing.get("maps"), dict):
            raise ValueError(f"Refusing to replace an incompatible variants catalog: {path}")
        catalog = existing
    else:
        catalog = {"schemaVersion": 1, "maps": {}}
    for map_id, variant in entries.items():
        current = catalog["maps"].get(map_id, [])
        if not isinstance(current, list):
            raise ValueError(f"Existing variants for {map_id} must be an array")
        catalog["maps"][map_id] = [row for row in current if not isinstance(row, dict) or row.get("id") != variant["id"]] + [variant]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog, indent=2), encoding="utf-8")


def generate(manifest_path, source_dir, output_dir, report_path, comparison_path,
             selected_ids=None, requested_crop=None, variants_path=None, scale=2):
    if scale != 2:
        raise ValueError("Only the requested 2x interpolation output is supported")
    source_root, maps = load_maps(manifest_path, source_dir, selected_ids)
    output_dir.mkdir(parents=True, exist_ok=True)
    source_paths = {item["path"] for item in maps}
    for item in maps:
        candidate = (output_dir / f"{item['id']}-clean2x.png").resolve()
        if candidate in source_paths:
            raise ValueError(f"Output would overwrite a selected source image: {candidate}")
    variants = {}
    report = {"method": "Pillow LANCZOS", "scale": scale,
              "interpretation": "Interpolated display variants; no new terrain detail is created.",
              "variants": []}
    for item in maps:
        output_path = output_dir / f"{item['id']}-clean2x.png"
        resolved_output = output_path.resolve()
        with Image.open(item["path"]) as source:
            source.load()
            source_size, source_mode = source.size, source.mode
            mode = "RGBA" if "A" in source.getbands() else "RGB"
            working = source.convert(mode)
            enlarged = working.resize((source.width * scale, source.height * scale), Image.Resampling.LANCZOS)
            working.close()
            enlarged.save(output_path, format="PNG", optimize=False, compress_level=6)
            enlarged.close()
        relative_output = resolved_output.relative_to(source_root) if resolved_output.is_relative_to(source_root) else None
        output_dims = [source_size[0] * scale, source_size[1] * scale]
        record = {
            "mapId": item["id"], "mapName": item["name"],
            "source": str(item["path"]), "sourceSha256": digest(item["path"]),
            "sourcePixels": list(source_size),
            "sourceMetersPerPixel": [item["widthMeters"] / source_size[0], item["heightMeters"] / source_size[1]],
            "output": str(resolved_output), "outputRelativeToSourceDir": str(relative_output) if relative_output else None,
            "outputSha256": digest(output_path), "outputBytes": output_path.stat().st_size,
            "outputPixels": output_dims,
            "displayMetersPerPixel": [item["widthMeters"] / output_dims[0], item["heightMeters"] / output_dims[1]],
            "sourceMode": source_mode, "outputMode": mode,
        }
        report["variants"].append(record)
        if relative_output is not None:
            variants[item["id"]] = {"id": "clean2x-lanczos", "label": "Smooth 2x (Lanczos)",
                                     "image": relative_output.as_posix(), "kind": "builtin",
                                     "widthPixels": output_dims[0], "heightPixels": output_dims[1]}
    comparison_image(maps, source_root, comparison_path, requested_crop)
    report["cropComparison"] = str(comparison_path.resolve())
    report["comparisonCrop"] = list(requested_crop) if requested_crop else "center square up to 512 px per map"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if variants_path is not None:
        if len(variants) != len(maps):
            raise ValueError("Variants catalog requires every output image to be under source-dir")
        merge_variant_catalog(variants_path, variants)
        report["variantsCatalog"] = str(variants_path.resolve())
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("demo/public/maps/manifest.json"))
    parser.add_argument("--source-dir", type=Path, default=Path("demo/public/maps"))
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="defaults to source-dir; place outputs inside source-dir to use a variants catalog")
    parser.add_argument("--report", type=Path, default=Path("work/extraction/upscale-report.json"))
    parser.add_argument("--comparison", type=Path, default=Path("work/extraction/upscale-crops.png"))
    parser.add_argument("--map", action="append", dest="map_ids", help="map id to process; repeat to select several")
    parser.add_argument("--crop", nargs=4, type=int, metavar=("X0", "Y0", "X1", "Y1"),
                        help="optional source-pixel crop shared by selected maps; default is each map's center square")
    parser.add_argument("--variants", nargs="?", const=Path("work/extraction/upscale-variants.json"), default=None,
                        type=Path, help="write/merge an optional variants catalog (default private path)")
    args = parser.parse_args()
    output_dir = args.output_dir or args.source_dir
    result = generate(args.manifest, args.source_dir, output_dir, args.report, args.comparison,
                      args.map_ids, args.crop, args.variants)
    for variant in result["variants"]:
        print(f"{variant['mapId']}: {variant['outputPixels'][0]}x{variant['outputPixels'][1]} {variant['outputBytes']} bytes")
