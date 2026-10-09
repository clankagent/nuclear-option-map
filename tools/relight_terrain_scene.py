"""Update referenced terrain materials in a packed scene without reimporting geometry.

Run in background Blender with an existing scene, then -- report.json output.blend.
This helper prepares a CPU scene; it does not render.
"""
import json
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_material_prototype import make_material
from render_safety import configure_scene, lower_process_priority, render_slot


def main(report_path, output_path):
    with render_slot():
        lower_process_priority()
        report = json.loads(Path(report_path).read_text(encoding="utf-8-sig"))
        records = [r for r in report["materials"] if r.get("shaderName") == "Shader Graphs/TerrainShader"]
        changed = []
        for record in records:
            old = bpy.data.materials.get(record["name"])
            if old is None or not old.users:
                continue
            new = make_material(record, {})
            old.user_remap(new)
            bpy.data.materials.remove(old)
            new.name = record["name"]
            changed.append(new.name)
        if not changed:
            raise ValueError("No referenced terrain material found in scene")
        # Purge superseded macro derivatives before packing the source textures.
        for image in list(bpy.data.images):
            if image.users == 0:
                bpy.data.images.remove(image)
        for image in bpy.data.images:
            if image.source == "FILE" and not image.packed_file:
                image.pack()
        configure_scene(bpy.context.scene, samples=16, threads=2)
        bpy.context.preferences.filepaths.save_version = 0
        output = Path(output_path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(output))
        receipt = {"terrainMaterials": changed, "surface": "source-rgb-lit",
                   "cpuThreads": 2, "rendered": False, "output": str(output)}
        output.with_suffix(".terrain.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print(json.dumps(receipt))


if __name__ == "__main__":
    main(*sys.argv[sys.argv.index("--") + 1:])
