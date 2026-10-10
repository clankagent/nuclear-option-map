import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from publish_gpu_resolution_comparison import verified_loaded_benchmark


class LoadedPublication(unittest.TestCase):
    def test_loaded_time_requires_matching_denoised_output(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name); denoised = folder/'denoised'; denoised.mkdir()
            png = denoised/'denoised.png'; png.write_bytes(b'completed fixture')
            sha = hashlib.sha256(png.read_bytes()).hexdigest()
            pieces = [dict(path='example.exr', sha256='raw')]
            gpu = dict(completed=True,sceneSha256='scene',windows=9,samplesPerWindow=4,completeBatchWallSeconds=5)
            cpu = dict(completeCpuJobWallSeconds=2,pngSha256=sha)
            plan = dict(sceneSha256='scene',pieces=pieces)
            proof = dict(completed=True,sceneSha256='scene',samples=4,width=720,height=720,pieces=pieces,sha256=sha)
            for filename, data in [('runtime.json',gpu),('denoise-wall.json',cpu),('pieces.json',plan)]:
                (folder/filename).write_text(json.dumps(data))
            proof_file = denoised/'proof.json'; proof_file.write_text(json.dumps(proof))
            self.assertEqual(verified_loaded_benchmark(folder,'scene',denoised), (gpu,cpu))
            for key, value in [('samples',8), ('sceneSha256','other'), ('pieces',[]), ('sha256','stale')]:
                old = proof[key]; proof[key] = value; proof_file.write_text(json.dumps(proof))
                with self.assertRaises(ValueError): verified_loaded_benchmark(folder,'scene',denoised)
                proof[key] = old
            proof_file.write_text(json.dumps(proof))
            cpu['pngSha256'] = 'another image'; (folder/'denoise-wall.json').write_text(json.dumps(cpu))
            with self.assertRaises(ValueError): verified_loaded_benchmark(folder,'scene',denoised)


if __name__ == '__main__':
    unittest.main()
