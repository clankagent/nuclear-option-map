"""One lossless overhead CPU quality comparison; run inside background Blender.

blender --background source.blend --threads 2 --python-exit-code 1
  --python tools/render_quality_probe.py -- --x X --z Z --world-width METERS
  --world-depth METERS --width 4096 --samples 128 --output OUTPUT_DIRECTORY
Never saves or modifies the input scene on disk or promotes live map imagery.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_safety import configure_scene, guarded_render, lower_process_priority, scene_evidence


def probe_dimensions(world_width, world_depth, width):
    if not all(math.isfinite(v) and v > 0 for v in (world_width, world_depth)):
        raise ValueError("World footprint must be finite and positive")
    if not isinstance(width, int) or not 256 <= width <= 4096:
        raise ValueError("Quality probe width must be 256..4096")
    height = round(width * world_depth / world_width)
    if not 256 <= height <= 4096:
        raise ValueError("Quality probe height must be 256..4096")
    return width, height


def probe_ortho_scale(world_width, width, height):
    """AUTO sensor fitting uses the larger image dimension, including portrait."""
    return world_width * max(1.0, height / width)


def main(argv):
    import bpy
    from mathutils import Vector

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--x", required=True, type=float)
    parser.add_argument("--z", required=True, type=float)
    parser.add_argument("--world-width", required=True, type=float)
    parser.add_argument("--world-depth", required=True, type=float)
    parser.add_argument("--width", type=int, default=4096)
    parser.add_argument("--samples", type=int, default=128)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if not all(math.isfinite(v) for v in (args.x, args.z)):
        parser.error("Coordinates must be finite")
    width, height = probe_dimensions(args.world_width, args.world_depth, args.width)
    output = args.output.resolve()
    if output.exists():
        parser.error("Use a fresh output directory to preserve comparisons")
    source = Path(bpy.data.filepath).resolve()
    if not source.is_file():
        parser.error("Load the source scene before preparing the probe")
    source_stat = source.stat()
    scene = bpy.context.scene
    lower_process_priority()
    configure_scene(scene, samples=args.samples, threads=2,
                    render_edge=max(width, height), quality_cpu=True)
    bounds = []
    top = 0
    for obj in scene.objects:
        if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_render:
            points = [obj.matrix_world @ Vector(v) for v in obj.bound_box]
            top = max(top, max(p.z for p in points))
            bounds.append((obj, min(p.x for p in points), min(p.y for p in points),
                           max(p.x for p in points), max(p.y for p in points)))
    shadow = 2048.0
    for obj in scene.objects:
        if obj.type == "LIGHT" and obj.data.type == "SUN":
            ray = obj.matrix_world.to_quaternion() @ Vector((0, 0, -1))
            if abs(ray.z) < 0.05:
                shadow = max(shadow, 163840.0)
            else:
                shadow = max(shadow, top * math.hypot(ray.x, ray.y) / abs(ray.z))
    # Visibility culling preserves the conservative shadow margin. It is not a
    # claim that loaded texture/geometry residency has been physically cropped.
    cx, cy = args.x, -args.z
    hx, hy = args.world_width / 2 + shadow, args.world_depth / 2 + shadow
    visible = 0
    for obj, x0, y0, x1, y1 in bounds:
        keep = x1 >= cx-hx and x0 <= cx+hx and y1 >= cy-hy and y0 <= cy+hy
        obj.hide_render = not keep
        visible += int(keep)
    camera_data = bpy.data.cameras.new("Quality probe")
    camera_data.type = "ORTHO"
    camera_data.clip_start, camera_data.clip_end = 1, 100000
    camera_data.ortho_scale = probe_ortho_scale(args.world_width, width, height)
    camera = bpy.data.objects.new("Quality probe", camera_data)
    scene.collection.objects.link(camera)
    camera.location = (cx, cy, top + 8000)
    camera.rotation_euler = (0, 0, 0)
    scene.camera = camera
    scene.frame_set(1)
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.render.resolution_percentage = 100
    scene.render.use_border = False
    scene.render.use_crop_to_border = False
    scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.film_transparent = False
    scene.cycles.seed = 0
    scene.cycles.use_animated_seed = False
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_min_samples = min(32, args.samples)
    scene.cycles.adaptive_threshold = 0.005
    scene.render.filter_size = 1.0
    scene.use_nodes = True
    nodes, links = scene.node_tree.nodes, scene.node_tree.links
    nodes.clear()
    layer = nodes.new("CompositorNodeRLayers")
    flip = nodes.new("CompositorNodeFlip")
    flip.axis = "Y"
    composite = nodes.new("CompositorNodeComposite")
    links.new(layer.outputs["Image"], flip.inputs[0])
    links.new(flip.outputs[0], composite.inputs[0])
    output.mkdir(parents=True)
    scene.render.filepath = str(output / "raw.png")
    settings = dict(scene_evidence(scene), sourceBlend=str(source),
                    sourceBytes=source_stat.st_size, sourceMtimeNs=source_stat.st_mtime_ns,
                    centerX=args.x, centerZ=args.z, worldWidth=args.world_width,
                    worldDepth=args.world_depth, metersPerPixel=args.world_width / width,
                    maxSamples=args.samples, adaptiveMinSamples=scene.cycles.adaptive_min_samples,
                    adaptiveThreshold=scene.cycles.adaptive_threshold,
                    filterSize=scene.render.filter_size, visibleObjects=visible,
                    cameraScale=camera_data.ortho_scale, cameraLocation=list(camera.location),
                    cameraRotation=list(camera.rotation_euler),
                    pixelAspect=[scene.render.pixel_aspect_x, scene.render.pixel_aspect_y],
                    viewTransform=scene.view_settings.view_transform,
                    look=scene.view_settings.look, exposure=scene.view_settings.exposure,
                    gamma=scene.view_settings.gamma,
                    shadowMarginMeters=shadow, northUpFlip="Y", rawDenoised=False)
    (output / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    started = time.monotonic()
    guarded_render(scene, samples=args.samples, threads=2, quality_cpu=True)
    if (source.stat().st_size, source.stat().st_mtime_ns) != (source_stat.st_size, source_stat.st_mtime_ns):
        raise RuntimeError("Source scene changed during probe")
    proof = dict(settings, **scene_evidence(scene), seconds=time.monotonic()-started,
                 output=str(output / "raw.png"), outputBytes=(output / "raw.png").stat().st_size,
                 outputSha256=hashlib.sha256((output / "raw.png").read_bytes()).hexdigest())
    (output / "proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print("QUALITY_PROBE", json.dumps(proof), flush=True)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1:])
