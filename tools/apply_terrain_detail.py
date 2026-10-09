"""Patch only terrain color nodes in an existing extracted Blender scene.

Render a matched before/after close-up before saving in place. Run in Blender:
--background SCENE --python tools/apply_terrain_detail.py -- REPORT OUTPUT_PREFIX
"""
import json
import sys
from pathlib import Path
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from terrain_detail import build_detail
from render_safety import guarded_render


def load_image(path, space):
    image = bpy.data.images.load(str(Path(path).resolve()), check_existing=True)
    image.colorspace_settings.name = space
    if image.packed_file is None:
        image.pack()
    return image


def patch(report_file, prefix):
    report = json.loads(Path(report_file).read_text(encoding="utf-8-sig"))
    scene = bpy.context.scene
    camera = scene.camera
    # Use the existing close-up camera, preserving actual scene/scenery geometry.
    detail = next((obj for obj in bpy.data.objects if obj.type == "CAMERA" and "Airfield detail" in obj.name), camera)
    scene.camera = detail
    original_scale = detail.data.ortho_scale
    detail.data.ortho_scale = min(original_scale, 1600)
    scene.render.resolution_x = scene.render.resolution_y = 768
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(prefix.resolve()) + "-before.png"
    guarded_render(scene)
    patched = []
    for record in report["materials"]:
        if record.get("shaderName") != "Shader Graphs/TerrainShader":
            continue
        for material in bpy.data.materials:
            if material.name != record["name"] or not material.use_nodes:
                continue
            nodes, links = material.node_tree.nodes, material.node_tree.links
            if any(n.label == "Approximate source detail; macro geography retained" for n in nodes):
                raise ValueError("Terrain detail has already been applied")
            tex = next((n for n in nodes if n.type == "TEX_IMAGE" and n.label.startswith("_macro_basecolor")), None)
            if tex is None or not tex.inputs["Vector"].is_linked:
                raise ValueError("Missing terrain macro texture/UV wiring")
            consumers = [(link.to_socket) for link in list(links) if link.from_socket == tex.outputs["Color"]]
            uv = tex.inputs["Vector"].links[0].from_socket
            color = build_detail(nodes, links, record, uv, tex.outputs["Color"], load_image)
            if color == tex.outputs["Color"]:
                raise ValueError("No prepared terrain detail files")
            tex.interpolation = "Cubic"
            for consumer in consumers:
                links.new(color, consumer)
            material["terrain_detail_approximation"] = "Source splat masks + highpass detail luminance; 1000m period; strength 0.20"
            patched.append(material.name)
    if not patched:
        raise ValueError("No terrain materials matched")
    scene.render.filepath = str(prefix.resolve()) + "-after.png"
    guarded_render(scene)
    detail.data.ortho_scale = original_scale
    scene.camera = camera
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
    print("TERRAIN_DETAIL_PATCH", patched, bpy.data.filepath)


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:]
    if len(args) != 2:
        raise SystemExit("Expected REPORT OUTPUT_PREFIX")
    patch(*args)
