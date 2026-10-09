"""Queue ownership and disconnect behavior, without Blender or game assets."""

import importlib.util
import json
import socket
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "satellite_tiles", Path(__file__).parents[1] / "tools/render_satellite_tiles.py"
)
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


class SatelliteQueueTests(unittest.TestCase):
    def test_receipt_read_retries_transient_windows_worker_lock(self):
        path = Path("receipt.json")
        with mock.patch.object(Path, "read_text", side_effect=[
            PermissionError("Worker temporarily denies reading"), '[{"seconds": 1}]'
        ]) as read, mock.patch.object(renderer.time, "sleep") as sleep:
            self.assertEqual(renderer.read_receipt(path), [{"seconds": 1}])
        self.assertEqual(read.call_count, 2)
        sleep.assert_called_once_with(.05)

    def test_receipt_read_persistent_permission_error_is_bounded(self):
        error = PermissionError("Receipt remains inaccessible")
        with mock.patch.object(Path, "read_text", side_effect=error) as read, \
                mock.patch.object(renderer.time, "sleep") as sleep:
            with self.assertRaises(PermissionError) as caught:
                renderer.read_receipt(Path("receipt.json"), missing_ok=True)
        self.assertIs(caught.exception, error)
        self.assertEqual(read.call_count, 40)
        self.assertEqual(sleep.call_count, 39)

    def test_missing_initial_receipt_returns_empty_without_exists_check(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            with mock.patch.object(Path, "exists", side_effect=AssertionError("Racy existence check")):
                self.assertEqual(renderer.read_receipt(path, missing_ok=True), [])
                with self.assertRaises(FileNotFoundError):
                    renderer.read_receipt(path)

    def test_receipt_read_does_not_retry_or_hide_corruption(self):
        with mock.patch.object(Path, "read_text", return_value='[{"broken":') as read, \
                mock.patch.object(renderer.time, "sleep") as sleep:
            with self.assertRaises(json.JSONDecodeError):
                renderer.read_receipt(Path("receipt.json"), missing_ok=True)
        self.assertEqual(read.call_count, 1)
        sleep.assert_not_called()

    def test_atomic_receipt_retries_transient_windows_reader_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            path.write_text('{"old":true}')
            original = Path.replace
            attempts = []
            def locked_once(source, destination):
                attempts.append(1)
                if len(attempts) == 1:
                    self.assertEqual(path.read_text(), '{"old":true}')
                    raise PermissionError("Reader temporarily denies replacement")
                return original(source, destination)
            with mock.patch.object(Path, "replace", locked_once), mock.patch.object(renderer.time, "sleep"):
                renderer.atomic_json(path, {"new": True})
            self.assertEqual(len(attempts), 2)
            self.assertEqual(path.read_text(), '{\n  "new": true\n}')

    def test_parent_derivation_downsamples_children_and_extends_world_edges(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for x in range(2):
                for y in range(2):
                    dest = root / "1" / str(x) / f"{y}.webp"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    Image.new("RGB", (516, 516), (x * 200, y * 200, 80)).save(dest, lossless=True)
            renderer.derive_parent_level(renderer.level_definition(81920, 81920, 1),
                                         renderer.level_definition(81920, 81920, 0), root, 100)
            with Image.open(root / "0/0/0.webp") as image:
                self.assertEqual(image.size, (516, 516))
                self.assertLess(max(abs(a-b) for a,b in zip(image.getpixel((0, 0)), (0, 0, 80))), 4)
                self.assertLess(max(abs(a-b) for a,b in zip(image.getpixel((515, 515)), (200, 200, 80))), 4)

    def test_chunk_geometry_covers_rectangular_world_without_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            level = renderer.level_definition(163840, 81920, 3)
            chunks = list(renderer.chunk_renders(level, 163840, 81920, root, root))
            self.assertEqual(len(chunks), 8)
            self.assertEqual(chunks[0]["size"], 2052)
            self.assertEqual(chunks[0]["x"], -61440)
            self.assertEqual(chunks[0]["z"], 20480)
            covered = {(r["column"] + dx, r["row"] + dy) for r in chunks
                       for dx in range(r["span"]) for dy in range(r["span"])}
            self.assertEqual(len(covered), 128)

    def test_chunk_slicing_retains_matching_internal_gutters(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "chunk.png"
            image = Image.new("RGB", (1028, 1028))
            # Coordinates give a distinct edge, checked after lossy WebP encoding.
            for x in range(1028):
                for y in range(1028):
                    image.putpixel((x, y), (x % 256, y % 256, 0))
            image.save(source)
            renderer.slice_chunk({"path": str(source), "level": 1,
                                  "column": 0, "row": 0, "span": 2}, root, 100)
            with Image.open(root / "1/0/0.webp") as left, Image.open(root / "1/1/0.webp") as right:
                self.assertEqual(left.size, (516, 516))
                # Compare the four source pixels shared by neighboring gutters.
                self.assertLess(max(abs(a-b) for a,b in zip(left.getpixel((513, 100)), right.getpixel((1, 100)))), 5)
            self.assertFalse(source.exists())

    def test_cancel_before_claim_prevents_render(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory) / "tile.job.json"
            job.write_text("{}")
            self.assertTrue(renderer.cancel_queued_job(job))
            self.assertIsNone(renderer.claim_job(job))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_claim_before_cancel_retains_active_render(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory) / "tile.job.json"
            job.write_text('{"tile":"requested"}')
            active = renderer.claim_job(job)
            self.assertFalse(renderer.cancel_queued_job(job))
            self.assertEqual(active.name, "tile.active.json")
            self.assertEqual(active.read_text(), '{"tile":"requested"}')

    def test_socket_disconnect_is_detected_without_consuming_data(self):
        local, remote = socket.socketpair()
        try:
            self.assertTrue(renderer.socket_connected(local))
            remote.sendall(b"x")
            self.assertTrue(renderer.socket_connected(local))
            self.assertEqual(local.recv(1), b"x")
            remote.close()
            self.assertFalse(renderer.socket_connected(local))
        finally:
            local.close()
            remote.close()

    def test_duplicate_wait_aborts_without_releasing_owner_lock(self):
        lock = threading.Lock()
        lock.acquire()
        try:
            with (
                self.assertRaises(ConnectionAbortedError),
                renderer.connected_lock(lock, lambda: False),
            ):
                self.fail("Disconnected waiter acquired an active render")
            self.assertTrue(lock.locked())
        finally:
            lock.release()


if __name__ == "__main__":
    unittest.main()
