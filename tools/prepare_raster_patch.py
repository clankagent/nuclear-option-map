# /// script
# requires-python = ">=3.11"
# dependencies = ["Pillow>=11,<13", "numpy>=2,<3"]
# ///
"""Build a static candidate from finest-tile replacements. No rendering or promotion.

Baseline files must be immutable and previously accepted. This tool checks required
file presence, but does not audit every baseline pixel or hash the entire baseline.
Per-level roots use string level keys and directories containing x/y.webp.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import shutil
from collections import OrderedDict
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, __version__ as pillow_version, features


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def ordinary(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlinks refused: ' + str(path))
    if not path.is_file():
        raise ValueError('Missing covered tile: ' + str(path))
    return path


def core_size(level, x, y, size):
    return min(size, level['width']-x*size), min(size, level['height']-y*size)


def covered(level, x, y):
    return 'coverage' not in level or any(r['x'] <= x < r['x']+r['columns'] and
        r['y'] <= y < r['y']+r['rows'] for r in level['coverage'])


def coordinates(level):
    # Stream the advertised grid; overlapping coverage rectangles never duplicate files.
    for x in range(level['columns']):
        for y in range(level['rows']):
            if covered(level, x, y):
                yield x, y


def load_rgb(path, expected):
    ordinary(path)
    with Image.open(path) as image:
        if image.size != expected:
            raise ValueError('Tile dimensions differ: ' + str(path))
        image.load()
        if image.mode not in ('RGB', 'RGBA') or (image.mode == 'RGBA' and image.getchannel('A').getextrema() != (255, 255)):
            raise ValueError('Opaque RGB tile required: ' + str(path))
        return image.convert('RGB')


def save_tile(image, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    image.save(temporary, format='WEBP', lossless=True, method=0)
    os.replace(temporary, destination)  # Break a reused hardlink before writing.


class Reader:
    def __init__(self, root, size, fill):
        self.root, self.size, self.fill = Path(root), size, fill
        self.cache = OrderedDict()
        self.hashes = {}
        self.inputs = {}

    def region(self, level, left, top, width, height):
        size = self.size
        xs = np.clip(np.arange(left, left+width), 0, level['width']-1)
        ys = np.clip(np.arange(top, top+height), 0, level['height']-1)
        result = np.empty((height, width, 3), dtype=np.uint8)
        for tx in np.unique(xs//size):
            ix = np.flatnonzero(xs//size == tx)
            for ty in np.unique(ys//size):
                iy = np.flatnonzero(ys//size == ty)
                x, y = int(tx), int(ty)
                if not covered(level, x, y):
                    if self.fill is None:
                        raise ValueError('Uncovered reads require explicit opaque fillRgba8')
                    result[np.ix_(iy, ix)] = self.fill[:3]
                    continue
                key = (level['level'], x, y)
                path = self.root/str(key[0])/str(x)/f'{y}.webp'
                if key not in self.cache:
                    w, h = core_size(level, x, y, size)
                    image = load_rgb(path, (w+4, h+4))
                    self.cache[key] = np.asarray(image)
                    self.hashes[key] = sha(path)
                    image.close()
                    if len(self.cache) > 16:
                        evicted, _ = self.cache.popitem(last=False)
                        self.hashes.pop(evicted)
                self.inputs['/'.join(map(str, key))] = self.hashes[key]
                pixels = self.cache[key]
                result[np.ix_(iy, ix)] = pixels[np.ix_(ys[iy]-y*size+2, xs[ix]-x*size+2)]
        return Image.fromarray(result)

    def derive(self, child, parent, x, y):
        w, h = core_size(parent, x, y, self.size)
        source = self.region(child, (x*self.size-2)*2-6, (y*self.size-2)*2-6,
                             (w+4)*2+12, (h+4)*2+12)
        scaled = source.resize((source.width//2, source.height//2), Image.Resampling.LANCZOS)
        return scaled.crop((3, 3, 3+w+4, 3+h+4))


def difference(old, new, level, x, y, size):
    w, h = core_size(level, x, y, size)
    box = ImageChops.difference(old.crop((2, 2, w+2, h+2)), new.crop((2, 2, w+2, h+2))).getbbox()
    return None if box is None else (box[0]+x*size, box[1]+y*size, box[2]+x*size, box[3]+y*size)


def dependent_tiles(parent, boxes, size):
    candidates = set()
    for left, top, right, bottom in boxes:
        # Support is [2*x*T-10, 2*(x*T+coreWidth)+10).
        for x in range(max(0, (left-10)//(2*size)), min(parent['columns'], (right+10)//(2*size)+1)):
            for y in range(max(0, (top-10)//(2*size)), min(parent['rows'], (bottom+10)//(2*size)+1)):
                w, h = core_size(parent, x, y, size)
                if covered(parent, x, y) and left < 2*(x*size+w)+10 and right > 2*x*size-10 and top < 2*(y*size+h)+10 and bottom > 2*y*size-10:
                    candidates.add((x, y))
    return sorted(candidates)


def prepare(base_manifest, base_root, replacements, output, base_roots=None, copy_base=False, plan_only=False):
    base_manifest, replacements = Path(base_manifest), Path(replacements)
    ordinary(base_manifest); ordinary(replacements)
    base_bytes = base_manifest.read_bytes(); plan_bytes = replacements.read_bytes()
    base_hash = hashlib.sha256(base_bytes).hexdigest()
    plan_hash = hashlib.sha256(plan_bytes).hexdigest()
    implementation_hash = sha(Path(__file__))
    manifest = json.loads(base_bytes.decode('utf-8-sig'))
    plan = json.loads(plan_bytes.decode('utf-8-sig'))

    def verify_inputs():
        if sha(base_manifest) != base_hash or sha(replacements) != plan_hash or sha(Path(__file__)) != implementation_hash:
            raise ValueError('Descriptor, replacement plan, or implementation changed during preparation')
    if manifest.get('schemaVersion') != 1 or plan.get('schemaVersion') != 1:
        raise ValueError('schemaVersion 1 required')
    metadata = manifest['tiles']; size = metadata['tileSize']; levels = metadata['levels']
    if type(size) is not int or not 64 <= size <= 2048 or metadata.get('gutter') != 2 or not levels:
        raise ValueError('Tile size 64..2048 and gutter 2 required')
    for z, level in enumerate(levels):
        if level['level'] != z or any(type(level[k]) is not int or level[k] < 1 for k in ('width', 'height', 'columns', 'rows')):
            raise ValueError('Positive consecutive integral levels required')
        if level['columns'] != math.ceil(level['width']/size) or level['rows'] != math.ceil(level['height']/size):
            raise ValueError('Invalid level grid')
        if z and any((level[k]+1)//2 != levels[z-1][k] for k in ('width', 'height')):
            raise ValueError('Parent dimensions must equal ceil(child / 2)')
        for r in level.get('coverage', []):
            if any(type(r[k]) is not int for k in ('x', 'y', 'columns', 'rows')) or not (0 <= r['x'] < r['x']+r['columns'] <= level['columns'] and 0 <= r['y'] < r['y']+r['rows'] <= level['rows']):
                raise ValueError('Invalid coverage rectangle')
    fill = manifest.get('fillRgba8')
    if fill is not None and (not isinstance(fill, list) or len(fill) != 4 or any(type(v) is not int or not 0 <= v <= 255 for v in fill) or fill[3] != 255):
        raise ValueError('Opaque fillRgba8 required')
    level_zero = levels[0]
    if max(level_zero['width'], level_zero['height']) > 4096 or level_zero['width']*level_zero['height'] > 4_000_000:
        raise ValueError('Level zero exceeds bounded overview canvas')
    if not isinstance(plan.get('tiles'), list) or not plan['tiles']:
        raise ValueError('Nonempty replacement tiles required')
    root = Path(base_root).absolute(); output = Path(output).absolute()
    overrides = base_roots or {}
    if any(not isinstance(z, str) for z in overrides):
        raise ValueError('Base root level keys must be strings, for example "10"')
    if any(z not in {str(i) for i in range(len(levels))} for z in overrides):
        raise ValueError('Unknown base root level')
    roots = {z: Path(overrides.get(str(z), root/str(z))).absolute() for z in range(len(levels))}
    for source in (root, *roots.values(), base_manifest, replacements):
        a, b = source.resolve(), output.resolve()
        if a == b or a.is_relative_to(b) or b.is_relative_to(a):
            raise ValueError('Separate nonoverlapping output required')
    if output.exists():
        raise ValueError('Fresh output directory required')
    finest = levels[-1]; z = finest['level']; validated = {}; bound = []
    for tile in plan['tiles']:
        x, y = tile['x'], tile['y']
        if type(x) is not int or type(y) is not int or not 0 <= x < finest['columns'] or not 0 <= y < finest['rows'] or not covered(finest, x, y):
            raise ValueError('Replacement outside existing finest coverage')
        if (x, y) in validated:
            raise ValueError('Duplicate replacement')
        path = replacements.parent/Path(tile['path'])
        ordinary(path)
        if sha(path) != tile['sha256']:
            raise ValueError('Replacement SHA-256 differs')
        w, h = core_size(finest, x, y, size)
        image = load_rgb(path, (w+4, h+4)); image.close()
        validated[x, y] = (path, tile['sha256'])
        bound.append(dict(x=x, y=y, sha256=tile['sha256']))
    # Presence checks precede publication. Baseline pixel validation is demand-driven.
    for level in levels:
        for x, y in coordinates(level):
            ordinary(roots[level['level']]/str(x)/f'{y}.webp')
    if plan_only:
        boxes = [(x*size, y*size, x*size+core_size(finest, x, y, size)[0], y*size+core_size(finest, x, y, size)[1]) for x, y in validated]
        counts = {}
        for child, parent in zip(reversed(levels[1:]), reversed(levels[:-1])):
            dirty = dependent_tiles(parent, boxes, size); counts[str(parent['level'])] = len(dirty)
            boxes = [(x*size, y*size, x*size+core_size(parent, x, y, size)[0], y*size+core_size(parent, x, y, size)[1]) for x, y in dirty]
        verify_inputs()
        return None, dict(planOnly=True, noRender=True, promoted=False, replacementCount=len(validated), conservativeDerivedCounts=counts, baselineDescriptorSha256=base_hash, replacementPlanSha256=plan_hash)
    output.mkdir(parents=True)
    reused = 0
    for level in levels:
        for x, y in coordinates(level):
            destination = output/str(level['level'])/str(x)/f'{y}.webp'
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = roots[level['level']]/str(x)/f'{y}.webp'
            if copy_base:
                shutil.copyfile(source, destination)
            else:
                os.link(source, destination)
            reused += 1
    staged = []; boxes = []
    for (x, y), (path, expected_hash) in validated.items():
        w, h = core_size(finest, x, y, size)
        image = load_rgb(path, (w+4, h+4))
        if sha(path) != expected_hash:
            raise ValueError('Replacement changed after validation')
        destination = output/str(z)/str(x)/f'{y}.webp'
        old = load_rgb(destination, image.size)
        if ImageChops.difference(old, image).getbbox() is not None:
            box = difference(old, image, finest, x, y, size)
            if box: boxes.append(box)
            save_tile(image, destination); reused -= 1
            staged.append(dict(level=z, x=x, y=y, kind='replacement', coreDifference=box, outputSha256=sha(destination), outputBytes=destination.stat().st_size))
        old.close(); image.close()
    reader = Reader(output, size, fill); derived = 0
    for child, parent in zip(reversed(levels[1:]), reversed(levels[:-1])):
        next_boxes = []
        for x, y in dependent_tiles(parent, boxes, size):
            reader.inputs = {}
            image = reader.derive(child, parent, x, y); derived += 1
            destination = output/str(parent['level'])/str(x)/f'{y}.webp'
            old = load_rgb(destination, image.size)
            if ImageChops.difference(old, image).getbbox() is not None:
                box = difference(old, image, parent, x, y, size)
                if box: next_boxes.append(box)
                save_tile(image, destination); reused -= 1
                staged.append(dict(level=parent['level'], x=x, y=y, kind='derived', coreDifference=box, inputs=reader.inputs, outputSha256=sha(destination), outputBytes=destination.stat().st_size))
        boxes = next_boxes
    level = levels[0]
    overview = reader.region(level, 0, 0, level['width'], level['height'])
    save_tile(overview, output/'overview.webp')
    candidate = copy.deepcopy(manifest); candidate['image'] = 'overview.webp'
    runtime = dict(pillow=pillow_version, numpy=np.__version__, webp=features.version('webp'))
    version = hashlib.sha256((base_hash+plan_hash+implementation_hash+json.dumps(runtime, sort_keys=True)).encode()).hexdigest()[:20]
    candidate['patch'] = dict(receipt='patch-receipt.json', baselineProofScope='Inherited provenance describes baseline only; not candidate acceptance', implementationSha256=implementation_hash)
    for level in candidate['tiles']['levels']:
        level['urlTemplate'] = '{z}/{x}/{y}.webp?v='+version
    receipt = dict(schemaVersion=1, implementationSha256=implementation_hash, runtime=runtime, baselineDescriptorSha256=base_hash, replacementPlanSha256=plan_hash,
                   replacements=bound, staged=staged, stagedCount=len(staged), changedCoreCount=sum(t['coreDifference'] is not None for t in staged),
                   derivedCount=derived, reusedCount=reused, overviewSha256=sha(output/'overview.webp'), noRender=True, promoted=False,
                   baselineAudit='Required file presence only; baseline must be immutable and previously accepted. Not a whole-baseline hash audit.',
                   resampling='Child cores; map-edge clamp; 6 child-pixel support; half-size Lanczos; crop 3; lossless WebP method 0')
    verify_inputs()
    for name, value in (('pyramid.json', candidate), ('patch-receipt.json', receipt)):
        (output/name).write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')
    return candidate, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base-manifest', 'base-root', 'replacements', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--base-roots', type=Path, help='JSON object: level number to directory containing x/y.webp; paths relative to this JSON')
    parser.add_argument('--plan-only', action='store_true', help='Validate inputs and print conservative dependency counts; create no files')
    parser.add_argument('--copy-base', action='store_true', help='Copy unchanged tiles instead of hardlinking; can use substantial disk space')
    args = parser.parse_args(); overrides = None
    if args.base_roots:
        ordinary(args.base_roots)
        overrides = {str(z): args.base_roots.parent/Path(path) for z, path in json.loads(args.base_roots.read_text(encoding='utf-8-sig')).items()}
    _, receipt = prepare(args.base_manifest, args.base_root, args.replacements, args.output, overrides, args.copy_base, args.plan_only)
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
