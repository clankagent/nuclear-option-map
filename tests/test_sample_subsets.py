import sys
from pathlib import Path
import unittest
import tempfile
import json
import hashlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from render_safety import comparison_subset
from run_sample_comparison import sample_plan, verify_chunk


class SampleSubsetTests(unittest.TestCase):
    def test_rejects_stale_inputs_and_changed_exr(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            raw = b'private EXR fixture'
            (output/'raw.exr').write_bytes(raw)
            runtime = dict(completed=True, samplesCompletedLog=1, samplesRequested=1,
                           sampleOffset=0, sampleSubsetLength=1, sampleSequenceTotal=128,
                           sceneSha256='scene', blenderSha256='binary', wallSeconds=1)
            proof = dict(sceneSha256='scene', outputSha256=hashlib.sha256(raw).hexdigest(),
                         planning=dict(sampleOffset=0, sampleSubsetLength=1), seconds=.5)
            (output/'runtime.json').write_text(json.dumps(runtime))
            (output/'proof.json').write_text(json.dumps(proof))
            self.assertEqual(verify_chunk(output, 0, 1, 'scene', 'binary')['count'], 1)
            for scene, binary in [('changed', 'binary'), ('scene', 'changed')]:
                with self.assertRaises(RuntimeError):
                    verify_chunk(output, 0, 1, scene, binary)
            (output/'raw.exr').write_bytes(b'changed EXR')
            with self.assertRaises(RuntimeError):
                verify_chunk(output, 0, 1, 'scene', 'binary')

    def test_exact_nonoverlapping_sequence_and_milestones(self):
        position = 0
        milestones = set()
        for offset, count in sample_plan():
            self.assertEqual(offset, position)
            self.assertEqual(comparison_subset(offset, count)['sampleSubsetLength'], count)
            position += count
            milestones.add(position)
        self.assertEqual(position, 128)
        self.assertTrue({1, 4, 16, 64, 128} <= milestones)

    def test_rejects_unbounded_or_empty_subset(self):
        for values in ((0, 17, 128), (127, 2, 128), (128, 1, 128), (-1, 1, 128),
                       (0, 1, 129), (0, 0, 128), (True, 1, 128), (0, 1.0, 128)):
            with self.assertRaises(ValueError):
                comparison_subset(*values)
