import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from texture_metadata import sampling_metadata


class TextureMetadataTests(unittest.TestCase):
    def test_keeps_distinct_linear_and_srgb_flags_and_per_axis_wrap(self):
        settings = SimpleNamespace(m_FilterMode=1, m_Aniso=4, m_MipBias=-.5,
                                   m_WrapU=0, m_WrapV=1, m_WrapW=2)
        for flag in (0, 1):
            result = sampling_metadata(SimpleNamespace(m_ColorSpace=flag, m_TextureSettings=settings))
            self.assertEqual(result["colorSpace"], flag)
            self.assertEqual(result["textureSettings"], vars(settings))

    def test_older_records_keep_optional_metadata_absent(self):
        self.assertEqual(sampling_metadata(SimpleNamespace()), {})
        result = sampling_metadata(SimpleNamespace(m_TextureSettings=SimpleNamespace(m_WrapMode=1)))
        self.assertEqual(result, {"textureSettings": {"m_WrapMode": 1}})


if __name__ == "__main__":
    unittest.main()
