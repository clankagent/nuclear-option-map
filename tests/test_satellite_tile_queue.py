"""Queue ownership and disconnect behavior, without Blender or game assets."""

import importlib.util
import socket
import tempfile
import threading
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "satellite_tiles", Path(__file__).parents[1] / "tools/render_satellite_tiles.py"
)
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


class SatelliteQueueTests(unittest.TestCase):
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
