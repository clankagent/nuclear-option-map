"""Export MapSettings sprites and metadata by serialized references."""
import argparse
import json
from pathlib import Path
import UnityPy
from map_assets import metadata, provenance, resolve, script_name, load_assets, monobehaviour_header, safe_id


def extract(source, output, report_path, geometry=False):
    env = load_assets(source)
    output.mkdir(parents=True, exist_ok=True)
    maps, evidence, errors = [], [], []
    map_roots = []
    names = {}
    for manager in env.objects:
        if manager.type.name != "MonoBehaviour":
            continue
        try:
            if script_name(manager, monobehaviour_header(manager)) != "MapSettingsManager":
                continue
            for entry in manager.parse_as_dict()["Maps"]:
                prefab = resolve(manager, entry["Prefab"])
                details_obj = resolve(manager, entry["Details"])
                details = details_obj.parse_as_dict()
                names[(str(prefab.assets_file.name), prefab.path_id)] = (
                    details["MapName"], provenance(details_obj))
        except Exception as exc:
            errors.append({**provenance(manager), "stage": "map-manager", "error": str(exc)})
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            header = monobehaviour_header(obj)
            if script_name(obj, header) != "MapSettings":
                continue
            tree = obj.parse_as_dict()
            gameobject = resolve(obj, tree["m_GameObject"])
            linked = names.get((str(obj.assets_file.name), obj.path_id))
            name = linked[0] if linked else gameobject.parse_as_dict().get("m_Name", "Map")
            item = metadata(tree, name, obj.path_id)
            sprite_reader = resolve(obj, tree["MapImage"])
            if sprite_reader.type.name != "Sprite":
                raise ValueError("MapImage does not reference a Sprite")
            # UnityPy Sprite.image performs atlas crop, packing rotation and alpha merge.
            sprite = sprite_reader.parse_as_object()
            image = sprite.image
            image.save(output / item["image"])
            maps.append(item)
            evidence.append({"map": item["id"], "settings": provenance(obj),
                             "sprite": provenance(sprite_reader), "imagePixels": list(image.size),
                             "details": linked[1] if linked else None,
                             "serializedSettings": tree})
            map_roots.append((item["id"], gameobject))
        except Exception as exc:
            errors.append({**provenance(obj), "error": str(exc)})
    manifest = {"schemaVersion": 1, "maps": maps}
    if maps:
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    geometry_report = export_geometry(map_roots, output / "geometry") if geometry else None
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({"source": str(source.resolve()), "maps": evidence,
        "errors": errors, "geometry": geometry_report}, indent=2), encoding="utf-8")
    if not maps:
        raise RuntimeError("No reference-verified MapSettings sprites exported; inspect the private report")
    return manifest


def export_geometry(roots, output):
    """Follow map prefab descendants; retain local meshes and exact transform graph.

    OBJ vertices are local space. Consumers must compose the recorded transforms.
    Scene terrain elsewhere is not claimed as covered by this prefab traversal.
    """
    output.mkdir(parents=True, exist_ok=True)
    records, errors = [], []
    exported = set()
    previous = {}
    scene_path = output / "scene.json"
    if scene_path.exists():
        for record in json.loads(scene_path.read_text(encoding="utf-8")).get("objects", []):
            if "mesh" in record and Path(record["file"]).name == record["file"]:
                ref = record["mesh"]
                previous[(ref["assetFile"], ref["pathId"])] = output / record["file"]
    for map_id, root in roots:
        seen = set()
        pending = [root]
        while pending:
            go = pending.pop()
            key = (str(go.assets_file.name), go.path_id)
            if key in seen:
                continue
            seen.add(key)
            try:
                tree = go.parse_as_dict()
                for component in tree.get("m_Component", []):
                    comp = resolve(go, component.get("component", component))
                    data = comp.parse_as_dict()
                    if comp.type.name == "Transform":
                        parent = data.get("m_Father", {})
                        parent_identity = provenance(resolve(comp, parent)) if parent.get("m_PathID") else None
                        records.append({"map": map_id, **provenance(comp), "transform": data,
                                        "parent": parent_identity, "gameObject": provenance(go),
                                        "name": tree.get("m_Name", "")})
                        for child_ref in data.get("m_Children", []):
                            child = resolve(comp, child_ref)
                            pending.append(resolve(child, child.parse_as_dict()["m_GameObject"]))
                    elif comp.type.name == "MeshFilter":
                        mesh_obj = resolve(comp, data["m_Mesh"])
                        filename = f"{safe_id(mesh_obj.assets_file.name, mesh_obj.path_id)}.obj"
                        mesh_key = (str(mesh_obj.assets_file.name), mesh_obj.path_id)
                        if mesh_key not in exported:
                            target = output / filename
                            if not target.exists():
                                # Reuse earlier exports when a map display name changes.
                                legacy = previous.get(mesh_key)
                                if legacy is not None and legacy.exists():
                                    legacy.rename(target)
                                else:
                                    mesh_text = mesh_obj.parse_as_object().export()
                                    if not isinstance(mesh_text, str):
                                        raise ValueError("UnityPy could not decode this mesh as OBJ")
                                    target.write_text(mesh_text, encoding="utf-8", newline="")
                            exported.add(mesh_key)
                        transform = next((resolve(go, c.get("component", c)) for c in tree.get("m_Component", [])
                                          if resolve(go, c.get("component", c)).type.name == "Transform"), None)
                        records.append({"map": map_id, **provenance(comp), "gameObject": provenance(go),
                                        "name": tree.get("m_Name", ""),
                                        "transform": provenance(transform) if transform else None,
                                        "mesh": provenance(mesh_obj), "file": filename, "space": "local"})
            except Exception as exc:
                errors.append({"map": map_id, **provenance(go), "error": str(exc)})
    result = {"status": "partial", "coverage": "MapSettings prefab descendants only",
              "objCoordinates": "UnityPy reflects local X; transforms retain Unity X/Y/Z",
              "objBasisFromUnity": [[-1, 0, 0], [0, 1, 0], [0, 0, 1]],
              "objTriangleWinding": "reversed from Unity indices",
              "materialFidelity": "not exported", "objects": records, "errors": errors}
    (output / "scene.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("demo/public/maps"))
    parser.add_argument("--report", type=Path, default=Path("work/extraction/provenance.json"))
    parser.add_argument("--geometry", action="store_true")
    args = parser.parse_args()
    result = extract(args.source, args.output, args.report, args.geometry)
    print(f"Exported {len(result['maps'])} reference-verified maps")
