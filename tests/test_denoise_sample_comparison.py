import importlib.util
from pathlib import Path
import tempfile
import unittest

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


if __name__ == '__main__':
    unittest.main()
