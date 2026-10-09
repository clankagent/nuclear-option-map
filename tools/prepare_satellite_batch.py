"""Prepare a packed, compressed camera animation for an offline render service.

Background Blender: -- scene.blend --python this.py -- MAP_ID WIDTH DEPTH OUTPUT
No render or network operation occurs. The saved scene remains CPU configured.
Production and saved preview cameras use 1248 pixels at 5 meters per pixel.
A 736-pixel preview preserves the camera footprint at 8.478 meters per pixel; receipt metadata
records this explicitly. Changing canvas size alone changes map resolution.
"""
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_safety import configure_scene, lower_process_priority

TILE, GUTTER, PADDING = 512, 2, 110
SPAN, LEVEL = 2, 5
MAP_BOUNDS = {"heartland-46396": (81920.0, 81920.0),
              "ignus-archipelago-46395": (163840.0, 81920.0)}


def main(map_id, width, depth, output):
    width, depth = float(width), float(depth)
    if not all(math.isfinite(v) and v > 0 for v in (width, depth)):
        raise ValueError("Invalid map bounds")
    if map_id not in MAP_BOUNDS or (width, depth) != MAP_BOUNDS[map_id]:
        raise ValueError("Require the known map ID and its matching world bounds")
    family = "heartland" if map_id == "heartland-46396" else "ignus"
    source = Path(bpy.data.filepath).resolve()
    output = Path(output).resolve()
    if family not in source.stem.lower():
        raise ValueError("Loaded scene filename does not match requested map family")
    if source == output:
        raise ValueError("Batch preparation must not overwrite its source scene")
    if any(obj.type == "CAMERA" and (obj.name.startswith("Satellite batch") or
           obj.name.startswith("Preview ")) for obj in bpy.context.scene.objects):
        raise ValueError("Scene already contains batch cameras; start from the original source scene")
    columns, rows = round(width / min(width, depth)) * 2**LEVEL, round(depth / min(width, depth)) * 2**LEVEL
    if not math.isclose(width / columns, depth / rows):
        raise ValueError("Require square map pixels")
    lower_process_priority()
    scene = bpy.context.scene
    top = max((obj.matrix_world @ Vector(p)).z for obj in scene.objects
              if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_render for p in obj.bound_box)
    logical_edge = SPAN * TILE + 2 * GUTTER
    edge = logical_edge + 2 * PADDING
    if edge * edge > 1920 * 1080:
        raise ValueError("Frame exceeds service's 1080p pixel budget")
    configure_scene(scene, samples=16, threads=2, render_edge=edge)
    mpp = width / (columns * TILE)
    data = bpy.data.cameras.new("Satellite batch camera")
    data.type = "ORTHO"
    data.clip_start, data.clip_end = 1, 100000
    data.ortho_scale = edge * mpp
    camera = bpy.data.objects.new("Satellite batch", data)
    scene.collection.objects.link(camera)
    camera.rotation_euler = (0, 0, 0)
    scene.camera = camera
    frames = []
    for x in range(0, columns, SPAN):
        for y in range(0, rows, SPAN):
            frame = len(frames) + 1
            cx = -width/2 + (x+SPAN/2)*TILE*mpp
            cz = depth/2 - (y+SPAN/2)*TILE*mpp
            camera.location = (cx, -cz, top+8000)
            camera.keyframe_insert("location", frame=frame)
            frames.append({"frame": frame, "column": x, "row": y, "span": SPAN,
                           "x": cx, "z": cz, "metersPerPixel": mpp,
                           "size": logical_edge, "renderEdge": edge, "padding": PADDING})
    if camera.animation_data:
        for action_layer in camera.animation_data.action.layers:
            for strip in action_layer.strips:
                for bag in getattr(strip, "channelbags", ()):
                    for curve in bag.fcurves:
                        for point in curve.keyframe_points:
                            point.interpolation = "CONSTANT"
    scene.frame_start, scene.frame_end = 1, len(frames)
    scene.frame_set(1)
    scene.render.resolution_x = scene.render.resolution_y = edge
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False
    scene.cycles.use_animated_seed = False
    scene.cycles.seed = 0
    scene.use_nodes = True
    nodes, links = scene.node_tree.nodes, scene.node_tree.links
    nodes.clear()
    layer = nodes.new("CompositorNodeRLayers")
    flip = nodes.new("CompositorNodeFlip"); flip.axis = "Y"
    out = nodes.new("CompositorNodeComposite")
    links.new(layer.outputs["Image"], flip.inputs[0]); links.new(flip.outputs[0], out.inputs[0])
    samples = [("city", 7500, 9000), ("airport", 12962.54, 19765.46), ("mountain", -14000, 8000), ("coast", -16500, 19500)] if family == "heartland" else [("city", -4533, 1787), ("coast", -75576, -33162), ("mountain", 63768, 30276)]
    previews = []
    for name, x, z in samples:
        preview_data = bpy.data.cameras.new("Preview " + name)
        preview_data.type = "ORTHO"
        preview_data.clip_start, preview_data.clip_end = 1, 100000
        preview_data.ortho_scale = edge * mpp
        preview = bpy.data.objects.new("Preview " + name, preview_data)
        scene.collection.objects.link(preview)
        preview.location = (x, -z, top+8000)
        preview.rotation_euler = (0, 0, 0)
        previews.append({"camera": preview.name, "x": x, "z": z,
                         "metersPerPixel": mpp, "renderEdge": edge,
                         "size": logical_edge, "padding": PADDING,
                         "recommendedPreviewWidth": 736, "recommendedPreviewHeight": 736,
                         "recommendedPreviewMetersPerPixel": edge * mpp / 736})
    for image in bpy.data.images:
        if image.source == "FILE" and not image.packed_file:
            image.pack()
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(output), compress=True)
    receipt = {"mapId": map_id, "worldWidth": width, "worldDepth": depth,
               "level": LEVEL, "columns": columns, "rows": rows,
               "tileSize": TILE, "gutter": GUTTER, "padding": PADDING,
               "camera": camera.name, "renderEdge": edge, "frames": frames,
               "previewCameras": [p["camera"] for p in previews], "previews": previews,
               "sourceBlend": str(source),
               "blendBytes": output.stat().st_size, "rendered": False,
               "uploaded": False, "savedDevice": "CPU"}
    output.with_suffix(".batch.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in receipt.items() if k != "frames"}))


if __name__ == "__main__":
    main(*sys.argv[sys.argv.index("--") + 1:])
