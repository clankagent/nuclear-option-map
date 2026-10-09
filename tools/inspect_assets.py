"""Inventory serialized map-related objects into a private JSON report."""
import argparse
import json
from collections import Counter
from pathlib import Path
import UnityPy
from map_assets import provenance, script_name, load_assets, monobehaviour_header


def inspect(source):
    env = load_assets(source)
    counts = Counter()
    records, errors = [], []
    for obj in env.objects:
        counts[obj.type.name] += 1
        if obj.type.name not in {"MonoBehaviour", "Mesh", "TerrainData", "Sprite"}:
            continue
        try:
            kind = script_name(obj, monobehaviour_header(obj)) if obj.type.name == "MonoBehaviour" else obj.type.name
            if kind in {"MapSettings", "MapSettingsManager", "MapDetails", "TerrainHeightMap", "TerrainData"}:
                tree = obj.parse_as_dict()
                records.append({**provenance(obj), "script": kind, "serialized": tree})
        except Exception as exc:
            errors.append({**provenance(obj), "error": str(exc)})
    return {"typeCounts": dict(counts), "mapObjects": records, "errors": errors}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(inspect(args.source), indent=2), encoding="utf-8")
