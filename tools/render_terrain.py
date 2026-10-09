"""Headless Blender terrain import, elevation raster, topographic render and GLB.

blender -b --python tools/render_terrain.py -- --scene SCENE --manifest MANIFEST
    --map heartland-46396 --output work/terrain/heartland --resolution 1024 --glb
"""
import argparse
import json
import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpy
import numpy as np
from mathutils import Vector
from import_blender import load
from terrain_selection import terrain_record


def material(kind, max_height, contour_interval, height_offset=0):
    mat = bpy.data.materials.new(f"terrain-{kind}")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    links.new(emission.outputs[0], output.inputs["Surface"])
    geometry = nodes.new("ShaderNodeNewGeometry")
    separate = nodes.new("ShaderNodeSeparateXYZ")
    links.new(geometry.outputs["Position"], separate.inputs[0])
    height = separate.outputs["Z"]
    if kind == "elevation":
        subtract = nodes.new("ShaderNodeMath")
        subtract.operation = "SUBTRACT"
        links.new(height, subtract.inputs[0])
        subtract.inputs[1].default_value = height_offset
        links.new(subtract.outputs[0], emission.inputs["Color"])
        return mat
    divide = nodes.new("ShaderNodeMath")
    divide.operation = "DIVIDE"
    links.new(height, divide.inputs[0])
    divide.inputs[1].default_value = max(max_height, 1)
    ramp = nodes.new("ShaderNodeValToRGB")
    colors = [(0, (0.13, 0.25, 0.16, 1)), (.12, (.28, .39, .19, 1)),
              (.4, (.52, .47, .27, 1)), (.7, (.55, .43, .31, 1)), (1, (.8, .79, .73, 1))]
    for element in list(ramp.color_ramp.elements)[1:]:
        ramp.color_ramp.elements.remove(element)
    ramp.color_ramp.elements[0].position, ramp.color_ramp.elements[0].color = colors[0]
    for position, color in colors[1:]:
        ramp.color_ramp.elements.new(position).color = color
    links.new(divide.outputs[0], ramp.inputs[0])
    modulo = nodes.new("ShaderNodeMath")
    modulo.operation = "MODULO"
    modulo.inputs[1].default_value = contour_interval
    links.new(height, modulo.inputs[0])
    threshold = nodes.new("ShaderNodeMath")
    threshold.operation = "LESS_THAN"
    threshold.inputs[1].default_value = max(contour_interval * .025, .5)
    links.new(modulo.outputs[0], threshold.inputs[0])
    mix = nodes.new("ShaderNodeMixRGB")
    mix.inputs[2].default_value = (.09, .1, .07, 1)
    links.new(threshold.outputs[0], mix.inputs[0])
    links.new(ramp.outputs[0], mix.inputs[1])
    links.new(mix.outputs[0], emission.inputs["Color"])
    return mat


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(args.manifest.read_text())
    map_info = next(m for m in manifest["maps"] if m["id"] == args.map)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    collection = load(args.scene, None, args.map, terrain_record)
    objects = list(collection.objects)
    if not objects:
        raise ValueError("No evidence-matched base terrain meshes selected")
    corners = [o.matrix_world @ Vector(c) for o in objects for c in o.bound_box]
    minimum = [min(c[i] for c in corners) for i in range(3)]
    maximum = [max(c[i] for c in corners) for i in range(3)]
    width, height = map_info["widthMeters"], map_info["heightMeters"]
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    scene.render.resolution_x = args.resolution
    scene.render.resolution_y = round(args.resolution * height / width)
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.view_settings.view_transform = "Standard"
    camera_data = bpy.data.cameras.new("map-orthographic")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = width
    camera_data.clip_start = .1
    camera_data.clip_end = max(100000, maximum[2] - minimum[2] + 10000)
    camera = bpy.data.objects.new("map-orthographic", camera_data)
    scene.collection.objects.link(camera)
    camera.location = (0, 0, maximum[2] + 1000)
    scene.camera = camera
    scene.world.color = (.025, .05, .075)
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_depth = "32"
    scene.render.image_settings.color_mode = "RGBA"
    height_offset = min(minimum[2], 0)
    elevation = material("elevation", maximum[2], args.contour, height_offset)
    for obj in objects:
        obj.data.materials.clear()
        obj.data.materials.append(elevation)
    scene.render.filepath = str((args.output / "elevation.exr").resolve())
    print(f"Rendering {len(objects)} base terrain objects, {sum(len(o.data.vertices) for o in objects)} vertices", flush=True)
    bpy.ops.render.render(write_still=True)
    # Render cameras look down Blender -Z; Blender Y=-UnityZ. Flip vertically
    # to store rows north-to-south, columns west-to-east in world X/Z.
    image = bpy.data.images.load(scene.render.filepath, check_existing=False)
    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape((image.size[1], image.size[0], 4))
    # Blender's pixel buffer starts bottom-left, which is north in this basis.
    heights = pixels[:, :, 0].copy() + height_offset
    coverage = pixels[:, :, 3] > .5
    heights[~coverage] = np.nan
    np.save(args.output / "height-meters.npy", heights)
    np.save(args.output / "coverage.npy", coverage)
    topo = material("topographic", maximum[2], args.contour)
    for obj in objects:
        obj.data.materials.clear()
        obj.data.materials.append(topo)
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_depth = "8"
    # Use Blender compositor Flip Y for a north-up exported PNG.
    scene.use_nodes = True
    nodes, links = scene.node_tree.nodes, scene.node_tree.links
    nodes.clear()
    render_layer = nodes.new("CompositorNodeRLayers")
    flip = nodes.new("CompositorNodeFlip")
    flip.axis = "Y"
    composite = nodes.new("CompositorNodeComposite")
    links.new(render_layer.outputs["Image"], flip.inputs[0])
    links.new(flip.outputs[0], composite.inputs[0])
    scene.render.filepath = str((args.output / "topographic.png").resolve())
    bpy.ops.render.render(write_still=True)
    # Compare rendered float samples with actual transformed terrain intersections.
    # Pixel-center rays provide an independent geometry-based elevation check.
    checks = []
    evaluated = bpy.context.evaluated_depsgraph_get()
    for obj in objects:
        center = sum((obj.matrix_world @ Vector(c) for c in obj.bound_box), Vector()) / 8
        column = int((center.x / width + .5) * image.size[0])
        row = int((center.y / height + .5) * image.size[1])
        if not (0 <= row < image.size[1] and 0 <= column < image.size[0]) or not coverage[row, column]:
            continue
        x = ((column + .5) / image.size[0] - .5) * width
        y = ((row + .5) / image.size[1] - .5) * height
        hit, location, normal, _, _, _ = scene.ray_cast(evaluated,
            Vector((x, y, maximum[2] + 1000)), Vector((0, 0, -1)))
        if hit:
            checks.append({"row": row, "column": column, "worldX": x, "worldZ": -y,
                "rayHeightMeters": location.z, "rasterHeightMeters": float(heights[row, column]),
                "errorMeters": abs(location.z - float(heights[row, column])),
                "normal": list(normal)})
        if len(checks) >= 16:
            break
    bpy.ops.wm.save_as_mainfile(filepath=str((args.output / "terrain.blend").resolve()))
    if args.glb:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in objects:
            obj.select_set(True)
        bpy.ops.export_scene.gltf(filepath=str((args.output / "terrain.glb").resolve()),
                                  export_format="GLB", use_selection=True)
    report = {"schemaVersion": 1, "map": args.map, "status": "base-terrain-only",
        "selection": "terrain2_tile[0-9]+ or terrain_naval_island[0-9]+ serialized mesh names",
        "objects": len(objects), "vertices": sum(len(o.data.vertices) for o in objects),
        "boundsBlenderMeters": {"min": minimum, "max": maximum},
        "widthMeters": width, "heightMeters": height,
        "rasterWidth": image.size[0], "rasterHeight": image.size[1],
        "heightArray": "height-meters.npy", "coverageArray": "coverage.npy",
        "exrHeightOffsetMeters": height_offset,
        "sampleHeightMinMeters": float(np.nanmin(heights)),
        "sampleHeightMaxMeters": float(np.nanmax(heights)),
        "arrayOrder": "north-to-south rows, west-to-east columns; world X/Z pixel centers",
        "missingGeometry": "NaN height and false coverage; negative elevations retained only where source geometry exists",
        "contourIntervalMeters": args.contour, "coveredPixels": int(coverage.sum()),
        "rayValidation": checks,
        "limitations": "Base mesh surface only; negative elevations retained where source geometry exists, missing coverage unknown; scenery/overlays and original materials excluded"}
    (args.output / "terrain.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--map", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--resolution", default=1024, type=int)
    parser.add_argument("--contour", default=100, type=float)
    parser.add_argument("--glb", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if not 64 <= args.resolution <= 8192 or not math.isfinite(args.contour) or args.contour <= 0:
        parser.error("Resolution must be64..8192 and contour interval must be positive and finite")
    run(args)
