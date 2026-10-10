"""Update referenced terrain materials in a packed scene without reimporting geometry.

Run in background Blender with an existing scene, then -- report.json output.blend.
This helper prepares a CPU scene; it does not render.
"""
import json
import argparse
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_material_prototype import make_material
from render_safety import configure_scene, lower_process_priority, render_slot
from terrain_texture_variant import TerrainTextureVariant
from check_terrain_texture_variant_scene import geometry_fingerprint


def main(report_path, output_path, terrain_textures=None):
    with render_slot():
        lower_process_priority()
        variant = TerrainTextureVariant(terrain_textures) if terrain_textures else None
        source_geometry = geometry_fingerprint(bpy.context.scene)
        report = json.loads(Path(report_path).read_text(encoding="utf-8-sig"))
        records = [r for r in report["materials"] if r.get("shaderName") == "Shader Graphs/TerrainShader"]
        changed = []
        for record in records:
            old = bpy.data.materials.get(record["name"])
            if old is None or not old.users:
                continue
            new = make_material(record, {}, texture_variant=variant)
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
        if geometry_fingerprint(bpy.context.scene) != source_geometry:
            raise ValueError("Terrain material replacement changed geometry or UVs")
        bpy.context.scene["terrainSourceGeometrySha256"] = source_geometry
        bpy.context.preferences.filepaths.save_version = 0
        output = Path(output_path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        receipt = {"terrainMaterials": changed, "surface": "source-rgb-lit",
                   "cpuThreads": 2, "rendered": False, "output": str(output),
                   "sourceGeometrySha256": source_geometry}
        if variant:
            receipt.update(surface="selected-rgb-lit", textureVariant=str(variant.path),
                           textureVariantSha256=variant.sha256,
                           originalAlphaNormalsMasks=True)
            bpy.context.scene["terrainTextureVariant"] = str(variant.path)
            bpy.context.scene["terrainTextureVariantSha256"] = variant.sha256
        # Save the provenance with the packed scene, not only the receipt.
        bpy.ops.wm.save_as_mainfile(filepath=str(output))
        output.with_suffix(".terrain.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print(json.dumps(receipt))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("report")
    parser.add_argument("output")
    parser.add_argument("--terrain-textures")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    main(args.report, args.output, args.terrain_textures)
