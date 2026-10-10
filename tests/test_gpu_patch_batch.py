import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from render_gpu_patch_batch import validate_windows
import render_gpu_patch_batch as worker
from run_gpu_patch_batch import validate_queue, segment_samples
from run_gpu_resolution_comparison import resolution_plan


class LoadedBatch(unittest.TestCase):
    manifest = dict(centerX=10570,centerZ=8150,footprintMeters=419.84)

    def test_nine_window_half_meter_plan_accepted(self):
        validate_windows(resolution_plan()[-1]['pieces'],self.manifest,4)

    def test_bound_violations_rejected(self):
        original = resolution_plan()[-1]['pieces']
        for windows,samples in ((original,17),(original*2,4),([],4)):
            with self.assertRaises(ValueError): validate_windows(windows,self.manifest,samples)
        for key,value in (('edge',257),('edge',True),('centerX',20000),('centerZ',float('nan')),
                          ('footprintMeters',500),('column',True)):
            windows = [dict(original[0],**{key:value})]
            with self.assertRaises(ValueError): validate_windows(windows,self.manifest,4)

    def test_queue_receipt_binding_and_bounds(self):
        prior = dict(completed=True,sceneSha256='scene',blenderSha256='binary',
                     queueStatesFactor='0.005',queueStatesObserved=[8191])
        validate_queue(prior,'scene','binary')
        for key,value in (('completed',False),('sceneSha256','other'),('blenderSha256','other'),
                          ('queueStatesFactor','nan'),('queueStatesObserved',[8193])):
            with self.assertRaises(ValueError): validate_queue(dict(prior,**{key:value}),'scene','binary')

    def log(self,counts):
        return '\n'.join(f'GPU_PATCH_BEGIN {json.dumps(dict(index=i))}\nSample {count}/4\nCROPPED_TRIAL_COMPLETE {{}}\nGPU_PATCH_END {json.dumps(dict(index=i))}'
                         for i,count in enumerate(counts))

    def test_independent_completed_samples_per_window(self):
        self.assertEqual([s['samplesCompletedLog'] for s in segment_samples(self.log([4,4]),2,4)],[4,4])
        with self.assertRaises(RuntimeError): segment_samples(self.log([4,3]),2,4)
        with self.assertRaises(RuntimeError): segment_samples(self.log([4]),2,4)
        with self.assertRaises(RuntimeError): segment_samples(self.log([4,4]).replace('"index": 1','"index": 0'),2,4)

    def test_original_camera_reset_before_each_guarded_trial(self):
        camera = SimpleNamespace(location=[10570,-8150,10000], data=SimpleNamespace(type='ORTHO',ortho_scale=419.84))
        bpy = SimpleNamespace(context=SimpleNamespace(scene=SimpleNamespace(camera=camera)),
                              data=SimpleNamespace(filepath='unchanged-candidate.blend'))
        calls = []
        def fake_trial(argv):
            calls.append((list(camera.location),camera.data.ortho_scale,argv))
            camera.location=[1,2,3]; camera.data.ortho_scale=120
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            (folder/'manifest.json').write_text(json.dumps(self.manifest))
            (folder/'pieces.json').write_text(json.dumps(dict(pieces=resolution_plan()[-1]['pieces'][:2])))
            with patch.dict(sys.modules,{'bpy':bpy}),patch.object(worker,'render_trial',fake_trial):
                worker.main(['--manifest',str(folder/'manifest.json'),'--pieces',str(folder/'pieces.json'),
                             '--output',str(folder),'--samples','4'])
        self.assertEqual(len(calls),2)
        for location,scale,argv in calls:
            self.assertEqual(location,[10570,-8150,10000]); self.assertEqual(scale,419.84)
            self.assertIn('--linear-exr',argv); self.assertNotIn('--sample-offset',argv)


if __name__ == '__main__':
    unittest.main()
