"""Resource guards with fake scenes/images; no Blender or GPU is started."""

import importlib.util
import tempfile
import subprocess
import sys
import os
import signal
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as Obj

SPEC = importlib.util.spec_from_file_location("render_safety", Path(__file__).parents[1] / "tools/render_safety.py")
safety = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(safety)


def scene():
    return Obj(render=Obj(engine="BLENDER_EEVEE_NEXT", threads=99,
                          threads_mode="AUTO", compositor_device="GPU"),
               cycles=Obj(device="GPU", use_denoising=True, use_preview_denoising=True, time_limit=1),
               view_layers=[Obj(cycles=Obj(use_denoising=True))])


def image(width, height, name="texture", users=1):
    return Obj(size=(width, height), name=name, users=users, type="IMAGE")


class RenderSafetyTests(unittest.TestCase):
    def test_default_configuration_is_cpu_and_bounded(self):
        target = scene()
        profile = safety.configure_scene(target, render_edge=2052)
        self.assertEqual(profile["device"], "cpu")
        self.assertEqual(target.render.engine, "CYCLES")
        self.assertEqual(target.cycles.device, "CPU")
        self.assertEqual(target.render.threads, 2)
        self.assertEqual(target.cycles.samples, 16)
        self.assertEqual(target.cycles.time_limit, 0)
        self.assertFalse(target.cycles.use_denoising)
        self.assertFalse(target.cycles.use_preview_denoising)
        self.assertFalse(target.view_layers[0].cycles.use_denoising)
        self.assertEqual(target.render.compositor_device, "CPU")

    def test_gpu_rejects_final_ground_textures_before_scene_configuration(self):
        target = scene()
        with self.assertRaisesRegex(ValueError, "exceeds 2048"):
            safety.configure_scene(target, device="gpu", images=[image(5470, 5470)], render_edge=516,
                                   gpu_scene_bound_bytes=safety.GPU_SCENE_BUDGET, gpu_time_limit_seconds=1)
        self.assertEqual(target.render.engine, "BLENDER_EEVEE_NEXT")

    def test_gpu_rejects_aggregate_float_mip_budget(self):
        with self.assertRaisesRegex(ValueError, "decoded texture budget"):
            safety.validate_gpu_budget([image(2048, 2048) for _ in range(4)], 516,
                                       scene_bound_bytes=safety.GPU_SCENE_BUDGET, time_limit_seconds=1)

    def test_gpu_accepts_only_small_explicit_budget(self):
        self.assertEqual(safety.validate_gpu_budget([image(1024, 1024)], 1028,
                                                  scene_bound_bytes=safety.GPU_SCENE_BUDGET, time_limit_seconds=1), int(1024 * 1024 * 16 * 4 / 3))
        with self.assertRaisesRegex(ValueError, "render edge"):
            safety.validate_gpu_budget([], 2052, scene_bound_bytes=safety.GPU_SCENE_BUDGET, time_limit_seconds=1)

    def test_gpu_fails_closed_without_total_scene_and_time_bounds(self):
        with self.assertRaisesRegex(ValueError, "GPU disabled"):
            safety.configure_scene(scene(), device="gpu", images=[], render_edge=516)

    def test_gpu_bound_covers_buffers_not_only_textures(self):
        with self.assertRaisesRegex(ValueError, "render buffers"):
            safety.validate_gpu_budget([], 1028, scene_bound_bytes=1024 * 1024, time_limit_seconds=1)
        with self.assertRaisesRegex(ValueError, "time bound"):
            safety.validate_gpu_budget([], 516, scene_bound_bytes=safety.GPU_SCENE_BUDGET, time_limit_seconds=2)

    def test_profile_rejects_excessive_threads_or_samples(self):
        with self.assertRaises(ValueError):
            safety.render_profile(threads=3)
        with self.assertRaises(ValueError):
            safety.render_profile(samples=64)

    def test_cpu_render_memory_edge_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "bound memory"):
            safety.configure_scene(scene(), render_edge=8192)

    def test_slot_excludes_another_owner_and_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "renderer.lock"
            with safety.render_slot(path):
                with self.assertRaisesRegex(RuntimeError, "safety lock"):
                    with safety.render_slot(path):
                        self.fail("Second renderer acquired the slot")
            with safety.render_slot(path):
                pass

    def test_slot_excludes_a_separate_process(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "renderer.lock"
            code = ("import sys; from pathlib import Path; "
                    f"sys.path.insert(0, {str(Path(SPEC.origin).parent)!r}); "
                    "from render_safety import render_slot; "
                    f"slot=render_slot(Path({str(path)!r})); slot.__enter__()")
            with safety.render_slot(path):
                result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("resource safety lock", result.stderr)
            result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_worker_lock_survives_forced_parent_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock, marker = root / "render.lock", root / "worker.pid"
            imports = f"import sys; sys.path.insert(0, {str(Path(SPEC.origin).parent)!r}); "
            worker = imports + ("from render_safety import render_slot; from pathlib import Path; import os,time; "
                                f"slot=render_slot(Path({str(lock)!r})); slot.__enter__(); "
                                f"Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)")
            launcher = imports + ("from render_safety import schedule_slot; import subprocess; from pathlib import Path; "
                                  f"slot=schedule_slot(Path({str(root / 'schedule.lock')!r})); slot.__enter__(); "
                                  f"child=subprocess.Popen([sys.executable,'-c',{worker!r}]); child.wait()")
            parent = subprocess.Popen([sys.executable, "-c", launcher], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            worker_pid = None
            try:
                deadline = time.monotonic() + 10
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(marker.exists(), "Worker did not acquire its lock")
                worker_pid = int(marker.read_text())
                parent.terminate()
                parent.wait(timeout=10)
                with self.assertRaisesRegex(RuntimeError, "resource safety lock"):
                    with safety.render_slot(lock):
                        self.fail("Parent termination released the active worker's lock")
            finally:
                if parent.poll() is None:
                    parent.terminate()
                    parent.wait(timeout=10)
                if worker_pid is not None:
                    os.kill(worker_pid, signal.SIGTERM)
                    # Wait until the OS releases the worker-owned lock.
                    deadline = time.monotonic() + 10
                    while True:
                        try:
                            with safety.render_slot(lock):
                                break
                        except RuntimeError:
                            if time.monotonic() > deadline:
                                raise
                            time.sleep(.05)


if __name__ == "__main__":
    unittest.main()
