# /// script
# requires-python = ">=3.11"
# dependencies = ["Pillow>=11,<13", "numpy>=2,<3", "scipy>=1.15,<2"]
# ///
"""Read-only, bounded exact-black candidate scan. Black is not a defect verdict.

uv run --script tools/audit_black_regions.py --level 6 --limit 64 --output work/black-artifacts-v1/scan
Scan explicit tiles with --tiles JSON containing [[x,y],...]. Four-connectivity
is reconciled across scanned tile cores only; gaps and unscanned neighbors stay
explicit. Memory is one tile plus compact component/boundary records. No render.
"""
import argparse
import hashlib
import io
import json
import math
from itertools import islice
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import urlopen

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage


def covered(level, x, y):
    return 0 <= x < level['columns'] and 0 <= y < level['rows'] and (
        'coverage' not in level or any(r['x'] <= x < r['x']+r['columns'] and
        r['y'] <= y < r['y']+r['rows'] for r in level['coverage']))


def tile_url(level, x, y):
    return level.get('urlOverrides', {}).get(f'{x}/{y}', level['urlTemplate'].replace(
        '{z}', str(level['level'])).replace('{x}', str(x)).replace('{y}', str(y)))


def validate_level(level, size, gutter):
    if type(size) is not int or not 1 <= size <= 2048 or type(gutter) is not int or not 0 <= gutter <= 8:
        raise ValueError('Tile size 1..2048 and gutter 0..8 required')
    if any(type(level.get(k)) is not int or level[k] < 1 for k in ('width','height','columns','rows')):
        raise ValueError('Positive integral level dimensions required')
    if level['columns'] != math.ceil(level['width']/size) or level['rows'] != math.ceil(level['height']/size):
        raise ValueError('Level dimensions do not match tile grid')
    for r in level.get('coverage', []):
        if any(type(r.get(k)) is not int for k in ('x','y','columns','rows')) or not (
            0 <= r['x'] < r['x']+r['columns'] <= level['columns'] and 0 <= r['y'] < r['y']+r['rows'] <= level['rows']):
            raise ValueError('Invalid coverage rectangle')


def select_coordinates(level, limit, tiles=None):
    if type(limit) is not int or not 1 <= limit <= 4096: raise ValueError('Tile limit must be 1..4096')
    if tiles is None:
        return list(islice(((x,y) for y in range(level['rows']) for x in range(level['columns']) if covered(level,x,y)),limit))
    if not isinstance(tiles,list) or len(tiles)>4096: raise ValueError('Explicit tile list must contain at most 4096 pairs')
    for pair in tiles:
        if not isinstance(pair,list) or len(pair)!=2 or any(type(v) is not int for v in pair):
            raise ValueError('Tile coordinates must be integer pairs, without booleans')
        x,y=pair
        if not 0 <= x < level['columns'] or not 0 <= y < level['rows']:
            raise ValueError('Tile coordinate outside level grid')
    return sorted(set(map(tuple,tiles)),key=lambda p:(p[1],p[0]))[:limit]


def tile_address(origin, url):
    return urljoin(urljoin(origin,'/maps/'),url)


def candidate_crop(rgba, box, x, y, size, gutter):
    a,b,c,d=box; cx=(a+c)//2-x*size+gutter; cy=(b+d)//2-y*size+gutter
    edge=min(256,rgba.shape[1],rgba.shape[0])
    left=max(0,min(rgba.shape[1]-edge,cx-edge//2)); top=max(0,min(rgba.shape[0]-edge,cy-edge//2))
    thumb=Image.fromarray(rgba[top:top+edge,left:left+edge,:3].copy())
    marker=ImageDraw.Draw(thumb)
    marker.rectangle((max(0,a-x*size+gutter-left-2),max(0,b-y*size+gutter-top-2),min(edge-1,c-x*size+gutter-left+1),min(edge-1,d-y*size+gutter-top+1)),outline='#ff4545')
    return thumb


def components(rgb):
    """Return labels and area, exclusive bbox, longest horizontal black run."""
    mask = np.all(rgb[:, :, :3] == 0, axis=2)
    labels, count = ndimage.label(mask)
    areas = np.bincount(labels.ravel(), minlength=count+1)
    runs = np.zeros(count+1, dtype=np.int64)
    for y, row in enumerate(mask):
        edges = np.flatnonzero(np.diff(np.r_[False, row, False]))
        for left, right in edges.reshape(-1, 2):
            k = labels[y, left]
            runs[k] = max(runs[k], right-left)
    records = []
    for k, slices in enumerate(ndimage.find_objects(labels), 1):
        ys, xs = slices
        records.append({'areaPixels': int(areas[k]), 'bboxPixels': [xs.start, ys.start, xs.stop, ys.stop],
                        'maxHorizontalRunPixels': int(runs[k])})
    return labels, records


class Union:
    def __init__(self): self.parents = []; self.records = []
    def add(self, record):
        k = len(self.parents); self.parents.append(k); self.records.append(record); return k
    def root(self, k):
        while self.parents[k] != k:
            self.parents[k] = self.parents[self.parents[k]]; k = self.parents[k]
        return k
    def join(self, a, b):
        a, b = self.root(a), self.root(b)
        if a != b: self.parents[b] = a
    def result(self):
        out = {}
        for k, rec in enumerate(self.records):
            root = self.root(k)
            if root not in out: out[root] = dict(rec, bboxPixels=list(rec['bboxPixels']), tileFragments=1, sourceTiles=[rec['sourceTile']] if 'sourceTile' in rec else [])
            else:
                r = out[root]; a = r['bboxPixels']; b = rec['bboxPixels']
                r['bboxPixels'] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                r['areaPixels'] += rec['areaPixels']; r['tileFragments'] += 1
                if 'sourceTile' in rec and rec['sourceTile'] not in r['sourceTiles']: r['sourceTiles'].append(rec['sourceTile'])
                r['maxHorizontalRunPixels'] = max(r['maxHorizontalRunPixels'], rec['maxHorizontalRunPixels'])
        return list(out.values())


def scan(args):
    if type(args.limit) is not int or not 1 <= args.limit <= 4096: raise ValueError('Tile limit must be 1..4096')
    raw = args.variants.read_bytes(); variants = json.loads(raw)
    catalog_raw=args.catalog.read_bytes(); catalog=json.loads(catalog_raw)
    maps={m['id']:m for m in catalog['maps']}
    explicit=None
    if args.tiles:
        if args.tiles.stat().st_size>1024*1024: raise ValueError('Tile selection exceeds 1 MiB')
        explicit=json.loads(args.tiles.read_text(encoding='utf-8-sig'))
    output = args.output; output.mkdir(parents=True, exist_ok=False)
    summaries = []
    for map_id, variants_for_map in variants['maps'].items():
        if args.map and args.map != map_id: continue
        variant = next(v for v in variants_for_map if v['label'] == 'Satellite')
        level = next(l for l in variant['tiles']['levels'] if l['level'] == args.level)
        size = variant['tiles']['tileSize']; gutter = variant['tiles'].get('gutter', 0)
        validate_level(level,size,gutter)
        definition=maps[map_id]; width=definition['widthMeters']; depth=definition['heightMeters']
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in (width,depth)):
            raise ValueError('Positive finite catalog world dimensions required')
        coords=select_coordinates(level,args.limit,explicit)
        union = Union(); boundary = {}; receipts = []; errors = []; tile_candidates = []; seam_runs = []
        for x, y in coords:
            if not covered(level, x, y): errors.append({'tile': [x,y], 'error': 'outside sparse coverage'}); continue
            url = tile_url(level, x, y)
            try:
                address=tile_address(args.origin,url)
                with urlopen(address, timeout=20) as response:
                    data = response.read(4*1024*1024+1)
                if len(data)>4*1024*1024: raise ValueError('Encoded tile bound exceeded')
                w, h = min(size, level['width']-x*size), min(size, level['height']-y*size)
                with Image.open(io.BytesIO(data)) as source:
                    if source.size != (w+2*gutter, h+2*gutter): raise ValueError('Unexpected tile dimensions')
                    rgba = np.asarray(source.convert('RGBA'))
                    if np.any(rgba[:,:,3] != 255): raise ValueError('Nonopaque tile')
                    rgb = rgba[gutter:gutter+h,gutter:gutter+w,:3].copy()
                labels, records = components(rgb); ids = {}
                for k, r in enumerate(records, 1):
                    a,b,c,d = r['bboxPixels']; r['bboxPixels'] = [a+x*size,b+y*size,c+x*size,d+y*size]
                    r['sourceTile'] = [x,y]; ids[k] = union.add(r)
                if len(union.records)>1_000_000: raise ValueError('Component count bound exceeded')
                ys, xs = np.nonzero(labels)
                for py in range(h):
                    edges = np.flatnonzero(np.diff(np.r_[False, labels[py]>0, False]))
                    for left,right in edges.reshape(-1,2):
                        if left == 0 or right == w:
                            seam_runs.append((py+y*size,int(left+x*size),int(right+x*size),ids[int(labels[py,left])]))
                edge = (xs==0)|(xs==w-1)|(ys==0)|(ys==h-1)
                for px,py in zip(xs[edge],ys[edge]):
                    gx,gy = int(px+x*size),int(py+y*size); own = ids[int(labels[py,px])]
                    for neighbor in ((gx-1,gy),(gx+1,gy),(gx,gy-1),(gx,gy+1)):
                        if neighbor in boundary: union.join(own,boundary[neighbor])
                    boundary[gx,gy] = own
                receipts.append({'tile':[x,y], 'url':url, 'sha256':hashlib.sha256(data).hexdigest(), 'blackPixels':int(len(xs)), 'components':len(records)})
                if records:
                    largest=max(records,key=lambda r:r['areaPixels'])
                    thumb=candidate_crop(rgba,largest['bboxPixels'],x,y,size,gutter)
                    tile_candidates.append((largest['areaPixels'],x,y,thumb))
                    # Sixteen 256-square RGB crops use at most 3 MiB decoded.
                    tile_candidates.sort(key=lambda t:t[:3],reverse=True)
                    if len(tile_candidates)>16: tile_candidates.pop()[3].close()
            except Exception as error: errors.append({'tile':[x,y], 'url':url, 'error':str(error)})
        # Adjacent row intervals yield the exact horizontal run through tile seams.
        run_max = {}; previous = None
        for gy,left,right,k in sorted(seam_runs):
            root = union.root(k)
            if previous and previous[0] == gy and previous[2] == left and previous[3] == root:
                previous = (gy,previous[1],right,root)
            else: previous = (gy,left,right,root)
            run_max[root] = max(run_max.get(root,0),previous[2]-previous[1])
        for k,r in enumerate(union.records):
            r['maxHorizontalRunPixels'] = max(r['maxHorizontalRunPixels'],run_max.get(union.root(k),0))
        candidates = union.result()
        for i,r in enumerate(sorted(candidates,key=lambda r:-r['areaPixels']),1):
            r['id']=i; a,b,c,d=r['bboxPixels']; r['worldBoundsXZ']=[-width/2+a*width/level['width'], depth/2-d*depth/level['height'], -width/2+c*width/level['width'], depth/2-b*depth/level['height']]
            r['areaSquareMeters']=r['areaPixels']*width/level['width']*depth/level['height']
        scanned={tuple(r['tile']) for r in receipts}
        partial_roots=set()
        for (gx,gy),k in boundary.items():
            tx,ty=gx//size,gy//size
            for nx,ny in ((gx-1,gy),(gx+1,gy),(gx,gy-1),(gx,gy+1)):
                if 0 <= nx < level['width'] and 0 <= ny < level['height'] and (nx//size,ny//size)!=(tx,ty) and (nx//size,ny//size) not in scanned:
                    partial_roots.add(union.root(k))
        # Result iteration follows each root's first fragment insertion order.
        roots=list(dict.fromkeys(union.root(k) for k in range(len(union.records))))
        for r,root in zip(candidates,roots): r['touchesUnscannedNeighbor']=root in partial_roots
        result={'map':map_id,'level':level,'metersPerPixel':width/level['width'],'variantsSha256':hashlib.sha256(raw).hexdigest(),'connectivity':4,'classification':'candidates only; exact black does not establish a defect','horizontalRunNote':'exact over scanned tile cores; unscanned coverage remains unknown','fill':variant.get('fillRgba8',variant.get('tiles',{}).get('fillRgba8')),'scannedTiles':receipts,'errors':errors,'components':sorted(candidates,key=lambda r:r['id'])}
        result['catalogSha256']=hashlib.sha256(catalog_raw).hexdigest()
        result['worldDimensionsMeters']=[width,depth]
        (output/(map_id+'.json')).write_text(json.dumps(result,indent=2))
        atlas=Image.new('RGB',(4*260,4*290),'#eeeeee'); draw=ImageDraw.Draw(atlas)
        for index,(_,x,y,thumb) in enumerate(tile_candidates):
            left=(index%4)*260; top=(index//4)*290; atlas.paste(thumb,(left,top)); draw.text((left+3,top+258),f'L{args.level} tile {x}/{y}',fill='black')
            thumb.close()
        atlas.save(output/(map_id+'-atlas.png'))
        summaries.append({'map':map_id,'scanned':len(receipts),'errors':len(errors),'components':len(candidates),'blackPixels':sum(r['areaPixels'] for r in candidates)})
    (output/'summary.json').write_text(json.dumps(summaries,indent=2)); print(json.dumps(summaries))


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--variants',type=Path,default=Path('demo/public/maps/variants.json'))
    p.add_argument('--catalog',type=Path,default=Path('demo/public/maps/manifest.json'))
    p.add_argument('--origin',default='http://127.0.0.1:47832/')
    p.add_argument('--map'); p.add_argument('--level',type=int,default=6); p.add_argument('--limit',type=int,default=64)
    p.add_argument('--tiles',type=Path); p.add_argument('--output',type=Path,required=True)
    scan(p.parse_args())
