"""CPU-only selective pyramid rebuild against a clean full recompute."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import os

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1]/'tools'))
from prepare_raster_patch import Reader, coordinates, core_size, prepare, save_tile, sha


class RasterPatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.base = self.root/'base'; self.base.mkdir()

    def fixture(self, size=64, width=270, height=266, sparse=False):
        self.size = size
        dimensions = [(width, height)]
        for _ in range(2):
            width, height = (width+1)//2, (height+1)//2
            dimensions.insert(0, (width, height))
        levels = [dict(level=z, width=w, height=h, columns=(w+size-1)//size, rows=(h+size-1)//size)
                  for z, (w, h) in enumerate(dimensions)]
        if sparse:
            levels[-1]['coverage'] = [dict(x=0, y=0, columns=1, rows=1)]
        self.record = dict(schemaVersion=1, fillRgba8=[18, 78, 101, 255], tiles=dict(tileSize=size, gutter=2, levels=levels))
        finest = levels[-1]
        for x, y in coordinates(finest):
            w, h = core_size(finest, x, y, size)
            yy, xx = np.indices((h+4, w+4))
            pixels = np.stack(((xx+x*size)*7%256, (yy+y*size)*11%256, (xx+yy)*3%256), axis=2).astype(np.uint8)
            save_tile(Image.fromarray(pixels), self.base/'2'/str(x)/f'{y}.webp')
        self.recompute(self.base)
        self.manifest = self.base/'pyramid.json'; self.manifest.write_text(json.dumps(self.record))
        self.plan = self.root/'replacements.json'

    def recompute(self, root):
        # Independent full-canvas reference: assemble each child core, clamp the
        # sampling window, then apply production's resize/crop formula.
        levels = self.record['tiles']['levels']
        for child, parent in zip(reversed(levels[1:]), reversed(levels[:-1])):
            canvas = np.empty((child['height'], child['width'], 3), dtype=np.uint8)
            canvas[:] = self.record['fillRgba8'][:3]
            for x, y in coordinates(child):
                w, h = core_size(child, x, y, self.size)
                with Image.open(root/str(child['level'])/str(x)/f'{y}.webp') as image:
                    canvas[y*self.size:y*self.size+h, x*self.size:x*self.size+w] = np.asarray(image.convert('RGB'))[2:h+2, 2:w+2]
            for x, y in coordinates(parent):
                w, h = core_size(parent, x, y, self.size)
                left, top = 2*(x*self.size-2)-6, 2*(y*self.size-2)-6
                xs = np.clip(np.arange(left, left+2*(w+4)+12), 0, child['width']-1)
                ys = np.clip(np.arange(top, top+2*(h+4)+12), 0, child['height']-1)
                source = Image.fromarray(canvas[np.ix_(ys, xs)])
                scaled = source.resize((source.width//2, source.height//2), Image.Resampling.LANCZOS)
                save_tile(scaled.crop((3, 3, w+7, h+7)), root/str(parent['level'])/str(x)/f'{y}.webp')
        l = levels[0]
        overview = Image.new('RGB', (l['width'], l['height']), tuple(self.record['fillRgba8'][:3]))
        for x, y in coordinates(l):
            w, h = core_size(l, x, y, self.size)
            with Image.open(root/'0'/str(x)/f'{y}.webp') as image:
                overview.paste(image.crop((2, 2, w+2, h+2)), (x*self.size, y*self.size))
        save_tile(overview, root/'overview.webp')

    def replacement(self, x=1, y=1, gutter=False):
        path = self.root/'replacement.webp'
        with Image.open(self.base/'2'/str(x)/f'{y}.webp') as im:
            image = im.convert('RGB')
        if gutter:
            image.putpixel((0, 0), (255, 0, 255))
        else:
            image.paste((255, 0, 255), (2, 2, min(image.width-2, 9), min(image.height-2, 9)))
        save_tile(image, path)
        self.plan.write_text(json.dumps(dict(schemaVersion=1, tiles=[dict(x=x, y=y, path=path.name, sha256=sha(path))])))
        return path

    def run_patch(self, **kwargs):
        return prepare(self.manifest, self.base, self.plan, self.root/'candidate', **kwargs)

    def compare_full(self, replacement, x, y):
        import shutil
        full = self.root/'full'; shutil.copytree(self.base, full)
        shutil.copyfile(replacement, full/'2'/str(x)/f'{y}.webp')
        self.recompute(full)
        for path in full.rglob('*.webp'):
            with Image.open(path) as a, Image.open(self.root/'candidate'/path.relative_to(full)) as b:
                self.assertEqual(a.tobytes(), b.tobytes(), str(path))

    def test_boundary_diagonal_partial_edges_and_full_equivalence(self):
        self.fixture(); replacement = self.replacement(2, 2)
        original = {p: sha(p) for p in self.base.rglob('*.webp')}
        _, receipt = self.run_patch()
        self.assertTrue(receipt['runtime']['pillow'])
        self.assertTrue(receipt['runtime']['webp'])
        self.compare_full(replacement, 2, 2)
        self.assertGreater(receipt['derivedCount'], 2)
        self.assertEqual(original, {p: sha(p) for p in original})
        reused = self.base/'2/0/0.webp'; candidate = self.root/'candidate/2/0/0.webp'
        self.assertEqual(reused.stat().st_ino, candidate.stat().st_ino)
        self.assertNotEqual((self.base/'2/2/2.webp').stat().st_ino, (self.root/'candidate/2/2/2.webp').stat().st_ino)

    def test_map_corner_partial_replacement(self):
        self.fixture(); replacement = self.replacement(4, 4)
        self.run_patch(); self.compare_full(replacement, 4, 4)

    def test_production_512_geometry(self):
        self.fixture(512, 1030, 1026); replacement = self.replacement(1, 1)
        self.run_patch(); self.compare_full(replacement, 1, 1)

    def test_gutter_only_does_not_propagate(self):
        self.fixture(); self.replacement(gutter=True)
        _, receipt = self.run_patch()
        self.assertEqual(receipt['derivedCount'], 0)
        self.assertEqual(receipt['stagedCount'], 1)
        self.assertEqual(receipt['changedCoreCount'], 0)

    def test_sparse_virtual_fill(self):
        self.fixture(sparse=True); replacement = self.replacement(0, 0)
        self.run_patch(); self.compare_full(replacement, 0, 0)
        self.assertFalse((self.root/'candidate/2/1/1.webp').exists())

    def test_missing_covered_tile(self):
        self.fixture(); self.replacement(); (self.base/'2/0/0.webp').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing covered'):
            self.run_patch()
        self.assertFalse((self.root/'candidate').exists())

    def test_bad_sha_and_out_of_coverage(self):
        self.fixture(sparse=True); self.replacement(0, 0)
        plan = json.loads(self.plan.read_text()); plan['tiles'][0]['sha256'] = '0'*64; self.plan.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            self.run_patch()
        plan['tiles'][0]['x'] = 1; self.plan.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, 'coverage'):
            self.run_patch()
        self.assertFalse((self.root/'candidate').exists())

    def test_overlap_and_existing_output(self):
        self.fixture(); self.replacement()
        with self.assertRaisesRegex(ValueError, 'nonoverlapping'):
            prepare(self.manifest, self.base, self.plan, self.base/'candidate')
        (self.root/'candidate').mkdir()
        with self.assertRaisesRegex(ValueError, 'Fresh'):
            self.run_patch()

    def test_plan_only_creates_no_files(self):
        self.fixture(); self.replacement()
        _, receipt = self.run_patch(plan_only=True)
        self.assertTrue(receipt['planOnly'])
        self.assertFalse((self.root/'candidate').exists())

    def test_sparse_missing_fill_and_nonopaque_replacement(self):
        self.fixture(sparse=True); self.replacement(0, 0)
        self.record.pop('fillRgba8'); self.manifest.write_text(json.dumps(self.record))
        with self.assertRaisesRegex(ValueError, 'explicit opaque fillRgba8'):
            self.run_patch()
        import shutil
        shutil.rmtree(self.root/'candidate')
        path = self.root/'replacement.webp'
        save_tile(Image.new('RGBA', (68, 68), (1, 2, 3, 100)), path)
        plan = json.loads(self.plan.read_text()); plan['tiles'][0]['sha256'] = sha(path); self.plan.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, 'Opaque RGB'):
            self.run_patch()
        self.assertFalse((self.root/'candidate').exists())

    def test_per_level_roots_and_copy_mode(self):
        self.fixture(); replacement = self.replacement(1, 1)
        import shutil
        moved = self.root/'finest'; shutil.move(self.base/'2', moved)
        self.run_patch(base_roots={'2': moved}, copy_base=True)
        self.assertNotEqual((self.base/'0/0/0.webp').stat().st_ino, (self.root/'candidate/0/0/0.webp').stat().st_ino)
        shutil.move(moved, self.base/'2')
        self.compare_full(replacement, 1, 1)

    def test_rejects_plan_and_baseline_descriptor_mutation_during_staging(self):
        self.fixture(); self.replacement()
        original_link = os.link
        for changed_path in (self.plan, self.manifest):
            with self.subTest(changed_path=changed_path):
                payload = changed_path.read_bytes()
                mutated = False

                def mutate_after_link(source, destination):
                    nonlocal mutated
                    original_link(source, destination)
                    if not mutated:
                        changed_path.write_bytes(payload+b'\n')
                        mutated = True

                with mock.patch('prepare_raster_patch.os.link', side_effect=mutate_after_link):
                    with self.assertRaisesRegex(ValueError, 'changed during preparation'):
                        self.run_patch()
                self.assertFalse((self.root/'candidate/patch-receipt.json').exists())
                self.assertFalse((self.root/'candidate/pyramid.json').exists())
                changed_path.write_bytes(payload)
                import shutil
                shutil.rmtree(self.root/'candidate')

    def test_empty_coverage_and_missing_fill(self):
        self.fixture(sparse=True); self.record['tiles']['levels'][-1]['coverage'] = []
        self.record.pop('fillRgba8'); self.manifest.write_text(json.dumps(self.record))
        self.plan.write_text(json.dumps(dict(schemaVersion=1, tiles=[])))
        with self.assertRaisesRegex(ValueError, 'Nonempty'):
            self.run_patch()


if __name__ == '__main__':
    unittest.main()
