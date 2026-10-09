"""Reference-based, offline Unity asset helpers. No game code is bundled."""
import math
import re
from pathlib import Path
import UnityPy
from UnityPy.classes import PPtr


def load_assets(source):
    source = Path(source)
    if not source.is_dir():
        return UnityPy.load(str(source))
    # A Unity Data folder contains native libraries and arbitrary bundle formats.
    # Load its serialized scene/assets and resource streams, not every DLL/file.
    paths = [str(p) for p in source.iterdir() if p.is_file() and
             (p.suffix in {".assets", ".resS", ".resource"} or
              re.fullmatch(r"level\d+", p.name) or p.name == "globalgamemanagers")]
    if not paths:
        raise ValueError("No root Unity serialized assets found; pass the actual Data folder")
    env = UnityPy.load(*paths)
    managed = source / "Managed"
    if managed.is_dir():
        from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator
        version = next(iter(env.objects)).assets_file.unity_version
        generator = TypeTreeGenerator(version)
        generator.load_local_dll_folder(str(managed))
        env.typetree_generator = generator
    return env


def safe_id(name, path_id):
    stem = re.sub(r"[^a-z0-9_-]+", "-", str(name).lower()).strip("-_")[:64]
    numeric = int(path_id)
    suffix = str(numeric) if numeric >= 0 else f"n{abs(numeric)}"
    return f"{stem or 'map'}-{suffix}"


def resolve(owner, reference):
    if not isinstance(reference, dict) or not reference.get("m_PathID"):
        raise ValueError("Missing serialized object reference")
    if "m_FileID" not in reference or int(reference["m_FileID"]) < 0:
        raise ValueError("Invalid serialized file reference")
    return PPtr(m_FileID=int(reference["m_FileID"]),
                m_PathID=int(reference["m_PathID"]), assetsfile=owner.assets_file).deref()


def script_name(obj, tree):
    try:
        script = resolve(obj, tree["m_Script"]).parse_as_dict()
        return script.get("m_ClassName", script.get("m_Name", ""))
    except (KeyError, ValueError, FileNotFoundError):
        return ""


def monobehaviour_header(obj):
    from UnityPy.helpers.Tpk import get_typetree_node
    node = get_typetree_node(obj.class_id, obj.version)
    return obj.parse_as_dict(node, check_read=False)


def metadata(tree, name, path_id):
    size = tree["MapSize"]
    width, height = float(size["x"]), float(size["y"])
    if not all(math.isfinite(v) and v > 0 for v in (width, height)):
        raise ValueError("MapSize must contain positive finite meter dimensions")
    grid = {"columns": int(tree["GridSizeX"]), "rows": int(tree["GridSizeY"]),
            "offsetX": int(tree["OffsetX"]), "offsetY": int(tree["OffsetY"])}
    if grid["columns"] <= 0 or grid["rows"] <= 0:
        raise ValueError("Grid dimensions must be positive")
    ident = safe_id(name, path_id)
    return {"id": ident, "name": str(name), "widthMeters": width,
            "heightMeters": height, "image": f"{ident}.png", "grid": grid}


def provenance(obj):
    return {"assetFile": str(obj.assets_file.name), "pathId": obj.path_id,
            "type": obj.type.name}
