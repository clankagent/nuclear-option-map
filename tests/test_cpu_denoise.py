"""CPU denoise geometry/provenance contracts without Blender or game assets."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('satellite_denoise', Path(__file__).parents[1] / 'tools/render_satellite_tiles.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
helper_spec = importlib.util.spec_from_file_location('cpu_denoise', Path(__file__).parents[1] / 'tools/cpu_denoise.py')
helper = importlib.util.module_from_spec(helper_spec)
helper_spec.loader.exec_module(helper)
PROFILE = dict(device='CPU', threads=2, tileAlignment=16, tileOverlap=96)

class CpuDenoiseTests(unittest.TestCase):
    def test_supervised_redirector_ancestry_is_bounded_and_rejects_cycles(self):
        self.assertTrue(helper._ancestor_in_parents(10, 30, {30: 20, 20: 10}))
        self.assertFalse(helper._ancestor_in_parents(10, 30, {30: 20, 20: 30}))
        self.assertFalse(helper._ancestor_in_parents(10, 30, {30: 20, 20: 1}))
        self.assertFalse(helper._ancestor_in_parents(1, 11, {i: i - 1 for i in range(2, 12)}))

    def test_halo_alignment_preserves_global_sampling_phase_and_center(self):
        for span in (1, 2, 4):
            size = span * 512 + 4
            geometry = renderer.denoise_geometry(size, PROFILE)
            padding = geometry['padding']
            self.assertGreaterEqual(padding, 96)
            self.assertEqual(geometry['renderEdge'] % 16, 0)
            self.assertEqual((-2 - padding) % 16, 0)
            self.assertEqual(geometry['crop'], [padding, padding, padding + size, padding + size])
            # Adjacent chunks expose identical world-space overlap positions.
            self.assertEqual(span * 512 - 2 - padding, (-2 - padding) + span * 512)
            self.assertEqual((geometry['renderEdge'] - size) / 2, padding)

    def test_oversized_padded_chunk_and_non_cpu_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            renderer.denoise_geometry(4100, PROFILE)
        with self.assertRaisesRegex(ValueError, 'CPU'):
            renderer.denoise_geometry(516, dict(PROFILE, device='GPU'))
        with self.assertRaisesRegex(ValueError, 'alignment'):
            renderer.denoise_geometry(512, PROFILE)

    def test_raw_dimension_or_runtime_hash_mismatch_never_replaces_chunk(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'chunk.png'
            path.write_bytes(b'original')
            settings = dict(profile=dict(PROFILE, librarySha256='approved'), python='python', libraryDirectory='library')
            good = dict(settings['profile'], width=736, height=736, outputWidth=516, outputHeight=516)
            for bad in (dict(good, width=735), dict(good, librarySha256='changed'), dict(good, outputWidth=512)):
                with mock.patch.object(renderer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(bad), '')):
                    with self.assertRaises(ValueError):
                        renderer.denoise_render_file(path, 516, settings)
                self.assertEqual(path.read_bytes(), b'original')

if __name__ == '__main__':
    unittest.main()
