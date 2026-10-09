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
Bind is loopback only. Keep parent coverage visible while HTTP requests wait.
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
from contextlib import contextmanager
from pathlib import Path

TILE, GUTTER = 512, 2
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
    temp.replace(path)


def blender_worker(job_path):
    """Restore scene visibility even when a queued render fails."""
    import bpy

    scene = bpy.context.scene
    visibility = {obj: obj.hide_render for obj in scene.objects}
    previous_camera = scene.camera
    cameras = set(bpy.data.cameras)
    try:
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
    scene.render.engine = "BLENDER_EEVEE_NEXT"
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
        camera.location = (cx, cy, top + 8000)
        camera_data.ortho_scale = half * 2
        scene.render.resolution_x = scene.render.resolution_y = tile["size"]
        path = Path(tile["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        scene.render.filepath = str(path.resolve())
        bpy.ops.render.render(write_still=True)
        result = {
            **tile,
            "seconds": time.perf_counter() - start,
            "visibleObjects": visible,
            "shadowMarginMeters": shadow,
        }
        results.append(result)
        atomic_json(Path(job["receipt"]), results)
        print("SATELLITE_TILE " + json.dumps(result), flush=True)
    for obj, hidden in original.items():
        obj.hide_render = hidden
    bpy.data.objects.remove(camera, do_unlink=True)
    bpy.data.cameras.remove(camera_data)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--blend", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--world-width", type=float, required=True)
    p.add_argument("--world-depth", type=float, required=True)
    p.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    p.add_argument("--max-level", type=int, default=0)
    p.add_argument("--tile-budget", type=int, default=64)
    p.add_argument(
        "--serve",
        type=int,
        metavar="PORT",
        help="Serve lazy cached tiles on loopback with persistent Blender",
    )
    p.add_argument("--benchmark", type=Path)
    p.add_argument("--quality", type=int, default=88)
    p.add_argument("--shadow-margin-meters", type=float, default=2048)
    args = p.parse_args()
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
    }
    source = scratch / "source.json"
    if source.exists() and json.loads(source.read_text()) != provenance:
        p.error(
            "Output belongs to different scene/settings; choose a fresh output directory"
        )
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
    env["PATH"] = (
        str(args.blender.parent / "blender.shared") + os.pathsep + env.get("PATH", "")
    )
    if args.serve:
        serve_tiles(args, levels, scratch, env)
        return

    def render_batch(renders, label):
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
            },
        )
        with (scratch / f"{label}.log").open("w", encoding="utf-8") as log:
            subprocess.run(
                [
                    str(args.blender),
                    "--background",
                    str(args.blend.resolve()),
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
                check=True,
            )
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
                size=TILE + GUTTER * 2,
                path=str((scratch / (s["name"] + ".png")).resolve()),
            )
            for s in samples
        ]
        results = render_batch(renders, "benchmark")
        for r in results:
            with Image.open(r["path"]) as im:
                im.save(args.output / (r["name"] + ".webp"), quality=args.quality)
        mean = sum(r["seconds"] for r in results) / len(results)
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
                "estimatedRenderHours": mean * count / 3600,
                "steadyEstimatedHours": steady * count / 3600
                if steady is not None
                else None,
                "firstRenderIncludesShaderWarmup": True,
            },
        )
        print(
            json.dumps(
                {
                    "meanSeconds": mean,
                    "tiles": count,
                    "estimatedHours": mean * count / 3600,
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
    for level in levels:
        z = level["level"]
        mpp = args.world_width / level["width"]
        renders = []
        for x in range(level["columns"]):
            for y in range(level["rows"]):
                destination = args.output / str(z) / str(x) / f"{y}.webp"
                if destination.exists():
                    continue
                renders.append(
                    {
                        "x": -args.world_width / 2 + (x + 0.5) * TILE * mpp,
                        "z": args.world_depth / 2 - (y + 0.5) * TILE * mpp,
                        "metersPerPixel": mpp,
                        "size": TILE + GUTTER * 2,
                        "path": str((scratch / f"{z}-{x}-{y}.png").resolve()),
                        "column": x,
                        "row": y,
                    }
                )
        results = render_batch(renders, f"level-{z}") if renders else []
        for r in results:
            dest = args.output / str(z) / str(r["column"]) / f"{r['row']}.webp"
            dest.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(r["path"]) as im:
                im.save(dest.with_suffix(".tmp"), format="WEBP", quality=args.quality)
            dest.with_suffix(".tmp").replace(dest)
            Path(r["path"]).unlink()
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
        atomic_json(args.output / "pyramid.json", metadata)
        print(
            f"Published complete level {z}: {level['columns']} x {level['rows']} tiles",
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
    """Keep the loaded scene and EEVEE shader cache alive between requests."""
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
                blender_worker(active)
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
