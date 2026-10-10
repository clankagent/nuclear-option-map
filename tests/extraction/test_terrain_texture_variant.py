import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from terrain_texture_variant import TerrainTextureVariant, resolve_texture_variant


class VariantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'source.bin').write_bytes(b'original alpha and rgb')
        (self.root / 'rgb.bin').write_bytes(b'new rgb')
        h = lambda name: hashlib.sha256((self.root / name).read_bytes()).hexdigest()
        self.asset = {'assetId': 'a', 'role': 'macro', 'source': 'source.bin',
                      'rgbExport': 'rgb.bin', 'alphaExport': 'source.bin',
                      'sourceSha256': h('source.bin'), 'alphaSha256': h('source.bin'),
                      'rgbSha256': h('rgb.bin'), 'sourcePixels': [2, 3],
                      'rgbPixels': [4, 6], 'rgbMode': 'RGB', 'colorSpace': 1,
                      'alphaSizeUnchanged': True, 'textureSettings': None}
        self.binding = {'property': '_macro_basecolor', 'assetId': 'a', 'alphaSource': 'source.bin'}
        self.spec = {'schemaVersion': 1, 'assets': [self.asset],
                     'materials': [{'material': 'Terrain1', 'rgbBindings': [self.binding]}]}

    def load(self):
        path = self.root / 'selection.json'
        path.write_text(json.dumps(self.spec))
        return TerrainTextureVariant(path)

    def test_correct_2x_binding_preserves_alpha(self):
        variant = self.load()
        texture = {'property': '_macro_basecolor', 'export': str(self.root / 'source.bin'),
                   'pixels': [2, 3], 'colorSpace': 1, 'textureSettings': None}
        asset = variant.texture_for({'name': 'Terrain1'}, texture, 'macro')
        self.assertEqual(asset['alphaExport'], str(self.root / 'source.bin'))
        self.assertEqual(len(list(variant.iter_assets())), 1)
        self.assertIsNone(variant.texture_for('Other', texture, 'macro'))
        with self.assertRaises(ValueError):
            variant.texture_for('Terrain1', texture, 'detail')
        texture['export'] = str(self.root / 'rgb.bin')
        with self.assertRaises(ValueError):
            variant.texture_for('Terrain1', texture, 'macro')

    def test_reject_wrong_role_alpha_size_and_forbidden_property(self):
        for key, bad in [('role', 'detail'), ('alphaExport', 'rgb.bin'),
                         ('rgbPixels', [4, 5]), ('sourceSha256', '0' * 64)]:
            with self.subTest(key=key):
                old = self.asset[key]
                self.asset[key] = bad
                with self.assertRaises(ValueError):
                    self.load()
                self.asset[key] = old
        self.binding['property'] = '_macro_normal'
        with self.assertRaises(ValueError):
            self.load()

    def test_duplicate_and_missing_asset_rejected(self):
        self.spec['assets'].append(dict(self.asset))
        with self.assertRaises(ValueError):
            self.load()
        self.spec['assets'].pop()
        self.binding['assetId'] = 'absent'
        with self.assertRaises(ValueError):
            self.load()

    def test_changed_source_content_rejected(self):
        (self.root / 'source.bin').write_bytes(b'changed source')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.load()

    def test_cached_variant_rechecks_modified_rgb(self):
        variant = self.load()
        (self.root / 'rgb.bin').write_bytes(b'changed derivative after validation')
        texture = {'property': '_macro_basecolor', 'export': str(self.root / 'source.bin'),
                   'pixels': [2, 3], 'colorSpace': 1, 'textureSettings': None}
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            variant.texture_for('Terrain1', texture, 'macro')

    def test_periodic_virtual_boxes_are_explicit_and_bounded(self):
        (self.root / 'tile.bin').write_bytes(b'periodic tile pixels')
        self.asset['macroTileSpec'] = 'tiles.json'
        spec = {'size': [4, 6], 'xCuts': [], 'yCuts': [],
                'periodicGutters': True, 'gutter': 2,
                'tiles': [{'x': 0, 'y': 0, 'box': [-2, -2, 6, 8], 'file': 'tile.bin'}]}

        def write():
            (self.root / 'tiles.json').write_text(json.dumps(spec))

        write()
        self.assertEqual(self.load().assets[0]['macroTiles'][0]['box'], [-2, -2, 6, 8])
        for gutter, box, periodic in [(17, [-2, -2, 6, 8], True),
                                      (2, [-3, -2, 6, 8], True),
                                      (2, [0, -2, 6, 8], True),
                                      (2, [-2, -2, 6, 8], False)]:
            with self.subTest(gutter=gutter, box=box, periodic=periodic):
                spec['gutter'], spec['periodicGutters'] = gutter, periodic
                spec['tiles'][0]['box'] = box
                write()
                with self.assertRaises(ValueError):
                    self.load()

    def test_default_descriptor_activation_switch_and_baseline(self):
        first = self.load()
        report = self.root / 'material-report.json'
        descriptor = self.root / 'terrain-textures.json'
        self.assertIsNone(resolve_texture_variant(report_path=report))
        descriptor.write_text(json.dumps({'schemaVersion': 1, 'manifest': 'selection.json'}))
        selected = resolve_texture_variant(report_path=report)
        self.assertEqual(selected.sha256, first.sha256)
        self.assertIs(selected, resolve_texture_variant(report_path=report))
        self.assertIs(selected, resolve_texture_variant(selected))
        self.assertIsNone(resolve_texture_variant(False, report))
        second_path = self.root / 'other-selection.json'
        second_path.write_text(json.dumps(self.spec, indent=2))
        descriptor.write_text(json.dumps({'schemaVersion': 1, 'manifest': second_path.name}))
        switched = resolve_texture_variant(report_path=report)
        self.assertEqual(switched.path, second_path)
        self.assertIsNot(switched, selected)
        self.assertIs(switched, resolve_texture_variant(second_path))

    def test_installed_invalid_default_never_falls_back(self):
        report = self.root / 'material-report.json'
        descriptor = self.root / 'terrain-textures.json'
        for contents in ['broken json', json.dumps({'schemaVersion': 2, 'manifest': 'x'}),
                         json.dumps({'schemaVersion': 1, 'manifest': ''}),
                         json.dumps({'schemaVersion': 1, 'manifest': 'missing.json'})]:
            descriptor.write_text(contents)
            with self.subTest(contents=contents):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    resolve_texture_variant(report_path=report)


if __name__ == '__main__':
    unittest.main()
