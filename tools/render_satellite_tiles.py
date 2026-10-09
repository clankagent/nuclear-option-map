# /// script
# requires-python = ">=3.11"
# dependencies = ["Pillow>=11,<13"]
# ///
"""Render private Blender scenes into bounded, north-up satellite pyramids.

uv run --script tools/render_satellite_tiles.py --blend scene.blend --output DIR
  --world-width 81920 --world-depth 81920 --max-level 1
Use --benchmark samples.json first (array of {name,x,z,metersPerPixel}).
Only complete disk levels are published, coarse first. For lazy fine tiles use
--serve 47833 --max-level 6: /pyramid.json advertises all requested levels,
/{z}/{x}/{y}.webp waits for a persistent Blender worker and caches the result.
The public browser should use completed static levels, never the lazy service.
Static baking renders bounded 4-by-4 tile chunks and streams their WebP slices.
Rendering defaults to Cycles CPU, two threads and 16 samples, at below-normal
priority, without GPU denoising. A project-wide lock permits one renderer.
The reserved --device gpu option is unavailable without verified total scene
memory/time bounds. Its preflight texture/edge caps alone never authorize GPU
execution. Enhanced scenes use CPU. --chunk-tiles 8 bounds CPU buffers to 4100px
with gutters. Uniform-water reuse requires actual geometry/material/light proof.
Use separate output folders when the scene changes. No game assets are bundled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_safety import (CPU_MAX_EDGE, GPU_MAX_EDGE, configure_scene,
                           lower_process_priority, render_profile, render_slot,
                           scene_evidence, schedule_slot)
from uniform_water import eligible as uniform_water_eligible, copy_verified_chunk

TILE, GUTTER = 512, 2


def chunk_renders(level, width, depth, scratch, output, chunk_tiles=4):
    """Bound render memory while amortizing scene/render setup across 16 tiles."""
    mpp = width / level["width"]
    span = min(chunk_tiles, level["columns"], level["rows"])
    for x in range(0, level["columns"], span):
        for y in range(0, level["rows"], span):
            if all((output / str(level["level"]) / str(tx) / f"{ty}.webp").exists()
                   for tx in range(x, x + span) for ty in range(y, y + span)):
                continue
            yield {
                "x": -width / 2 + (x + span / 2) * TILE * mpp,
                "z": depth / 2 - (y + span / 2) * TILE * mpp,
                "metersPerPixel": mpp, "size": span * TILE + GUTTER * 2,
                "path": str((scratch / f"{level['level']}-{x}-{y}.png").resolve()),
                "column": x, "row": y, "span": span, "level": level["level"],
            }


def slice_chunk(render, output, quality):
    """Preserve overlapping tile gutters; promptly release each temporary PNG."""
    from PIL import Image
    with Image.open(render["path"]) as image:
        for dx in range(render["span"]):
            for dy in range(render["span"]):
                dest = output / str(render["level"]) / str(render["column"] + dx) / f"{render['row'] + dy}.webp"
                dest.parent.mkdir(parents=True, exist_ok=True)
                crop = image.crop((dx * TILE, dy * TILE, (dx + 1) * TILE + GUTTER * 2, (dy + 1) * TILE + GUTTER * 2))
                crop.save(dest.with_suffix(".tmp"), format="WEBP", quality=quality)
                dest.with_suffix(".tmp").replace(dest)
    Path(render["path"]).unlink()


def derive_parent_level(child, parent, output, quality):
    """Downsample bounded neighborhoods, including gutters and Lanczos support."""
    from PIL import Image
    for x in range(parent["columns"]):
        for y in range(parent["rows"]):
            dest = output / str(parent["level"]) / str(x) / f"{y}.webp"
            support = 6  # Lanczos radius 3 at a 2:1 downsampling ratio.
            left, top = x * TILE * 2 - GUTTER * 2 - support, y * TILE * 2 - GUTTER * 2 - support
            edge = (TILE + GUTTER * 2) * 2 + support * 2
            region = Image.new("RGB", (edge, edge))
            for tx in range(math.floor(left / TILE), math.floor((left + edge - 1) / TILE) + 1):
                for ty in range(math.floor(top / TILE), math.floor((top + edge - 1) / TILE) + 1):
                    sx, sy = max(0, min(child["columns"] - 1, tx)), max(0, min(child["rows"] - 1, ty))
                    with Image.open(output / str(child["level"]) / str(sx) / f"{sy}.webp") as source:
                        tile = source.crop((GUTTER, GUTTER, TILE + GUTTER, TILE + GUTTER))
                        if tx != sx:
                            ix = 0 if tx < 0 else TILE - 1
                            tile = tile.crop((ix, 0, ix + 1, TILE)).resize((TILE, TILE))
                        if ty != sy:
                            iy = 0 if ty < 0 else TILE - 1
                            tile = tile.crop((0, iy, TILE, iy + 1)).resize((TILE, TILE))
                        region.paste(tile, (tx * TILE - left, ty * TILE - top))
            dest.parent.mkdir(parents=True, exist_ok=True)
            scaled = region.resize((edge // 2, edge // 2), Image.Resampling.LANCZOS)
            scaled.crop((support // 2, support // 2, edge // 2 - support // 2, edge // 2 - support // 2)).save(dest.with_suffix(".tmp"), format="WEBP", quality=quality)
            dest.with_suffix(".tmp").replace(dest)


DEFAULT_BLENDER = Path(
    "C:/Users/agent/AppData/Local/Programs/AgentTools/Blender/blender-4.5.14-windows-x64/blender-runtime.exe"
)


def level_definition(width, depth, level):
    # Square pixels, exact rectangular world coverage (e.g. Ignus 2:1).
    ratio = width / depth
    if ratio >= 1:
        cols, rows = round(ratio), 1
    else:
        cols, rows = 1, round(1 / ratio)
    if not math.isclose(width / cols, depth / rows, rel_tol=1e-8):
        raise ValueError(
            "World aspect must be an integer or reciprocal integer for square tiles"
        )
    cols *= 2**level
    rows *= 2**level
    return {
        "level": level,
        "width": cols * TILE,
        "height": rows * TILE,
        "columns": cols,
        "rows": rows,
        "urlTemplate": "{z}/{x}/{y}.webp",
    }


def atomic_json(path, value):
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    # Windows readers can briefly deny DELETE sharing during read_text().
    # Keep the old complete receipt intact and retry the atomic replacement.
    for attempt in range(40):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(.05)


def blender_worker(job_path, *, owns_slot=False):
    """Restore scene visibility even when a queued render fails."""
    import bpy

    scene = bpy.context.scene
    visibility = {obj: obj.hide_render for obj in scene.objects}
    previous_camera = scene.camera
    cameras = set(bpy.data.cameras)
    try:
        # The child owns an actual OS lock. Parent death cannot release it while
        # a render is still active. Persistent workers already hold this lock.
        with nullcontext() if owns_slot else render_slot():
            _render_blender_job(job_path)
    finally:
        for obj, hidden in visibility.items():
            obj.hide_render = hidden
        scene.camera = previous_camera
        for camera_data in set(bpy.data.cameras) - cameras:
            for obj in list(scene.objects):
                if obj.type == "CAMERA" and obj.data == camera_data:
                    bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.cameras.remove(camera_data)


def _render_blender_job(job_path):
    import bpy
    from mathutils import Vector

    job = json.loads(Path(job_path).read_text())
    scene = bpy.context.scene
    lower_process_priority()
    profile = job.get("renderProfile", render_profile())
    configure_scene(scene, device=profile["device"], samples=profile["samples"],
                    threads=profile["threads"], images=bpy.data.images,
                    render_edge=max(tile["size"] for tile in job["renders"]))
    original = {o: o.hide_render for o in scene.objects}
    bounds = []
    top = 0
    for obj in scene.objects:
        if (
            obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"}
            and not obj.hide_render
        ):
            points = [obj.matrix_world @ Vector(v) for v in obj.bound_box]
            box = (
                min(v.x for v in points),
                min(v.y for v in points),
                max(v.x for v in points),
                max(v.y for v in points),
            )
            top = max(top, max(v.z for v in points))
            bounds.append((obj, box))
    # Uniform spatial bins; large terrain/water objects remain global.
    cell = job.get("indexCellMeters", 4096)
    bins, global_objects = {}, set()
    for obj, (x0, y0, x1, y1) in bounds:
        a, b, c, d = (
            math.floor(x0 / cell),
            math.floor(y0 / cell),
            math.floor(x1 / cell),
            math.floor(y1 / cell),
        )
        if (c - a + 1) * (d - b + 1) > 256:
            global_objects.add(obj)
        else:
            for x in range(a, c + 1):
                for y in range(b, d + 1):
                    bins.setdefault((x, y), set()).add(obj)
    camera_data = bpy.data.cameras.new("Satellite tile camera")
    camera = bpy.data.objects.new("Satellite tile camera", camera_data)
    scene.collection.objects.link(camera)
    camera.rotation_euler = (0, 0, 0)
    camera_data.type = "ORTHO"
    camera_data.clip_start, camera_data.clip_end = 1, 100000
    scene.camera = camera
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False
    scene.use_nodes = True
    nodes, links = scene.node_tree.nodes, scene.node_tree.links
    nodes.clear()
    layer = nodes.new("CompositorNodeRLayers")
    flip = nodes.new("CompositorNodeFlip")
    flip.axis = "Y"
    composite = nodes.new("CompositorNodeComposite")
    links.new(layer.outputs["Image"], flip.inputs[0])
    links.new(flip.outputs[0], composite.inputs["Image"])
    # Conservative shadow reach from sun angle and maximum scene elevation.
    shadow = job.get("shadowMarginMeters", 2048)
    for light in scene.objects:
        if light.type == "LIGHT" and light.data.type == "SUN":
            direction = light.matrix_world.to_quaternion() @ Vector((0, 0, -1))
            if abs(direction.z) < 0.05:
                shadow = max(shadow, job["worldWidth"], job["worldDepth"])
            else:
                shadow = max(
                    shadow,
                    top * math.hypot(direction.x, direction.y) / abs(direction.z),
                )
    results = []
    water_cache = Path(job["receipt"]).with_name("uniform-water-chunk.png")
    water_signature = None
    water_cache.unlink(missing_ok=True)
    for tile in job["renders"]:
        start = time.perf_counter()
        cx, cy = tile["x"], -tile["z"]
        half = tile["metersPerPixel"] * tile["size"] / 2
        margin = half + shadow
        candidates = set(global_objects)
        for ix in range(
            math.floor((cx - margin) / cell), math.floor((cx + margin) / cell) + 1
        ):
            for iy in range(
                math.floor((cy - margin) / cell), math.floor((cy + margin) / cell) + 1
            ):
                candidates.update(bins.get((ix, iy), ()))
        visible = 0
        visible_geometry = []
        for obj, (x0, y0, x1, y1) in bounds:
            keep = (
                obj in candidates
                and x1 >= cx - margin
                and x0 <= cx + margin
                and y1 >= cy - margin
                and y0 <= cy + margin
            )
            obj.hide_render = not keep
            visible += int(keep)
            if keep:
                visible_geometry.append(obj)
        camera.location = (cx, cy, top + 8000)
        camera_data.ortho_scale = half * 2
        scene.render.resolution_x = scene.render.resolution_y = tile["size"]
        path = Path(tile["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        scene.render.filepath = str(path.resolve())
        proof = None
        signature = None
        reused = False
        if job.get("reuseUniformWater"):
            try:
                proof = _water_snapshot(scene, visible_geometry, (cx-half, cy-half, cx+half, cy+half), shadow, top)
                signature = json.dumps([profile, tile["size"], scene.cycles.seed, scene.frame_current, proof["surfaceSignature"]], sort_keys=True)
                reused = copy_verified_chunk(water_cache, path, proof, cached_signature=water_signature, render_signature=signature)
            except (AttributeError, KeyError, TypeError, ValueError):
                proof = None  # Unknown/unsupported data always renders normally.
        if not reused:
            bpy.ops.render.render(write_still=True)
            if proof and uniform_water_eligible(proof):
                import shutil
                shutil.copyfile(path, water_cache)
                water_signature = signature
        result = {
            **tile,
            "seconds": time.perf_counter() - start,
            "visibleObjects": visible,
            "shadowMarginMeters": shadow,
            "actualScene": scene_evidence(scene),
            "uniformWaterReused": reused,
            "uniformWaterProof": proof if proof and uniform_water_eligible(proof) else None,
        }
        results.append(result)
        atomic_json(Path(job["receipt"]), results)
        print("SATELLITE_TILE " + json.dumps(result), flush=True)
        if job.get("streamParentPid"):
            # Backpressure keeps scratch disk to one bounded chunk, even when
            # encoding is slower than rendering. The consumer removes the PNG.
            deadline = time.monotonic() + 600
            while path.exists():
                if not parent_alive(job["streamParentPid"]):
                    raise RuntimeError("Static bake consumer exited")
                if time.monotonic() > deadline:
                    raise TimeoutError("Static chunk encoding exceeded 10 minutes")
                time.sleep(0.05)
    for obj, hidden in original.items():
        obj.hide_render = hidden
    bpy.data.objects.remove(camera, do_unlink=True)
    bpy.data.cameras.remove(camera_data)
    water_cache.unlink(missing_ok=True)


def _water_snapshot(scene, objects, frame, shadow, top):
    """Inspect actual render-visible data; names/colors never establish eligibility."""
    from mathutils import Vector

    # Reject normal terrain/coastal chunks before traversing mesh vertices or
    # shader graphs. Eligibility needs exactly one four-vertex plane.
    rejected = {"geometry": [], "uniformWorld": False, "orthoDown": False, "surfaceSignature": []}
    if len(objects) != 1:
        return rejected
    candidate = objects[0]
    if (candidate.type != "MESH" or len(candidate.data.vertices) != 4
            or len(candidate.data.polygons) != 1 or len(candidate.data.polygons[0].vertices) != 4):
        return rejected

    def constant_surface(tree, output_type, source_type):
        if tree is None or tree.animation_data:
            return False
        outputs = [n for n in tree.nodes if n.type == output_type and n.is_active_output]
        if len(outputs) != 1:
            return False
        output = outputs[0]
        if any(socket.is_linked for socket in output.inputs if socket.name != "Surface"):
            return False
        surface = output.inputs.get("Surface")
        if surface is None or len(surface.links) != 1:
            return False
        shader = surface.links[0].from_node
        if shader.type != source_type or any(socket.is_linked for socket in shader.inputs):
            return False
        if source_type == "BSDF_PRINCIPLED":
            if (shader.inputs["Alpha"].default_value != 1 or shader.inputs["Transmission Weight"].default_value != 0
                    or shader.inputs["Subsurface Weight"].default_value != 0):
                return False
        return True

    def tree_signature(tree):
        values = []
        for node in tree.nodes:
            inputs = []
            for socket in node.inputs:
                value = getattr(socket, "default_value", None)
                if value is not None and not isinstance(value, (str, bool, int, float)):
                    value = list(value)
                inputs.append((socket.name, value))
            values.append((node.type, inputs))
        return repr(values)

    geometry = []
    volume = any(o.type == "VOLUME" and not o.hide_render for o in scene.objects)
    for obj in objects:
        points = [obj.matrix_world @ v.co for v in obj.data.vertices] if obj.type == "MESH" else []
        quad = len(points) == 4 and len(obj.data.polygons) == 1 and len(obj.data.polygons[0].vertices) == 4
        bounds = [min((p.x for p in points), default=0), min((p.y for p in points), default=0),
                  max((p.x for p in points), default=0), max((p.y for p in points), default=0)]
        horizontal = quad and max(p.z for p in points) - min(p.z for p in points) < 1e-6
        crosses = []
        if quad:
            order = list(obj.data.polygons[0].vertices)
            for i in range(4):
                a, b, c = (points[order[(i + j) % 4]] for j in range(3))
                crosses.append((b.x-a.x)*(c.y-b.y) - (b.y-a.y)*(c.x-b.x))
        convex = bool(crosses) and (all(v > 1e-6 for v in crosses) or all(v < -1e-6 for v in crosses))
        corners = {(round(p.x, 6), round(p.y, 6)) for p in points}
        expected = {(round(x, 6), round(y, 6)) for x in (bounds[0], bounds[2]) for y in (bounds[1], bounds[3])}
        materials = list(obj.data.materials) if obj.type == "MESH" else []
        constant = (len(materials) == 1 and materials[0] is not None and materials[0].use_nodes
                    and not materials[0].animation_data and not obj.modifiers and not obj.animation_data
                    and not obj.data.shape_keys and not obj.particle_systems and getattr(obj, "visible_camera", True)
                    and constant_surface(materials[0].node_tree, "OUTPUT_MATERIAL", "BSDF_PRINCIPLED"))
        geometry.append({"mesh": obj.type == "MESH", "horizontalQuad": horizontal,
                         "convexQuad": convex,
                         "axisAligned": quad and corners == expected,
                         "constantOpaqueMaterial": bool(constant), "bounds": bounds,
                         "height": points[0].z if points else 0})
    lights = []
    for obj in scene.objects:
        if obj.type == "LIGHT" and not obj.hide_render:
            direction = obj.matrix_world.to_quaternion() @ Vector((0, 0, -1))
            lights.append({"type": obj.data.type, "constant": not obj.data.use_nodes and not obj.data.animation_data and not obj.animation_data,
                           "direction": list(direction), "energy": obj.data.energy,
                           "color": list(obj.data.color), "angle": getattr(obj.data, "angle", 0)})
    world = scene.world
    def collection_holdout(collection):
        return collection.holdout or collection.indirect_only or any(collection_holdout(child) for child in collection.children)
    material_override = any(layer.material_override is not None for layer in scene.view_layers)
    holdout = any(getattr(obj, "is_holdout", False) for obj in objects) or any(collection_holdout(layer.layer_collection) for layer in scene.view_layers)
    signature = [int(obj.as_pointer()) for obj in objects] + [int(world.as_pointer()) if world else 0]
    for obj in objects:
        if obj.type == "MESH":
            for mat in obj.data.materials:
                if mat and mat.use_nodes:
                    signature.append((int(mat.as_pointer()), tree_signature(mat.node_tree)))
    if world and world.use_nodes:
        signature.append(tree_signature(world.node_tree))
    signature.append(lights)
    return {"geometry": geometry, "volume": volume, "materialOverride": material_override, "holdout": holdout,
            "uniformWorld": bool(world and world.use_nodes and not world.animation_data and constant_surface(world.node_tree, "OUTPUT_WORLD", "BACKGROUND")),
            "orthoDown": scene.camera.data.type == "ORTHO" and all(abs(v) < 1e-8 for v in scene.camera.rotation_euler),
            "frame": list(frame), "shadowMargin": shadow, "sceneTop": top, "lights": lights,
            "surfaceSignature": signature}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--blend", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--world-width", type=float, required=True)
    p.add_argument("--world-depth", type=float, required=True)
    p.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    p.add_argument("--max-level", type=int, default=0)
    p.add_argument("--device", choices=["cpu", "gpu"], default="cpu",
                   help="CPU default; reserved GPU mode is unavailable without verified total scene/time bounds")
    p.add_argument("--samples", type=int, default=16)
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--tile-budget", type=int, default=64)
    p.add_argument("--chunk-tiles", type=int, choices=[1, 2, 4, 8], default=4,
                   help="Static render chunk edge in tiles (default 2048 pixels)")
    p.add_argument("--derive-parents", action="store_true",
                   help="Render only finest level, derive all parent levels with bounded neighborhoods")
    p.add_argument("--reuse-uniform-water", action="store_true",
                   help="Reuse genuine chunks only after strict actual-scene uniform-plane proof")
    p.add_argument(
        "--serve",
        type=int,
        metavar="PORT",
        help="Serve lazy cached tiles on loopback with persistent Blender",
    )
    p.add_argument("--benchmark", type=Path)
    p.add_argument("--benchmark-size", type=int, choices=[516, 1028, 2052, 4100], default=516,
                   help="Benchmark tile/chunk dimensions including exterior gutters")
    p.add_argument("--quality", type=int, default=88)
    p.add_argument("--shadow-margin-meters", type=float, default=2048)
    args = p.parse_args()
    if args.device == "gpu":
        p.error("GPU disabled: no verified total scene memory/time bound is available; use --device cpu")
    try:
        args.render_profile = render_profile(args.device, args.samples, args.threads)
    except ValueError as exc:
        p.error(str(exc))
    max_edge = GPU_MAX_EDGE if args.device == "gpu" else CPU_MAX_EDGE
    requested_edge = (args.benchmark_size if args.benchmark else
                      TILE + GUTTER * 2 if args.serve else
                      min(args.chunk_tiles, 2 ** args.max_level) * TILE + GUTTER * 2)
    if requested_edge > max_edge:
        p.error(f"{args.device.upper()} render edge {requested_edge} exceeds safety limit {max_edge}; reduce --chunk-tiles/--benchmark-size")
    print(f"Resource-safe renderer: CYCLES {args.device.upper()}, {args.threads} CPU threads, {args.samples} samples; GPU denoising off", flush=True)
    if (
        not all(
            math.isfinite(v)
            for v in (args.world_width, args.world_depth, args.shadow_margin_meters)
        )
        or args.world_width <= 0
        or args.world_depth <= 0
        or args.shadow_margin_meters < 0
        or not 0 <= args.max_level <= 12
    ):
        p.error("Invalid bounds/level/shadow margin")
    if not 1 <= args.quality <= 100 or args.tile_budget < 1:
        p.error("Quality must be 1..100 and tile budget positive")
    if args.serve is not None and not 1 <= args.serve <= 65535:
        p.error("Port must be 1..65535")
    from PIL import Image

    args.output.mkdir(parents=True, exist_ok=True)
    scratch = args.output / ".render-work"
    scratch.mkdir(exist_ok=True)
    provenance = {
        "blend": str(args.blend.resolve()),
        "blendBytes": args.blend.stat().st_size,
        "blendMtimeNs": args.blend.stat().st_mtime_ns,
        "worldWidth": args.world_width,
        "worldDepth": args.world_depth,
        "tileSize": TILE,
        "gutter": GUTTER,
        "quality": args.quality,
        "shadowMarginMeters": args.shadow_margin_meters,
        "renderProfile": args.render_profile,
        "reuseUniformWater": args.reuse_uniform_water,
    }
    source = scratch / "source.json"
    if source.exists():
        previous = json.loads(source.read_text())
        previous.setdefault("reuseUniformWater", False)
        if previous != provenance:
            p.error("Output belongs to different scene/settings; choose a fresh output directory")
    atomic_json(source, provenance)
    levels = [
        level_definition(args.world_width, args.world_depth, z)
        for z in range(args.max_level + 1)
    ]
    args.cache_version = hashlib.sha256(
        json.dumps(provenance, sort_keys=True).encode()
    ).hexdigest()[:16]
    for level in levels:
        level["urlTemplate"] += f"?v={args.cache_version}"
    count = sum(l["columns"] * l["rows"] for l in levels)
    if not args.benchmark and not args.serve and count > args.tile_budget:
        p.error(
            f"{count} tiles exceed budget {args.tile_budget}; benchmark first, then explicitly raise --tile-budget"
        )
    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = str(args.threads)
    env["PATH"] = (
        str(args.blender.parent / "blender.shared") + os.pathsep + env.get("PATH", "")
    )
    if args.serve:
        with schedule_slot():
            serve_tiles(args, levels, scratch, env)
        return

    def render_batch(renders, label, consume=None):
        receipt = scratch / f"{label}-receipt.json"
        jobpath = scratch / f"{label}-job.json"
        if receipt.exists():
            receipt.unlink()
        atomic_json(
            jobpath,
            {
                "worldWidth": args.world_width,
                "worldDepth": args.world_depth,
                "shadowMarginMeters": args.shadow_margin_meters,
                "renders": renders,
                "receipt": str(receipt.resolve()),
                "streamParentPid": os.getpid() if consume else None,
                "renderProfile": args.render_profile,
                "reuseUniformWater": args.reuse_uniform_water,
            },
        )
        with schedule_slot(), (scratch / f"{label}.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [
                    str(args.blender),
                    "--background",
                    str(args.blend.resolve()),
                    "--threads", str(args.threads),
                    "--python-exit-code",
                    "1",
                    "--python",
                    str(Path(__file__).resolve()),
                    "--",
                    "--worker",
                    str(jobpath.resolve()),
                ],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0,
            )
            consumed = 0
            try:
                while True:
                    completed = json.loads(receipt.read_text()) if receipt.exists() else []
                    if consume:
                        for result in completed[consumed:]:
                            consume(result)
                        consumed = len(completed)
                    status = process.poll()
                    if status is not None:
                        # Read again after exit to catch the final atomic receipt update.
                        completed = json.loads(receipt.read_text()) if receipt.exists() else []
                        if consume:
                            for result in completed[consumed:]:
                                consume(result)
                        if status:
                            raise subprocess.CalledProcessError(status, process.args)
                        break
                    time.sleep(0.05)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=20)
        return json.loads(receipt.read_text())

    if args.benchmark:
        samples = json.loads(args.benchmark.read_text())
        import re

        if not isinstance(samples, list) or not samples:
            p.error("Benchmark needs a nonempty sample array")
        for sample in samples:
            if (
                not re.fullmatch(r"[A-Za-z0-9_-]+", sample.get("name", ""))
                or not all(
                    isinstance(sample.get(k), (float, int)) and math.isfinite(sample[k])
                    for k in ("x", "z", "metersPerPixel")
                )
                or sample["metersPerPixel"] <= 0
            ):
                p.error("Invalid benchmark name, coordinates, or pixel scale")
        renders = [
            dict(
                s,
                size=args.benchmark_size,
                path=str((scratch / (s["name"] + ".png")).resolve()),
            )
            for s in samples
        ]
        results = render_batch(renders, "benchmark")
        for r in results:
            with Image.open(r["path"]) as im:
                im.save(args.output / (r["name"] + ".webp"), quality=args.quality)
        mean = sum(r["seconds"] for r in results) / len(results)
        tiles_per_render = ((args.benchmark_size - GUTTER * 2) / TILE) ** 2
        steady = (
            sum(r["seconds"] for r in results[1:]) / max(1, len(results) - 1)
            if len(results) > 1
            else None
        )
        atomic_json(
            args.output / "benchmark.json",
            {
                "samples": results,
                "estimatedTiles": count,
                "estimatedRenderHours": mean * count / tiles_per_render / 3600,
                "steadyEstimatedHours": steady * count / tiles_per_render / 3600
                if steady is not None
                else None,
                "tilesPerRender": tiles_per_render,
                "amortizedSecondsPerTile": mean / tiles_per_render,
                "chunkEstimatedHours": mean * count / tiles_per_render / 3600,
                "firstRenderIncludesShaderWarmup": True,
                "renderProfile": args.render_profile,
                "actualScene": results[0]["actualScene"],
            },
        )
        print(
            json.dumps(
                {
                    "meanSeconds": mean,
                    "tiles": count,
                    "estimatedHours": mean * count / tiles_per_render / 3600,
                },
                indent=2,
            )
        )
        return
    metadata = {
        "schemaVersion": 1,
        "image": f"overview.webp?v={args.cache_version}",
        "tiles": {"tileSize": TILE, "gutter": GUTTER, "levels": []},
    }
    for level in (list(reversed(levels)) if args.derive_parents else levels):
        z = level["level"]
        if args.derive_parents and z < args.max_level:
            derive_parent_level(levels[z + 1], level, args.output, args.quality)
        else:
            renders = list(chunk_renders(level, args.world_width, args.world_depth,
                                         scratch, args.output, args.chunk_tiles))
            if renders:
                render_batch(renders, f"level-{z}", lambda r: slice_chunk(r, args.output, args.quality))
        if z == 0:
            overview = Image.new("RGB", (level["width"], level["height"]))
            for x in range(level["columns"]):
                for y in range(level["rows"]):
                    with Image.open(args.output / "0" / str(x) / f"{y}.webp") as im:
                        overview.paste(
                            im.crop((GUTTER, GUTTER, TILE + GUTTER, TILE + GUTTER)),
                            (x * TILE, y * TILE),
                        )
            overview.save(args.output / "overview.webp", quality=args.quality)
        metadata["tiles"]["levels"].append(level)
        metadata["tiles"]["levels"].sort(key=lambda item: item["level"])
        if not args.derive_parents or z == 0:
            atomic_json(args.output / "pyramid.json", metadata)
        print(
            f"Completed level {z}: {level['columns']} x {level['rows']} tiles",
            flush=True,
        )


def parent_alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        kernel.CloseHandle(handle)


def claim_job(path):
    """Atomically separate cancelable queue entries from active renders."""
    active = path.with_name(path.name.replace(".job.json", ".active.json"))
    try:
        path.rename(active)
    except FileNotFoundError:
        return None
    return active


def cancel_queued_job(path):
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


@contextmanager
def connected_lock(lock, connected):
    while not lock.acquire(timeout=0.1):
        if not connected():
            raise ConnectionAbortedError("Client disconnected while awaiting tile")
    try:
        if not connected():
            raise ConnectionAbortedError("Client disconnected before tile request")
        yield
    finally:
        lock.release()


def socket_connected(connection):
    import select
    import socket

    try:
        ready, _, _ = select.select([connection], [], [], 0)
        return not ready or connection.recv(1, socket.MSG_PEEK) != b""
    except OSError:
        return False


def persistent_worker(queue_dir, parent_pid):
    """Keep the loaded scene alive while owning the actual OS render lock."""
    with render_slot():
        _persistent_worker_loop(queue_dir, parent_pid)


def _persistent_worker_loop(queue_dir, parent_pid):
    queue = Path(queue_dir)
    while not (queue / "stop").exists() and parent_alive(parent_pid):
        pending = sorted(queue.glob("*.job.json"))
        if not pending:
            time.sleep(0.1)
            continue
        for path in pending:
            if (queue / "stop").exists() or not parent_alive(parent_pid):
                return
            active = claim_job(path)
            if active is None:
                continue
            try:
                blender_worker(active, owns_slot=True)
                active.rename(active.with_suffix(".done"))
            except Exception as exc:  # noqa: BLE001 - isolate failures at job/request boundaries
                atomic_json(path.with_suffix(".error"), {"error": str(exc)})
                active.unlink(missing_ok=True)


def serve_tiles(args, levels, scratch, env):
    """Loopback cache service. A request remains pending until its real tile exists."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from PIL import Image

    queue = scratch / "queue"
    queue.mkdir(exist_ok=True)
    for old in queue.glob("*"):
        old.unlink()
    process_log = (scratch / "service-blender.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            str(args.blender),
            "--background",
            str(args.blend.resolve()),
            "--threads", str(args.threads),
            "--python-exit-code",
            "1",
            "--python",
            str(Path(__file__).resolve()),
            "--",
            "--server-worker",
            str(queue.resolve()),
            str(os.getpid()),
        ],
        env=env,
        stdout=process_log,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0,
    )
    locks = {}
    mutex = threading.Lock()

    def obtain(z, x, y, connected):
        key = (z, x, y)
        with mutex:
            lock = locks.setdefault(key, threading.Lock())
        with connected_lock(lock, connected):
            dest = args.output / str(z) / str(x) / f"{y}.webp"
            if dest.exists():
                return dest
            name = f"{time.time_ns()}-{z}-{x}-{y}"
            png = queue / f"{name}.png"
            receipt = queue / f"{name}.receipt.json"
            job = queue / f"{name}.job.json"
            mpp = args.world_width / levels[z]["width"]
            with mutex:
                if len(list(queue.glob("*.job.json"))) >= 32:
                    raise RuntimeError("Tile queue full; retry shortly")
                atomic_json(
                    job,
                    {
                        "worldWidth": args.world_width,
                        "worldDepth": args.world_depth,
                        "shadowMarginMeters": args.shadow_margin_meters,
                        "renderProfile": args.render_profile,
                        "reuseUniformWater": args.reuse_uniform_water,
                        "receipt": str(receipt.resolve()),
                        "renders": [
                            {
                                "x": -args.world_width / 2 + (x + 0.5) * TILE * mpp,
                                "z": args.world_depth / 2 - (y + 0.5) * TILE * mpp,
                                "metersPerPixel": mpp,
                                "size": TILE + GUTTER * 2,
                                "path": str(png.resolve()),
                            }
                        ],
                    },
                )
            deadline = time.monotonic() + 600
            while not receipt.exists():
                if not connected() and cancel_queued_job(job):
                    raise ConnectionAbortedError("Canceled obsolete queued tile")
                if process.poll() is not None:
                    raise RuntimeError("Blender worker exited; inspect service log")
                error = job.with_suffix(".error")
                if error.exists():
                    raise RuntimeError(error.read_text())
                if time.monotonic() > deadline:
                    cancel_queued_job(job)
                    raise TimeoutError("Tile queue exceeded 10 minutes")
                time.sleep(0.1)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(png) as im:
                im.save(dest.with_suffix(".tmp"), format="WEBP", quality=args.quality)
            dest.with_suffix(".tmp").replace(dest)
            png.unlink()
            return dest

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            import re

            path = self.path.split("?", 1)[0]
            try:
                if path == "/pyramid.json":
                    body = json.dumps(
                        {
                            "schemaVersion": 1,
                            "image": f"overview.webp?v={args.cache_version}",
                            "tiles": {
                                "tileSize": TILE,
                                "gutter": GUTTER,
                                "levels": levels,
                            },
                        }
                    ).encode()
                    content = "application/json"
                elif path == "/overview.webp":
                    overview = args.output / "overview.webp"
                    if not overview.exists():
                        level = levels[0]
                        im = Image.new("RGB", (level["width"], level["height"]))
                        for x in range(level["columns"]):
                            for y in range(level["rows"]):
                                with Image.open(
                                    obtain(
                                        0,
                                        x,
                                        y,
                                        lambda: socket_connected(self.connection),
                                    )
                                ) as tile:
                                    im.paste(
                                        tile.crop(
                                            (
                                                GUTTER,
                                                GUTTER,
                                                TILE + GUTTER,
                                                TILE + GUTTER,
                                            )
                                        ),
                                        (x * TILE, y * TILE),
                                    )
                        im.save(overview, quality=args.quality)
                    body = overview.read_bytes()
                    content = "image/webp"
                else:
                    match = re.fullmatch(r"/(\d+)/(\d+)/(\d+)\.webp", path)
                    if not match:
                        self.send_error(404)
                        return
                    z, x, y = map(int, match.groups())
                    if (
                        z >= len(levels)
                        or x >= levels[z]["columns"]
                        or y >= levels[z]["rows"]
                    ):
                        self.send_error(404)
                        return
                    body = obtain(
                        z, x, y, lambda: socket_connected(self.connection)
                    ).read_bytes()
                    content = "image/webp"
                self.send_response(200)
                self.send_header("Content-Type", content)
                self.send_header("Content-Length", str(len(body)))
                self.send_header(
                    "Cache-Control",
                    "public, max-age=86400" if content == "image/webp" else "no-cache",
                )
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception as exc:  # noqa: BLE001 - isolate failures at job/request boundaries
                self.send_error(503, str(exc))

    server = ThreadingHTTPServer(("127.0.0.1", args.serve), Handler)
    print(
        f"Satellite tile cache listening on http://127.0.0.1:{args.serve}/; {len(levels)} lazy levels",
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        (queue / "stop").touch()
        server.server_close()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.terminate()
        process_log.close()


if __name__ == "__main__":
    if "--server-worker" in sys.argv:
        persistent_worker(
            sys.argv[sys.argv.index("--server-worker") + 1],
            int(sys.argv[sys.argv.index("--server-worker") + 2]),
        )
    elif "--worker" in sys.argv:
        blender_worker(sys.argv[sys.argv.index("--worker") + 1])
    else:
        main()
