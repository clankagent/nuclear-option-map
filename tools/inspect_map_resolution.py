"""Inspect serialized map images and referenced terrain textures locally."""
import argparse
import json
import re
from pathlib import Path

from map_assets import load_assets, provenance, resolve


def ref_id(value):
    if isinstance(value, dict):
        return int(value.get("m_PathID", 0))
    return 0


def texture_record(obj):
    tree = obj.parse_as_dict()
    fields = ("m_Name", "m_Width", "m_Height", "m_MipCount", "m_TextureFormat",
              "m_CompleteImageSize", "m_StreamData", "image data size")
    return {**provenance(obj), **{key: tree.get(key) for key in fields if key in tree}}


def inspect(source):
    env = load_assets(source)
    # The known built-in MapSettings IDs are from the private extractor report.
    # Avoid parsing every Texture2D: its typetree includes image_data, which can
    # allocate gigabytes while only its metadata is needed here.
    map_ids = {46395, 46396}
    objects = (o for o in env.objects if o.type.name == "MonoBehaviour" and o.path_id in map_ids)
    maps = []
    for obj in objects:
        try:
            tree = obj.parse_as_dict()
            refs = {}
            for key in ("MapImage", "TerrainColorMap", "OceanDepthmap", "OceanBasecolor"):
                value = tree.get(key)
                try:
                    target = resolve(obj, value)
                    if target.type.name == "Sprite":
                        sprite_tree = target.parse_as_dict()
                        texture_ref = sprite_tree.get("m_RD", {}).get("texture", {})
                        tex_path = ref_id(texture_ref)
                        backing = resolve(target, texture_ref) if tex_path else None
                        image_name = ("ignus-archipelago-46395.png" if obj.path_id == 46395
                                      else "heartland-46396.png")
                        refs[key] = {**provenance(target), "name": sprite_tree.get("m_Name"),
                                     "spriteRect": sprite_tree.get("m_Rect"),
                                     "spritePixels": [sprite_tree["m_Rect"]["width"], sprite_tree["m_Rect"]["height"]],
                                     "texturePathId": tex_path,
                                     "extractedImage": str(Path("demo/public/maps") / image_name),
                                     "backingTexture": texture_record(backing) if backing else None}
                    elif target.type.name == "Texture2D":
                        refs[key] = texture_record(target)
                    else:
                        refs[key] = {**provenance(target), "name": target.parse_as_dict().get("m_Name")}
                except Exception as exc:
                    refs[key] = {"reference": value, "error": str(exc)}
            name = None
            try:
                go = resolve(obj, tree["m_GameObject"])
                name = go.parse_as_dict().get("m_Name")
            except Exception:
                pass
            maps.append({**provenance(obj), "gameObject": name, "MapSize": tree.get("MapSize"),
                         "references": refs})
        except Exception:
            continue

    named_objects = []
    name_re = re.compile(r"(?:terrain.*(?:map|thumbnail)|^(?:editorMap|mapGrids|mapPanel)$)", re.I)
    for item in env.objects:
        if item.type.name != "Sprite":
            continue
        try:
            name = item.peek_name()
            if not name or not name_re.search(name):
                continue
            sprite = item.parse_as_dict()
            texture_ref = sprite.get("m_RD", {}).get("texture", {})
            backing = resolve(item, texture_ref) if ref_id(texture_ref) else None
            named_objects.append({**provenance(item), "name": name,
                                  "spriteRect": sprite.get("m_Rect"),
                                  "backingTexture": texture_record(backing) if backing else None})
        except Exception:
            pass
    return {"source": str(Path(source).resolve()), "maps": maps,
            "searchedMapSettingsPathIds": sorted(map_ids),
            "mapNamedSpritesAndTextures": named_objects,
            "note": "Targeted built-in MapSettings and the small set of map-named Sprite candidates; no game runtime was used."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.source)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"Inspected {len(result['maps'])} targeted built-in MapSettings objects")
