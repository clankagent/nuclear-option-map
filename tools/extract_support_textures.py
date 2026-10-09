"""Export referenced TerrainColorMap textures without changing the public catalog."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from PIL import Image

from map_assets import load_assets, provenance, resolve, script_name, monobehaviour_header


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_catalog(path):
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    maps = data.get("maps") if isinstance(data, dict) else None
    if not isinstance(maps, list):
        raise ValueError(f"Map catalog must contain a maps array: {path}")
    return {row["id"]: row for row in maps if isinstance(row, dict) and isinstance(row.get("id"), str)}


def records_from_report(report_path, catalog, selected_ids):
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    map_records = report.get("maps") if isinstance(report, dict) else None
    if not isinstance(map_records, list):
        raise ValueError(f"Extractor report must contain a maps array: {report_path}")
    records = []
    known = set()
    for item in map_records:
        if not isinstance(item, dict):
            continue
        map_id = item.get("map")
        settings_ref = item.get("settings")
        if not isinstance(map_id, str) or not isinstance(settings_ref, dict):
            continue
        if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in map_id):
            raise ValueError(f"Unsafe map id in extractor report: {map_id!r}")
        known.add(map_id)
        if selected_ids and map_id not in selected_ids:
            continue
        asset_file, path_id = settings_ref.get("assetFile"), settings_ref.get("pathId")
        if not isinstance(asset_file, str) or not isinstance(path_id, int):
            raise ValueError(f"Invalid MapSettings reference for {map_id} in {report_path}")
        catalog_row = catalog.get(map_id, {})
        records.append({"mapId": map_id, "settingsRef": {"assetFile": asset_file, "pathId": path_id},
                        "name": catalog_row.get("name") or map_id,
                        "catalogWidthMeters": catalog_row.get("widthMeters"),
                        "catalogHeightMeters": catalog_row.get("heightMeters")})
    missing = sorted(set(selected_ids or []) - known)
    if missing:
        raise ValueError(f"Map id(s) are absent from extractor report: {', '.join(missing)}")
    if not records:
        raise ValueError(f"No MapSettings records selected in {report_path}")
    return records


def compare_existing(image, path):
    if not path.is_file():
        return False
    with Image.open(path) as existing:
        existing.load()
        return existing.size == image.size and existing.mode == image.mode and existing.tobytes() == image.tobytes()


def extract(source, map_report, manifest, output, view_output, selected_ids=None,
            verify_existing=False, force=False):
    env = load_assets(source)
    catalog = read_catalog(manifest)
    requested = list(dict.fromkeys(selected_ids or []))
    records = records_from_report(map_report, catalog, requested)
    by_identity = {(str(obj.assets_file.name), obj.path_id): obj for obj in env.objects}
    output.mkdir(parents=True, exist_ok=True)
    view_output.mkdir(parents=True, exist_ok=True)
    exported = []
    for row in records:
        identity = (row["settingsRef"]["assetFile"], row["settingsRef"]["pathId"])
        settings_obj = by_identity.get(identity)
        if settings_obj is None or settings_obj.type.name != "MonoBehaviour":
            raise ValueError(f"Missing serialized MapSettings object for {row['mapId']}: {identity}")
        if script_name(settings_obj, monobehaviour_header(settings_obj)) != "MapSettings":
            raise ValueError(f"Serialized object for {row['mapId']} is not MapSettings: {identity}")
        settings = settings_obj.parse_as_dict()
        map_size = settings.get("MapSize", {})
        width_m, height_m = float(map_size.get("x", 0)), float(map_size.get("y", 0))
        if width_m <= 0 or height_m <= 0:
            raise ValueError(f"MapSettings for {row['mapId']} has invalid MapSize")
        for key, actual in (("catalogWidthMeters", width_m), ("catalogHeightMeters", height_m)):
            expected = row[key]
            if expected is not None and abs(float(expected) - actual) > 1e-4:
                raise ValueError(f"Catalog {key} disagrees with serialized MapSize for {row['mapId']}")
        game_object_name = None
        try:
            game_object_name = resolve(settings_obj, settings["m_GameObject"]).parse_as_dict().get("m_Name")
        except Exception:
            pass
        texture = resolve(settings_obj, settings["TerrainColorMap"])
        if texture.type.name != "Texture2D":
            raise ValueError(f"{row['mapId']} TerrainColorMap points to {texture.type.name}")
        decoded = texture.parse_as_object()
        image = decoded.image
        expected_size = (int(decoded.m_Width), int(decoded.m_Height))
        if image.size != expected_size:
            raise ValueError(f"Decoded dimensions {image.size} differ from Texture2D metadata {expected_size}")
        map_id = row["mapId"]
        # Keep the map ID from the extractor report stable for variant lookup.
        out_path = output / f"{map_id}-terrain-color.png"
        view_path = view_output / out_path.name
        existing_out, existing_view = out_path.exists(), view_path.exists()
        if verify_existing:
            if not compare_existing(image, out_path):
                raise ValueError(f"Existing private PNG does not match the serialized texture: {out_path}")
            if not view_path.is_file() or sha256(view_path) != sha256(out_path):
                raise ValueError(f"Existing view PNG is not byte-identical to the private extraction: {view_path}")
        else:
            if (existing_out or existing_view) and not force:
                raise FileExistsError(f"Output already exists; use --verify-existing or --force: {out_path} / {view_path}")
            image.save(out_path, format="PNG", optimize=False, compress_level=6)
            if view_path.resolve() != out_path.resolve():
                shutil.copyfile(out_path, view_path)

        exported.append({
            "mapId": map_id, "mapName": row.get("name") or game_object_name or map_id,
            "gameObjectName": game_object_name,
            "settings": provenance(settings_obj), "mapSizeMeters": [width_m, height_m],
            "terrainColorMap": provenance(texture), "textureName": decoded.m_Name,
            "texturePixels": list(image.size), "textureFormat": decoded.m_TextureFormat,
            "mipCount": decoded.m_MipCount,
            "metersPerPixel": [width_m / image.width, height_m / image.height],
            "png": str(out_path.resolve()), "pngBytes": out_path.stat().st_size,
            "pngSha256": sha256(out_path), "pngMode": image.mode,
            "viewPng": str(view_path.resolve()), "viewPngBytes": view_path.stat().st_size,
            "viewPngSha256": sha256(view_path),
            "orientation": "UnityPy Texture2D.image flips raw Unity texture rows to PIL top-left convention; Unity Texture2D.GetPixel y=0 is bottom row.",
        })
        image.close()
    report = {"source": str(Path(source).resolve()), "mapReport": str(map_report.resolve()),
              "records": exported}
    report_path = output.parent / "terrain-color-provenance.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--map-report", type=Path, default=Path("work/extraction/provenance.json"),
                        help="prior tools/extract_map.py provenance report")
    parser.add_argument("--manifest", type=Path, default=Path("demo/public/maps/manifest.json"),
                        help="optional consumer catalog for map names and extent cross-checks")
    parser.add_argument("--output", type=Path, default=Path("work/extraction/support-textures"))
    parser.add_argument("--view-output", type=Path, default=Path("demo/public/maps"))
    parser.add_argument("--map", action="append", dest="map_ids", help="map ID to export; repeat to select several")
    parser.add_argument("--verify-existing", action="store_true", help="check output pixels/hashes without rewriting PNGs")
    parser.add_argument("--force", action="store_true", help="allow replacement of existing local output PNGs")
    args = parser.parse_args()
    report = extract(args.source, args.map_report, args.manifest, args.output, args.view_output,
                     args.map_ids, args.verify_existing, args.force)
    for record in report["records"]:
        print(f"{record['mapName']}: {record['texturePixels']} {record['viewPng']}")
