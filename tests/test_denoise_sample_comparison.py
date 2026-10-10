import importlib.util
from pathlib import Path
import tempfile
import unittest
import numpy as np

MODULE = Path(__file__).resolve().parents[1]/'tools'/'denoise_sample_comparison.py'
spec = importlib.util.spec_from_file_location('comparison', MODULE)
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


class ChunkValidation(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'linear.exr'
        self.path.write_bytes(b'fixture')

    def chunks(self):
        counts = [1, 3, 12]+[16]*7
        offset = 0
        result = []
        for count in counts:
            result.append(dict(path=str(self.path), count=count, offset=offset))
            offset += count
        return result

    def test_contiguous_exact_milestones(self):
        comparison.validate_chunks(self.chunks())

    def test_overlap_rejected(self):
        chunks = self.chunks()
        chunks[2]['offset'] = 3
        with self.assertRaises(ValueError):
            comparison.validate_chunks(chunks)


    def test_incomplete_sampling_rejected(self):
        with self.assertRaises(ValueError):
            comparison.validate_chunks(self.chunks()[:-1])

    def test_milestone_cannot_be_reconstructed_inside_average(self):
        chunks = self.chunks()
        chunks[0]['count'] = 2
        with self.assertRaises(ValueError):
            comparison.validate_chunks(chunks)

    def test_boolean_is_not_a_sample_count(self):
        chunks = self.chunks()
        chunks[0]['count'] = True
        with self.assertRaises(ValueError):
            comparison.validate_chunks(chunks)


class HdrValidation(unittest.TestCase):
    def test_rectangular_and_maximum_edges(self):
        comparison.validate_hdr(np.zeros((16, 4100, 3), dtype=np.float32), 240)

    def test_invalid_dimensions_and_values(self):
        for value in (np.zeros((1, 4101, 3)), np.zeros((0, 2, 3)),
                      np.zeros((2, 2, 4)), np.full((2, 2, 3), np.nan),
                      np.full((2, 2, 3), -1)):
            with self.assertRaises(ValueError):
                comparison.validate_hdr(value, 240)

    def test_invalid_timeout(self):
        for timeout in (True, 0, -1, float('nan'), float('inf'), 241):
            with self.assertRaises(ValueError):
                comparison.validate_hdr(np.zeros((2, 2, 3)), timeout)


if __name__ == '__main__':
    unittest.main()
