"""Resource limits for offline renders; importing this module never starts Blender."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DEVICE = "cpu"
DEFAULT_SAMPLES = 16
DEFAULT_THREADS = 2
GPU_MAX_EDGE = 1028
GPU_MAX_TEXTURE_EDGE = 2048
GPU_TEXTURE_BUDGET = 256 * 1024 * 1024
GPU_SCENE_BUDGET = 512 * 1024 * 1024
GPU_MAX_SECONDS = 1.0
CPU_MAX_EDGE = 4100  # 4096px chunk + two 2px exterior gutters.
CPU_QUALITY_MAX_SAMPLES = 256  # Explicit, single-view quality comparisons only.
GPU_TRIAL_MAX_EDGE = 256
GPU_TRIAL_MAX_TRIANGLES = 50_000
GPU_TRIAL_TEXTURE_BUDGET = 32 * 1024 * 1024
GPU_TRIAL_STATES_FACTOR = "0.000001"
LOCK_PATH = Path(__file__).resolve().parents[1] / "work" / "render-safety.lock"
SCHEDULE_LOCK_PATH = LOCK_PATH.with_name("render-scheduler.lock")


def render_profile(device=DEFAULT_DEVICE, samples=DEFAULT_SAMPLES, threads=DEFAULT_THREADS, *, quality_cpu=False):
    if device not in {"cpu", "gpu"}:
        raise ValueError("Render device must be cpu or gpu")
    if quality_cpu and device != "cpu":
        raise ValueError("High-quality sample allowance is CPU-only")
    sample_limit = CPU_QUALITY_MAX_SAMPLES if quality_cpu else 16
    if not isinstance(samples, int) or not 1 <= samples <= sample_limit:
        raise ValueError(f"Render samples must be 1..{sample_limit}")
    if not isinstance(threads, int) or not 1 <= threads <= 2:
        raise ValueError("Render CPU threads must be 1..2")
    return {"engine": "CYCLES", "device": device, "samples": samples,
            "threads": threads, "denoising": False, "compositorDevice": "CPU"}


def validate_gpu_budget(images, render_edge, *, scene_bound_bytes=None, time_limit_seconds=None):
    """Conservative float32 RGBA + mip storage; no hardware probing/allocation."""
    if scene_bound_bytes is None or time_limit_seconds is None:
        raise ValueError("GPU disabled without an explicit verified total scene memory bound and render time bound; use CPU")
    if not isinstance(scene_bound_bytes, int) or not 0 < scene_bound_bytes <= GPU_SCENE_BUDGET:
        raise ValueError(f"GPU total scene bound must be 1..{GPU_SCENE_BUDGET} bytes")
    if not 0 < time_limit_seconds <= GPU_MAX_SECONDS:
        raise ValueError(f"GPU render time bound must be positive and <= {GPU_MAX_SECONDS} second")
    if render_edge is None or render_edge > GPU_MAX_EDGE or render_edge < 1:
        raise ValueError(f"GPU render edge must be 1..{GPU_MAX_EDGE} pixels")
    total = 0
    for image in images:
        if not getattr(image, "users", 0) or getattr(image, "type", "IMAGE") in {"RENDER_RESULT", "COMPOSITING"}:
            continue
        width, height = image.size[:2]
        if max(width, height) > GPU_MAX_TEXTURE_EDGE:
            raise ValueError(f"GPU refused texture {image.name!r}: {width}x{height} exceeds {GPU_MAX_TEXTURE_EDGE}")
        # Count even byte-backed textures as float32, including a full mip chain.
        total += int(width * height * 4 * 4 * 4 / 3)
        if total > GPU_TEXTURE_BUDGET:
            raise ValueError(f"GPU refused decoded texture budget: {total} bytes exceeds {GPU_TEXTURE_BUDGET}")
    # Reserve 16 float32 RGBA render/scratch buffers. The explicit caller bound
    # must additionally cover geometry/BVH/kernel storage; images alone cannot
    # prove a whole scene fits. No current CLI supplies that verified bound.
    minimum = total + render_edge * render_edge * 16 * 16
    if minimum > scene_bound_bytes:
        raise ValueError("GPU total scene bound is smaller than textures and conservative render buffers")
    return total


def configure_scene(scene, *, device=DEFAULT_DEVICE, samples=DEFAULT_SAMPLES,
                    threads=DEFAULT_THREADS, images=(), render_edge=None,
                    gpu_scene_bound_bytes=None, gpu_time_limit_seconds=None,
                    quality_cpu=False):
    """Call before render setup. Pass bpy.data.images for an explicit GPU request."""
    profile = render_profile(device, samples, threads, quality_cpu=quality_cpu)
    if render_edge is not None and (render_edge < 1 or render_edge > CPU_MAX_EDGE):
        raise ValueError(f"Render edge must be 1..{CPU_MAX_EDGE} pixels to bound memory")
    if device == "gpu":
        validate_gpu_budget(images, render_edge, scene_bound_bytes=gpu_scene_bound_bytes,
                            time_limit_seconds=gpu_time_limit_seconds)
    scene.render.engine = profile["engine"]
    scene.cycles.device = device.upper()
    scene.cycles.samples = samples
    scene.cycles.time_limit = gpu_time_limit_seconds if device == "gpu" else 0
    scene.cycles.use_denoising = False
    if hasattr(scene.cycles, "use_preview_denoising"):
        scene.cycles.use_preview_denoising = False
    if hasattr(scene.render, "compositor_device"):
        scene.render.compositor_device = "CPU"
    scene.render.threads_mode = "FIXED"
    scene.render.threads = threads
    # Also clear saved view-layer denoising flags; CPU execution never uses a
    # GPU denoiser inherited from a .blend file.
    for layer in getattr(scene, "view_layers", ()):
        if hasattr(layer, "cycles") and hasattr(layer.cycles, "use_denoising"):
            layer.cycles.use_denoising = False
    return profile


def guarded_render(scene=None, *, samples=DEFAULT_SAMPLES, threads=DEFAULT_THREADS, quality_cpu=False):
    """CPU-only guarded one-shot helper for standalone Blender tools."""
    import bpy
    scene = scene or bpy.context.scene
    edge = max(scene.render.resolution_x, scene.render.resolution_y) * scene.render.resolution_percentage / 100
    with render_slot():
        lower_process_priority()
        configure_scene(scene, samples=samples, threads=threads,
                        images=bpy.data.images, render_edge=edge, quality_cpu=quality_cpu)
        return bpy.ops.render.render(write_still=True)


def validate_cropped_gpu_trial(inventory, images, width, height, samples):
    """Bound a physically cropped experiment, not unknown driver memory/time.

    Caller inventories the actual loaded scene before CUDA initialization. This
    separate profile deliberately never treats an estimated peak as a verified
    whole-device cap. General GPU rendering remains closed above.
    """
    if any(not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= GPU_TRIAL_MAX_EDGE
           for v in (width, height)):
        raise ValueError("Cropped GPU trial output must be 1..256 pixels per edge")
    if not isinstance(samples, int) or isinstance(samples, bool) or not 1 <= samples <= 16:
        raise ValueError("Cropped GPU trial samples must be 1..16")
    if inventory.get("physicallyCropped") is not True:
        raise ValueError("GPU trial requires physically cropped geometry and textures")
    triangles = inventory.get("triangles")
    if not isinstance(triangles, int) or not 0 < triangles <= GPU_TRIAL_MAX_TRIANGLES:
        raise ValueError("Cropped GPU trial requires 1..50000 triangles")
    for field, limit in (("vertices", 150_000), ("loops", 150_000), ("attributeBytesConservative", 32 * 1024 * 1024)):
        if not isinstance(inventory.get(field), int) or not 0 <= inventory[field] <= limit:
            raise ValueError(f"Cropped GPU trial {field} exceeds {limit}")
    if inventory.get("unsupported"):
        raise ValueError(f"Unsupported GPU trial inputs: {inventory['unsupported']}")
    if not isinstance(inventory.get("shaderNodes"), int) or not 0 <= inventory["shaderNodes"] <= 1024:
        raise ValueError("Cropped GPU trial shader graph exceeds 1024 nodes")
    factor_text = os.environ.get("CYCLES_CONCURRENT_STATES_FACTOR", "")
    try:
        factor = float(factor_text)
    except ValueError:
        factor = 0
    if not 0 < factor <= 1/128:
        raise ValueError("Cropped GPU trial requires the bounded CUDA path queue environment")
    texture_bytes = 0
    for image in images:
        if not getattr(image, "users", 0) or getattr(image, "type", "IMAGE") in {"RENDER_RESULT", "COMPOSITING"}:
            continue
        w, h = image.size[:2]
        if min(w, h) < 1 or max(w, h) > 1024:
            raise ValueError(f"Cropped GPU trial texture {image.name!r} exceeds 1024 pixels")
        texture_bytes += (w * h * 64 + 2) // 3
    if texture_bytes > GPU_TRIAL_TEXTURE_BUDGET:
        raise ValueError("Cropped GPU trial textures exceed 32 MiB conservative decoded storage")
    return {"textureBytesConservative": texture_bytes,
            "renderBufferReserveBytes": width * height * 256,
            "geometryReserveBytes": triangles * 1024,
            "maxPrimaryPaths": width * height * samples,
            "queueStatesFactor": factor_text,
            "queueStatesLimit": 8192,
            "unknownDriverAndKernelOverhead": True,
            "hardDeviceMemoryCap": False, "hardGpuDeadline": False}


def cuda_queue_plan(multiprocessors, threads_per_multiprocessor, target_states):
    """Match Cycles' float32 arithmetic; caller queries the selected device."""
    import struct
    if any(not isinstance(v, int) or v <= 0 for v in (multiprocessors, threads_per_multiprocessor)):
        raise ValueError("CUDA attributes must be positive integers")
    if target_states not in (1024, 8192):
        raise ValueError("CUDA trial queue target must be 1024 or 8192")
    default = max(multiprocessors * threads_per_multiprocessor, 65536) * 16
    f32 = lambda value: struct.unpack("f", struct.pack("f", value))[0]
    factor = f32((target_states-1) / default)
    expected = max(int(f32(default * factor)), 1024)
    if expected > target_states:
        raise ValueError("Rounded CUDA queue factor exceeds target")
    return {"multiprocessors": multiprocessors, "threadsPerMultiprocessor": threads_per_multiprocessor,
            "defaultStates": default, "factor": format(factor, ".17g"),
            "expectedStates": expected, "targetStates": target_states}


def configure_cropped_gpu_trial(scene, inventory, images, *, samples=1):
    evidence = validate_cropped_gpu_trial(inventory, images, scene.render.resolution_x,
                                          scene.render.resolution_y, samples)
    configure_scene(scene, samples=samples, threads=2, render_edge=GPU_TRIAL_MAX_EDGE)
    scene.cycles.device = "GPU"
    scene.cycles.time_limit = 1.0  # Sampling only; never a whole-job guarantee.
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.max_bounces = 4
    scene.cycles.diffuse_bounces = 2
    scene.cycles.glossy_bounces = 2
    scene.cycles.transmission_bounces = 2
    scene.cycles.transparent_max_bounces = 4
    scene.cycles.volume_bounces = 0
    scene.cycles.caustics_reflective = False
    scene.cycles.caustics_refractive = False
    scene.render.use_persistent_data = False
    if hasattr(scene.render, "use_motion_blur"):
        scene.render.use_motion_blur = False
    return evidence


def comparison_subset(offset, count, total=128):
    """An exact contiguous sample subset; each GPU invocation remains <=16."""
    if any(not isinstance(v, int) or isinstance(v, bool) for v in (offset, count, total)):
        raise ValueError("Sample subset values must be integers")
    if not 1 <= total <= 128 or not 1 <= count <= 16 or not 0 <= offset < total or offset + count > total:
        raise ValueError("Comparison requires total<=128 and a nonempty <=16 sample subset within total")
    return {"sampleOffset": offset, "sampleSubsetLength": count, "sampleSequenceTotal": total}


def scene_evidence(scene):
    """Read configured execution settings for receipts; never probes hardware."""
    return {
        "engine": scene.render.engine, "device": scene.cycles.device,
        "samples": scene.cycles.samples, "threadsMode": scene.render.threads_mode,
        "timeLimitSeconds": scene.cycles.time_limit,
        "threads": scene.render.threads, "denoising": scene.cycles.use_denoising,
        "previewDenoising": getattr(scene.cycles, "use_preview_denoising", False),
        "layerDenoising": [getattr(getattr(layer, "cycles", None), "use_denoising", False)
                           for layer in getattr(scene, "view_layers", ())],
        "compositorDevice": getattr(scene.render, "compositor_device", "CPU-only legacy compositor"),
        "width": scene.render.resolution_x, "height": scene.render.resolution_y,
        "resolutionPercentage": scene.render.resolution_percentage,
    }


def lower_process_priority():
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000):  # BELOW_NORMAL_PRIORITY_CLASS
            raise OSError(ctypes.get_last_error(), "Unable to lower renderer priority")
    else:
        os.nice(10)


@contextmanager
def render_slot(path=LOCK_PATH):
    """Nonblocking OS lock, released on process death; one renderer per project."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another map renderer holds the resource safety lock; finish or stop it before rendering") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def schedule_slot(path=SCHEDULE_LOCK_PATH):
    """Parent scheduling gate; the Blender child separately owns render_slot."""
    return render_slot(path)
