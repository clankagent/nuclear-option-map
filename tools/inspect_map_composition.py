"""Inspect serialized map color/water layers and scene object names offline."""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

from PIL import Image

from map_assets import load_assets, provenance, resolve, monobehaviour_header, script_name


def _color_stats(image):
    image = image.convert("RGBA")
    alpha = image.getchannel("A")
    alpha_hist = Counter(alpha.getdata())
    colors = Counter(image.convert("RGB").getdata())
    return {"size": list(image.size), "mode": image.mode,
            "alphaHistogram": dict(sorted(alpha_hist.items())),
            "commonRgb": [[list(rgb), count] for rgb, count in colors.most_common(12)]}


def _compose_fallback(layers, output):
    """Build a private candidate: game ocean color base + terrain color over land."""
    with Image.open(layers["OceanBasecolor"]["png"]) as ocean_file, \
            Image.open(layers["OceanDepthmap"]["png"]) as depth_file, \
            Image.open(layers["TerrainColorMap"]["png"]) as terrain_file:
        ocean = ocean_file.convert("RGBA")
        depth = depth_file.convert("RGB").convert("L")
        terrain = terrain_file.convert("RGBA").resize(ocean.size, Image.Resampling.LANCZOS)
        # Depth map is bright over land and dark over open water. A soft ramp
        # keeps the shoreline transition from becoming a hard binary cut.
        alpha = depth.point(lambda value: max(0, min(255, round((value - 112) * 255 / (160 - 112)))))
        terrain.putalpha(alpha)
        ocean.alpha_composite(terrain)
        output.parent.mkdir(parents=True, exist_ok=True)
        ocean.convert("RGB").save(output, format="PNG", optimize=False, compress_level=6)
        terrain.close()
        alpha.close()
        depth.close()
        ocean.close()
    return str(output.resolve())


def inspect(source, report_path, output_dir):
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    rows = report.get("maps")
    if not isinstance(rows, list):
        raise ValueError("Extractor report must contain maps[]")
    env = load_assets(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    by_identity = {(str(obj.assets_file.name), obj.path_id): obj for obj in env.objects}
    maps = []
    for row in rows:
        map_id = row.get("map")
        ref = row.get("settings") or {}
        settings_obj = by_identity.get((ref.get("assetFile"), ref.get("pathId")))
        if settings_obj is None or settings_obj.type.name != "MonoBehaviour":
            continue
        header = monobehaviour_header(settings_obj)
        if script_name(settings_obj, header) != "MapSettings":
            continue
        settings = settings_obj.parse_as_dict()
        map_record = {"mapId": map_id, "settings": provenance(settings_obj),
                      "MapSize": settings.get("MapSize"), "layers": {}}
        for field in ("TerrainColorMap", "OceanBasecolor", "OceanDepthmap"):
            try:
                obj = resolve(settings_obj, settings[field])
                if obj.type.name != "Texture2D":
                    map_record["layers"][field] = {**provenance(obj), "error": "reference is not Texture2D"}
                    continue
                parsed = obj.parse_as_object()
                image = parsed.image
                filename = f"{map_id}-{field.lower()}.png"
                output = output_dir / filename
                image.save(output, format="PNG", optimize=False, compress_level=6)
                map_record["layers"][field] = {
                    **provenance(obj), "name": parsed.m_Name,
                    "pixels": list(image.size), "format": parsed.m_TextureFormat,
                    "mipCount": parsed.m_MipCount,
                    "metersPerPixel": [float(settings["MapSize"]["x"]) / image.width,
                                       float(settings["MapSize"]["y"]) / image.height],
                    "png": str(output.resolve()), "colorStats": _color_stats(image)}
                image.close()
            except Exception as exc:
                map_record["layers"][field] = {"error": str(exc)}
        maps.append(map_record)

        if all(map_record["layers"].get(key, {}).get("png")
               for key in ("TerrainColorMap", "OceanBasecolor", "OceanDepthmap")):
            candidate = output_dir / f"{map_id}-fallback-candidate.png"
            map_record["fallbackCandidate"] = _compose_fallback(map_record["layers"], candidate)
            map_record["fallbackMethod"] = (
                "OceanBasecolor RGB base; resized TerrainColorMap alpha-blended with "
                "OceanDepthmap grayscale ramp 112..160; candidate only, UV alignment not code-verified")

    # Names are metadata-only, avoiding Texture2D payload reads. This reveals
    # whether separate building/road mesh classes are serialized in the client.
    relevant = []
    pattern = re.compile(r"build|road|terrain2_tile|terrain_naval_island|water|ocean", re.I)
    for obj in env.objects:
        if obj.type.name not in ("GameObject", "Mesh", "MeshFilter"):
            continue
        try:
            name = obj.peek_name()
            if name and pattern.search(name):
                relevant.append({**provenance(obj), "name": name})
        except Exception:
            pass
    return {"source": str(Path(source).resolve()), "mapReport": str(report_path.resolve()),
            "maps": maps, "relevantObjectNames": relevant,
            "note": "Serialized texture and object-name inspection only; no game runtime or height extrapolation."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--map-report", type=Path, default=Path("work/extraction/provenance.json"))
    parser.add_argument("--output", type=Path, default=Path("work/extraction/composition-textures"))
    parser.add_argument("--report", type=Path, default=Path("work/extraction/composition-report.json"))
    args = parser.parse_args()
    result = inspect(args.source, args.map_report, args.output)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Inspected {len(result['maps'])} maps; exported referenced visual layers")
