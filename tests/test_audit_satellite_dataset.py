"""Completed dataset evidence checks, without Blender or a GPU."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import audit_satellite_dataset as audit
from PIL import Image


class DatasetAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = self.root / "fixture"
        scratch = self.dataset / ".render-work"
        scratch.mkdir(parents=True)
        blend = self.root / "source.blend"
        blend.write_bytes(b"private test source")
        stat = blend.stat()
        profile = {"device": "CPU", "threads": 2, "tileAlignment": 32,
                   "tileOverlap": 64, "hdr": True, "srgb": False}
        self.source = {"blend": str(blend), "blendBytes": stat.st_size,
                       "blendMtimeNs": stat.st_mtime_ns,
                       "blendSha256": hashlib.sha256(blend.read_bytes()).hexdigest(),
                       "worldWidth": 81920, "worldDepth": 81920,
                       "tileSize": 512, "gutter": 2,
                       "renderProfile": {"engine": "CYCLES", "device": "cpu", "samples": 4,
                                         "threads": 2, "denoising": False, "compositorDevice": "CPU"},
                       "cpuDenoise": {"profile": profile, "linear": True}}
        version = hashlib.sha256(json.dumps(self.source, sort_keys=True).encode()).hexdigest()[:16]
        self.level = {"level": 0, "width": 512, "height": 512, "columns": 1,
                      "rows": 1, "urlTemplate": f"{{z}}/{{x}}/{{y}}.webp?v={version}"}
        self.metadata = {"image": f"overview.webp?v={version}",
                         "tiles": {"tileSize": 512, "gutter": 2, "levels": [self.level]}}
        geometry = audit.denoise_geometry(516, profile)
        edge = geometry["renderEdge"]
        self.render = {"column": 0, "row": 0, "level": 0, "span": 1,
                       "x": 0, "z": 0, "metersPerPixel": 160, "size": 516,
                       "path": str(scratch / "0-0-0.png"), "seconds": 1,
                       "actualScene": {"engine": "CYCLES", "device": "CPU", "samples": 4,
                                       "threadsMode": "FIXED", "threads": 2,
                                       "denoising": False, "previewDenoising": False,
                                       "layerDenoising": [False], "compositorDevice": "CPU",
                                       "timeLimitSeconds": 0, "width": edge, "height": edge,
                                       "resolutionPercentage": 100,
                                       "enabledPasses": ["use_pass_combined"]},
                       "cpuDenoise": {**profile, **geometry, "outputWidth": 516,
                                      "outputHeight": 516, "width": edge, "height": edge,
                                      "colorSpace": "scene-linear"}}
        audit.atomic_write(scratch / "source.json", self.source)
        audit.atomic_write(scratch / "level-0-receipt.json", [self.render])
        audit.atomic_write(self.dataset / "pyramid.json", self.metadata)
        tile = self.dataset / "0" / "0" / "0.webp"
        tile.parent.mkdir(parents=True)
        Image.new("RGB", (516, 516), "blue").save(tile, "WEBP")
        Image.new("RGB", (512, 512), "blue").save(self.dataset / "overview.webp", "WEBP")

    def test_complete_linear_dataset_uses_runtime_padding(self):
        report, _ = audit.audit_dataset(self.dataset, "heartland-46396", 0, 4)
        self.assertEqual(report["tiles"], 1)
        self.assertEqual(self.render["cpuDenoise"]["padding"], 94)
        self.assertFalse(report["visualQualityAcceptedByAudit"])
        self.assertEqual(report["sourceIntegrity"]["sha256"], self.source["blendSha256"])
        self.assertEqual(report["finalContentIntegrity"]["files"], 2)

    def test_independent_source_hash_and_final_content_hash(self):
        report, _ = audit.audit_dataset(self.dataset, "heartland-46396", 0, 4,
                                         self.source["blendSha256"])
        with self.assertRaisesRegex(ValueError, "expected integrity hash"):
            audit.audit_dataset(self.dataset, "heartland-46396", 0, 4, "0" * 64)
        original = report["finalContentIntegrity"]["aggregateSha256"]
        Image.new("RGB", (516, 516), "red").save(self.dataset / "0/0/0.webp", "WEBP")
        updated, _ = audit.audit_dataset(self.dataset, "heartland-46396", 0, 4)
        self.assertNotEqual(original, updated["finalContentIntegrity"]["aggregateSha256"])
        records = [("b", "2" * 64), ("a", "1" * 64)]
        self.assertEqual(audit.aggregate_content_sha256(records),
                         audit.aggregate_content_sha256(list(reversed(records))))

    def test_current_receipt_supersedes_archived_chunk(self):
        previous = copy.deepcopy(self.render)
        previous["seconds"] = 999
        audit.atomic_write(self.dataset / ".render-work/level-0-receipt-history-1.json", [previous])
        receipts, files = audit.latest_chunk_receipts(self.dataset, 0)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["seconds"], 1)
        self.assertEqual(len(files), 2)

    def test_reject_missing_grid_and_gpu_or_noncombined_receipts(self):
        for mutate in (lambda r: r["actualScene"].update(device="GPU"),
                       lambda r: r["actualScene"].update(enabledPasses=["use_pass_combined", "use_pass_z"]),
                       lambda r: r["cpuDenoise"].update(padding=110)):
            render = copy.deepcopy(self.render)
            mutate(render)
            with self.assertRaises(ValueError):
                audit.audit_chunks([render], self.source, self.dataset, self.level, 4)
        with self.assertRaisesRegex(ValueError, "Missing source chunks"):
            audit.audit_chunks([], self.source, self.dataset, self.level, 4)

    def test_reject_source_change(self):
        source = Path(self.source["blend"])
        source.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Source scene changed"):
            audit.audit_dataset(self.dataset, "heartland-46396", 0, 4)

    def test_reject_unadvertised_tile(self):
        extra = self.dataset / "0/0/1.webp"
        Image.new("RGB", (516, 516)).save(extra, "WEBP")
        with self.assertRaisesRegex(ValueError, "Unexpected/missing tile paths"):
            audit.audit_dataset(self.dataset, "heartland-46396", 0, 4)

    def test_complete_level_six_source_grid(self):
        level = {"level": 6, "width": 32768, "height": 32768,
                 "columns": 64, "rows": 64}
        size = 2052
        geometry = audit.denoise_geometry(size, self.source["cpuDenoise"]["profile"])
        renders = []
        for x in range(0, 64, 4):
            for y in range(0, 64, 4):
                render = copy.deepcopy(self.render)
                render.update(column=x, row=y, level=6, span=4, size=size,
                              metersPerPixel=2.5, x=-40960 + (x + 2) * 1280,
                              z=40960 - (y + 2) * 1280,
                              path=str(self.dataset / f".render-work/6-{x}-{y}.png"))
                render["actualScene"].update(width=geometry["renderEdge"], height=geometry["renderEdge"])
                render["cpuDenoise"].update(geometry, outputWidth=size, outputHeight=size,
                                           width=geometry["renderEdge"], height=geometry["renderEdge"])
                renders.append(render)
        audit.audit_chunks(renders, self.source, self.dataset, level, 4)
        self.assertEqual(len(renders), 256)
        with self.assertRaisesRegex(ValueError, "Missing source chunks"):
            audit.audit_chunks(renders[:-1], self.source, self.dataset, level, 4)

    def test_promotion_preserves_other_maps_entries_and_fields(self):
        variants = {"otherRoot": [1, 2], "maps": {
            "heartland-46396": [{"id": "satellite", "label": "Satellite", "custom": 7},
                                {"id": "topography", "custom": [3]}],
            "ignus-archipelago-46395": [{"id": "satellite", "image": "unchanged"}]}}
        path = self.root / "variants.json"
        audit.atomic_write(path, variants)
        audit.promote_variants(path, "heartland-46396", self.dataset, self.metadata)
        updated = audit.read_json(path)
        self.assertEqual(updated["otherRoot"], variants["otherRoot"])
        self.assertEqual(updated["maps"]["ignus-archipelago-46395"], variants["maps"]["ignus-archipelago-46395"])
        self.assertEqual(updated["maps"]["heartland-46396"][1], variants["maps"]["heartland-46396"][1])
        self.assertEqual(updated["maps"]["heartland-46396"][0]["custom"], 7)
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Concurrent variants update"):
            audit.atomic_write(path, {}, original=b"stale")
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(self.root.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
