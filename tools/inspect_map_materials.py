"""Inspect materials assigned inside serialized map prefab hierarchies offline."""
import argparse
import json
import re
from pathlib import Path

from map_assets import load_assets, provenance, resolve, monobehaviour_header, script_name, safe_id


def _name(obj):
    try:
        return obj.peek_name() or ""
    except Exception:
        return ""


def _pairs(values):
    result = []
    for pair in values or []:
        if isinstance(pair, (tuple, list)) and len(pair) == 2:
            result.append((pair[0], pair[1]))
            continue
        if isinstance(pair, dict):
            result.append((pair.get("first", pair.get("key")), pair.get("second", pair.get("value"))))
    return result


def inspect(source, report_path, output_dir):
    env = load_assets(source)
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    rows = report.get("maps")
    if not isinstance(rows, list):
        raise ValueError("Extractor report must contain maps[]")
    objects = {(str(obj.assets_file.name), obj.path_id): obj for obj in env.objects}
    output_dir.mkdir(parents=True, exist_ok=True)
    materials = {}
    maps = []

    def material_record(mat_obj):
        key = (str(mat_obj.assets_file.name), mat_obj.path_id)
        if key in materials:
            return materials[key]
        tree = mat_obj.parse_as_dict()
        shader_name = None
        try:
            shader = resolve(mat_obj, tree["m_Shader"])
            shader_data = shader.parse_as_dict()
            shader_name = (shader_data.get("m_ParsedForm", {}).get("m_Name") or
                           shader_data.get("m_Name") or _name(shader))
        except Exception:
            pass
        saved = tree.get("m_SavedProperties", {})
        textures = []
        for prop, envmap in _pairs(saved.get("m_TexEnvs", [])):
            if not isinstance(envmap, dict):
                continue
            ref = envmap.get("m_Texture")
            entry = {"property": str(prop), "scale": envmap.get("m_Scale"),
                     "offset": envmap.get("m_Offset"), "textureRef": ref}
            try:
                tex = resolve(mat_obj, ref)
                entry.update({"texture": provenance(tex), "textureName": _name(tex)})
                if tex.type.name == "Texture2D":
                    parsed = tex.parse_as_object()
                    entry.update({"pixels": [int(parsed.m_Width), int(parsed.m_Height)],
                                  "format": str(parsed.m_TextureFormat), "mipCount": int(parsed.m_MipCount)})
                    out = output_dir / f"{safe_id(parsed.m_Name or 'texture', tex.path_id)}.png"
                    if not out.exists():
                        parsed.image.save(out, format="PNG", optimize=False, compress_level=6)
                    entry["export"] = str(out.resolve())
            except Exception as exc:
                entry["error"] = str(exc)
            textures.append(entry)
        colors = [{"property": str(k), "value": v} for k, v in _pairs(saved.get("m_Colors", []))]
        floats = [{"property": str(k), "value": v} for k, v in _pairs(saved.get("m_Floats", []))]
        record = {**provenance(mat_obj), "name": _name(mat_obj), "shaderName": shader_name,
                  "textures": textures, "colors": colors, "floats": floats}
        materials[key] = record
        return record

    for map_row in rows:
        settings_ref = map_row.get("settings", {})
        settings = objects.get((settings_ref.get("assetFile"), settings_ref.get("pathId")))
        if settings is None or settings.type.name != "MonoBehaviour":
            continue
        if script_name(settings, monobehaviour_header(settings)) != "MapSettings":
            continue
        settings_tree = settings.parse_as_dict()
        root = resolve(settings, settings_tree["m_GameObject"])
        pending, seen = [root], set()
        mesh_records, errors = [], []
        while pending:
            go = pending.pop()
            go_key = (str(go.assets_file.name), go.path_id)
            if go_key in seen:
                continue
            seen.add(go_key)
            try:
                go_tree = go.parse_as_dict()
                components = []
                for component_ref in go_tree.get("m_Component", []):
                    component = resolve(go, component_ref.get("component", component_ref))
                    component_tree = component.parse_as_dict()
                    if component.type.name == "Transform":
                        for child_ref in component_tree.get("m_Children", []):
                            child = resolve(component, child_ref)
                            pending.append(resolve(child, child.parse_as_dict()["m_GameObject"]))
                    elif component.type.name == "MeshFilter":
                        mesh = resolve(component, component_tree.get("m_Mesh"))
                        components.append({"mesh": provenance(mesh), "meshName": _name(mesh)})
                    elif component.type.name in ("MeshRenderer", "SkinnedMeshRenderer", "Renderer"):
                        material_refs = component_tree.get("m_Materials", [])
                        assigned = []
                        for ref in material_refs:
                            if not ref.get("m_PathID"):
                                continue
                            mat = resolve(component, ref)
                            material_record(mat)
                            assigned.append({**provenance(mat), "name": _name(mat)})
                        components.append({"renderer": provenance(component),
                                           "materials": assigned})
                if components:
                    mesh_records.append({**provenance(go), "gameObject": go_tree.get("m_Name", ""),
                                         "components": components})
            except Exception as exc:
                errors.append({**provenance(go), "name": _name(go), "error": str(exc)})
        maps.append({"mapId": map_row.get("map"), "root": provenance(root),
                     "gameObjectsVisited": len(seen), "renderedObjects": mesh_records, "errors": errors})
    return {"source": str(Path(source).resolve()), "mapReport": str(report_path.resolve()),
            "maps": maps, "materialCount": len(materials), "materials": list(materials.values()),
            "note": "Materials and textures resolved from serialized map-prefab MeshRenderer references; no runtime used."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--map-report", type=Path, default=Path("work/extraction/provenance.json"))
    parser.add_argument("--textures", type=Path, default=Path("work/extraction/material-textures"))
    parser.add_argument("--report", type=Path, default=Path("work/extraction/material-report.json"))
    args = parser.parse_args()
    result = inspect(args.source, args.map_report, args.textures)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"Inspected {sum(len(x['renderedObjects']) for x in result['maps'])} rendered objects; "
          f"resolved {result['materialCount']} materials")
