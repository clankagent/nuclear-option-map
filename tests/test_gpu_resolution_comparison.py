import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from run_gpu_resolution_comparison import resolution_plan, forecasts, verify_piece, digest
from denoise_resolution_comparison import validate_plan, place_piece, verified_sample_count


class ResolutionComparison(unittest.TestCase):
    def test_six_scales_equal_integer_bounded_windows(self):
        plan = resolution_plan()
        self.assertEqual([p['width'] for p in plan], [120,144,180,240,360,720])
        self.assertEqual([len(p['pieces']) for p in plan], [1,1,1,1,4,9])
        for row in plan:
            self.assertEqual(row['grid']*row['pieces'][0]['edge'], row['width'])
            for piece in row['pieces']:
                self.assertLessEqual(piece['edge'], 256)
                self.assertGreaterEqual(piece['centerX']-piece['footprintMeters']/2, 10390)
                self.assertLessEqual(piece['centerX']+piece['footprintMeters']/2, 10750)
                self.assertGreaterEqual(piece['centerZ']-piece['footprintMeters']/2, 7970)
                self.assertLessEqual(piece['centerZ']+piece['footprintMeters']/2, 8330)

    def test_north_up_assembly_retains_bottom_up_pixel_rows(self):
        import numpy as np
        result = np.zeros((4,4,4))
        for row in range(2):
            for column in range(2):
                place_piece(result, np.full((2,2,4), row*2+column+1),
                            dict(edge=2, rowFromSouth=row, column=column))
        self.assertEqual(result[:,:,0].tolist(), [[1,1,2,2],[1,1,2,2],[3,3,4,4],[3,3,4,4]])

    def test_duplicate_or_missing_mosaic_cells_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)/'a.exr'; path.write_bytes(b'fixture')
            plan = dict(width=360,height=360,grid=2,pieces=[dict(edge=180,rowFromSouth=r,column=c,path=str(path))
                                                         for r in range(2) for c in range(2)])
            validate_plan(plan)
            plan['pieces'][-1] = plan['pieces'][0]
            with self.assertRaises(ValueError): validate_plan(plan)

    def test_forecast_is_area_times_complete_preview_wall(self):
        result = forecasts(7)
        self.assertEqual(result['Ignus']['extrapolatedSeconds'], 2*result['Heartland']['extrapolatedSeconds'])
        self.assertAlmostEqual(result['Heartland']['extrapolatedSeconds'], 7*(81920/360)**2)

    def test_denoiser_derives_samples_and_rejects_incomplete_or_mixed_jobs(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            pieces = []
            for index in range(2):
                folder = root/str(index); folder.mkdir()
                raw = folder/'raw.exr'; raw.write_bytes(b'fixture')
                pieces.append(dict(path=str(raw), sha256=digest(raw), edge=32))
                (folder/'proof.json').write_text(json.dumps(dict(samples=8,device='GPU',denoising=False,
                    width=32,height=32,sceneSha256='scene',outputSha256=digest(raw))))
                (folder/'runtime.json').write_text(json.dumps(dict(completed=True,samplesRequested=8,
                    samplesCompletedLog=8,sceneSha256='scene')))
            plan = dict(pieces=pieces, sceneSha256='scene')
            self.assertEqual(verified_sample_count(plan), 8)
            plan['samples'] = 4
            with self.assertRaises(ValueError): verified_sample_count(plan)
            del plan['samples']
            runtime_file = root/'1'/'runtime.json'
            runtime = json.loads(runtime_file.read_text()); runtime['samplesCompletedLog'] = 7
            runtime_file.write_text(json.dumps(runtime))
            with self.assertRaises(ValueError): verified_sample_count(plan)
            runtime.update(samplesCompletedLog=4,samplesRequested=4)
            runtime_file.write_text(json.dumps(runtime))
            proof_file = root/'1'/'proof.json'
            proof = json.loads(proof_file.read_text()); proof['samples'] = 4
            proof_file.write_text(json.dumps(proof))
            with self.assertRaises(ValueError): verified_sample_count(plan)

    def test_completed_receipt_requires_gpu_four_samples_hash_and_window(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name); raw=folder/'raw.exr'; raw.write_bytes(b'fixture')
            piece = resolution_plan()[0]['pieces'][0]
            runtime = dict(completed=True,samplesCompletedLog=4,samplesRequested=4,sceneSha256='scene',
                           blenderSha256='binary',queueStatesObserved=[8191],wallSeconds=2)
            proof = dict(sceneSha256='scene',outputSha256=digest(raw),device='GPU',samples=4,
                         width=120,height=120,denoising=False,deviceSelection=[dict(type='CUDA')],seconds=1,
                         **{k:piece[k] for k in ('centerX','centerZ','footprintMeters')})
            def save():
                (folder/'runtime.json').write_text(json.dumps(runtime))
                (folder/'proof.json').write_text(json.dumps(proof))
            save(); verify_piece(folder,piece,'scene','binary')
            for field, value in [('samples',1), ('device','CPU'), ('centerZ',8151), ('outputSha256','wrong')]:
                old=proof[field]; proof[field]=value; save()
                with self.assertRaises(RuntimeError): verify_piece(folder,piece,'scene','binary')
                proof[field]=old


if __name__ == '__main__':
    unittest.main()
