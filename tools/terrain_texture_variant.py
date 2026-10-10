"""Validate private RGB terrain variants without Blender or image decoding."""
import hashlib
import json
import struct
from pathlib import Path
from terrain_detail import DETAIL_PROPERTIES

DETAIL_KEYS = ('dunes', 'sand', 'grass', 'grass_fine', 'rock', 'fields', 'lush')
ALLOWED = {'_macro_basecolor': 'macro', **{
    '_SampleTexture2D_' + DETAIL_PROPERTIES[k] + '_Texture_1_Texture2D': 'detail'
    for k in DETAIL_KEYS}}


class TerrainTextureVariant:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self._hashes = {}
        self.sha256 = self._hash(self.path)
        spec = json.loads(self.path.read_text(encoding='utf-8-sig'))
        if spec.get('schemaVersion') != 1:
            raise ValueError('Unsupported terrain variant schema')
        self.assets = []
        ids, sources = {}, {}
        for original in spec['assets']:
            asset = dict(original)
            identity = asset['assetId']
            if identity in ids:
                raise ValueError('Duplicate terrain asset ID')
            for key in ('source', 'rgbExport', 'alphaExport'):
                asset[key] = str(self._path(asset[key]))
            if asset['source'] in sources:
                raise ValueError('Duplicate terrain source')
            if asset['role'] not in ('macro', 'detail') or asset['rgbMode'] != 'RGB':
                raise ValueError('Invalid terrain RGB role or mode')
            size = asset['sourcePixels']
            if (len(size) != 2 or any(type(v) is not int or v <= 0 for v in size)
                    or asset['rgbPixels'] != [v * 2 for v in size]):
                raise ValueError('Terrain RGB must be exactly 2x')
            if asset['colorSpace'] not in (0, 1):
                raise ValueError('Invalid original texture color space')
            if (asset['alphaExport'] != asset['source'] or
                    asset['alphaSha256'] != asset['sourceSha256'] or
                    asset.get('alphaSizeUnchanged') is not True):
                raise ValueError('Original terrain alpha must remain intact')
            self._verify(asset['source'], asset['sourceSha256'])
            self._verify(asset['rgbExport'], asset['rgbSha256'])
            self._png_metadata(asset['source'], size)
            self._png_metadata(asset['rgbExport'], asset['rgbPixels'], rgb=True)
            if asset.get('macroTileSpec'):
                if asset['role'] != 'macro':
                    raise ValueError('Only macro assets may have sampling tiles')
                self._tiles(asset)
            ids[identity] = asset
            sources[asset['source']] = asset
            self.assets.append(asset)
        self._bindings = {}
        used = set()
        for material in spec['materials']:
            name = material['material']
            if name in self._bindings:
                raise ValueError('Duplicate terrain material')
            bindings = {}
            for binding in material['rgbBindings']:
                prop = binding['property']
                if prop not in ALLOWED or prop in bindings:
                    raise ValueError('Forbidden or duplicate terrain RGB property')
                if binding['assetId'] not in ids:
                    raise ValueError('Missing terrain asset')
                asset = ids[binding['assetId']]
                if asset['role'] != ALLOWED[prop]:
                    raise ValueError('Terrain property role mismatch')
                if self._path(binding['alphaSource']) != Path(asset['source']):
                    raise ValueError('Material alpha source mismatch')
                bindings[prop] = asset
                used.add(asset['assetId'])
            self._bindings[name] = bindings
        if used != set(ids):
            raise ValueError('Unbound terrain assets')

    def _path(self, value):
        path = Path(value)
        return (path if path.is_absolute() else self.path.parent / path).resolve()

    def _hash(self, path):
        path = Path(path).resolve()
        if path not in self._hashes:
            h = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1048576), b''):
                    h.update(block)
            self._hashes[path] = h.hexdigest()
        return self._hashes[path]

    def _verify(self, path, expected):
        if self._hash(path) != expected:
            raise ValueError('Terrain asset hash mismatch: ' + str(path))

    @staticmethod
    def _png_metadata(path, size, rgb=False):
        if Path(path).suffix.lower() != '.png':
            return
        with Path(path).open('rb') as stream:
            header = stream.read(33)
        if (len(header) != 33 or header[:8] != b'\x89PNG\r\n\x1a\n' or
                header[12:16] != b'IHDR' or list(struct.unpack('>II', header[16:24])) != size):
            raise ValueError('PNG dimensions disagree with terrain manifest')
        if rgb and (header[24] != 8 or header[25] != 2):
            raise ValueError('Terrain derivative must be 8-bit RGB PNG')

    def _tiles(self, asset):
        path = self._path(asset['macroTileSpec'])
        spec = json.loads(path.read_text(encoding='utf-8-sig'))
        width, height = asset['rgbPixels']
        if spec['size'] != [width, height]:
            raise ValueError('Macro tile geometry size mismatch')
        for key, limit in (('xCuts', width), ('yCuts', height)):
            cuts = spec[key]
            if cuts != sorted(set(cuts)) or any(type(v) is not int or not 0 < v < limit for v in cuts):
                raise ValueError('Invalid macro tile cuts')
        nx, ny = len(spec['xCuts']) + 1, len(spec['yCuts']) + 1
        periodic = spec.get('periodicGutters') is True
        gutter = spec.get('gutter', 0) if periodic else 0
        if periodic and (type(gutter) is not int or not 0 < gutter <= 16):
            raise ValueError('Invalid periodic macro gutter')
        seen, tiles = set(), []
        for original in spec['tiles']:
            tile = dict(original)
            position = (tile['x'], tile['y'])
            if position in seen or position[0] not in range(nx) or position[1] not in range(ny):
                raise ValueError('Invalid macro tile grid')
            left, top, right, bottom = tile['box']
            if (any(type(v) is not int for v in (left, top, right, bottom)) or
                    not (-gutter <= left < right <= width + gutter and
                         -gutter <= top < bottom <= height + gutter)):
                raise ValueError('Invalid macro tile box')
            xs, ys = [0] + spec['xCuts'] + [width], [0] + spec['yCuts'] + [height]
            x, y = position
            if not (left <= xs[x] and right >= xs[x+1] and top <= ys[y] and bottom >= ys[y+1]):
                raise ValueError('Macro tile lacks its sampling region')
            if periodic and ((x == 0 and left != -gutter) or
                             (x == nx - 1 and right != width + gutter) or
                             (y == 0 and top != -gutter) or
                             (y == ny - 1 and bottom != height + gutter)):
                raise ValueError('Periodic macro tile lacks outer filtering gutter')
            file = Path(tile['file'])
            file = (file if file.is_absolute() else path.parent / file).resolve()
            tile['path'], tile['sha256'] = str(file), self._hash(file)
            self._png_metadata(file, [right - left, bottom - top], rgb=True)
            seen.add(position)
            tiles.append(tile)
        if len(seen) != nx * ny:
            raise ValueError('Incomplete macro tile grid')
        asset['macroTileSpec'] = str(path)
        asset['macroTileSpecSha256'] = self._hash(path)
        asset['macroTiles'] = tiles

    def texture_for(self, record, texture, role='detail'):
        """Return validated RGB replacement or None for an unselected binding."""
        name = record['name'] if isinstance(record, dict) else record
        asset = self._bindings.get(name, {}).get(texture.get('property'))
        if asset is None:
            return None
        if role != asset['role']:
            raise ValueError('Requested terrain role mismatch')
        if self._path(texture['export']) != Path(asset['source']):
            raise ValueError('Material texture source mismatch')
        if texture.get('pixels') != asset['sourcePixels'] or texture.get('colorSpace') != asset['colorSpace']:
            raise ValueError('Material texture metadata mismatch')
        if texture.get('textureSettings') != asset.get('textureSettings'):
            raise ValueError('Material texture sampling settings mismatch')
        self._verify(texture['export'], asset['sourceSha256'])
        return asset

    def iter_assets(self):
        return iter(self.assets)
