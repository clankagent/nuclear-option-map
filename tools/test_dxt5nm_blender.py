"""Render a two-pixel-pair fixture through the same Blender normal decoder."""
import sys
from pathlib import Path

import bpy
from mathutils import Euler
sys.path.insert(0, str(Path(__file__).resolve().parent))
from unity_normal_map import build_dxt5nm_encoded


def fixture_material(name, alpha, green):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    a, g = nodes.new("ShaderNodeValue"), nodes.new("ShaderNodeValue")
    a.outputs[0].default_value = alpha
    g.outputs[0].default_value = green
    encoded = build_dxt5nm_encoded(nodes, links, a.outputs[0], g.outputs[0])
    links.new(encoded, emission.inputs["Color"])
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return mat


def main(output_path):
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for x, name, alpha, green in ((-0.5, "flat", 0.5, 0.5), (0.5, "tilted", 0.75, 0.5)):
        bpy.ops.mesh.primitive_plane_add(size=0.9, location=(x, 0, 0))
        plane = bpy.context.object
        plane.name = name
        plane.data.materials.append(fixture_material(name, alpha, green))
    camera_data = bpy.data.cameras.new("fixture-camera")
    camera = bpy.data.objects.new("fixture-camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = (0, 0, 3)
    camera.rotation_euler = Euler((0, 0, 0), "XYZ")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 2.0
    scene = bpy.context.scene
    scene.camera = camera
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x, scene.render.resolution_y = 32, 16
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.view_settings.view_transform = "Standard"
    scene.render.filepath = output_path
    bpy.ops.render.render(write_still=True)
    proof = bpy.data.images.load(output_path, check_existing=False)
    proof.colorspace_settings.name = "Non-Color"

    def pixel(x, y):
        start = (y * proof.size[0] + x) * 4
        return tuple(round(255 * proof.pixels[start + channel]) for channel in range(3))

    flat, tilted = pixel(8, 8), pixel(24, 8)
    print(f"DXT5NM_PIXELS flat={flat} tilted={tilted}")
    expected = ((188, 188, 255), (225, 188, 247))
    for actual, target in zip((flat, tilted), expected):
        if any(abs(channel - expected_channel) > 3 for channel, expected_channel in zip(actual, target)):
            raise AssertionError(f"Blender normal decode pixel {actual} differs from expected {target}")


args = sys.argv[sys.argv.index("--") + 1:]
if len(args) != 1:
    raise SystemExit("Pass one output PNG path after --")
main(args[0])
