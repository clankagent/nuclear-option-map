import importlib.util
from pathlib import Path
import unittest
import argparse
import io
import json
import tempfile
from unittest.mock import patch
import numpy as np
from PIL import Image

spec=importlib.util.spec_from_file_location('black',Path(__file__).parents[1]/'tools/audit_black_regions.py')
black=importlib.util.module_from_spec(spec); spec.loader.exec_module(black)

class BlackScanTests(unittest.TestCase):
    def test_exact_rgb_and_four_connectivity(self):
        image=np.full((4,5,3),255,dtype=np.uint8)
        image[0,0]=0; image[1,1]=0; image[2,1:4]=0; image[3,4]=[0,0,1]
        labels,records=black.components(image)
        self.assertEqual(len(records),2)
        self.assertEqual(sorted(r['areaPixels'] for r in records),[1,4])
        self.assertEqual(max(r['maxHorizontalRunPixels'] for r in records),3)
    def test_sparse_and_override(self):
        level={'columns':3,'rows':3,'level':10,'coverage':[{'x':1,'y':1,'columns':1,'rows':1}], 'urlTemplate':'a/{z}/{x}/{y}', 'urlOverrides':{'1/1':'patched'}}
        self.assertFalse(black.covered(level,0,0)); self.assertTrue(black.covered(level,1,1))
        self.assertEqual(black.tile_url(level,1,1),'patched')
        self.assertEqual(black.tile_url(level,2,2),'a/10/2/2')
    def test_seam_component_union(self):
        u=black.Union()
        a=u.add({'areaPixels':2,'bboxPixels':[510,0,512,1],'maxHorizontalRunPixels':2})
        b=u.add({'areaPixels':3,'bboxPixels':[512,0,515,1],'maxHorizontalRunPixels':3})
        u.join(a,b); r=u.result()[0]
        self.assertEqual(r['areaPixels'],5); self.assertEqual(r['bboxPixels'],[510,0,515,1]); self.assertEqual(r['tileFragments'],2)
    def test_actual_scan_seam_gutters_and_world_bounds(self):
        # Gutter black must never contribute; five core pixels cross the seam.
        tiles=[]
        for x in range(2):
            a=np.full((6,6,3),255,dtype=np.uint8); a[0]=0
            a[2,3:5] = 0 if x==0 else 255
            if x==1: a[2,1:4]=0
            stream=io.BytesIO(); Image.fromarray(a).save(stream,format='PNG'); tiles.append(stream.getvalue())
        def fetch(url,timeout=0): return io.BytesIO(tiles[1 if '/1/0' in url else 0])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); variants=root/'variants.json'
            variants.write_text(json.dumps({'maps':{'heartland-46396':[{'label':'Satellite','tiles':{'tileSize':4,'gutter':1,'levels':[{'level':0,'width':8,'height':4,'columns':2,'rows':1,'urlTemplate':'test/{x}/{y}'}]}}]}}))
            catalog=root/'catalog.json'; catalog.write_text(json.dumps({'maps':[{'id':'heartland-46396','widthMeters':80,'heightMeters':40}]}))
            args=argparse.Namespace(variants=variants,catalog=catalog,output=root/'out',map=None,level=0,tiles=None,limit=2,origin='http://localhost/')
            with patch.object(black,'urlopen',side_effect=fetch) as requests: black.scan(args)
            self.assertEqual(requests.call_count,2) # Atlas uses the scanned pixels, without refetching.
            result=json.loads((root/'out/heartland-46396.json').read_text())
            self.assertEqual(result['errors'],[]); self.assertEqual(len(result['components']),1)
            record=result['components'][0]
            self.assertEqual(record['areaPixels'],5); self.assertEqual(record['maxHorizontalRunPixels'],5)
            self.assertEqual(record['bboxPixels'],[2,1,7,2]); self.assertEqual(record['tileFragments'],2)
            self.assertEqual(record['worldBoundsXZ'],[-20,0,30,10])
            self.assertEqual(record['areaSquareMeters'],500)
    def test_url_resolution(self):
        origin='http://127.0.0.1:47832/'
        self.assertEqual(black.tile_address(origin,'https://example.com/tile.webp?v=1'),'https://example.com/tile.webp?v=1')
        self.assertEqual(black.tile_address(origin,'/maps/foo.webp'),'http://127.0.0.1:47832/maps/foo.webp')
        self.assertEqual(black.tile_address(origin,'terrain/foo.webp'),'http://127.0.0.1:47832/maps/terrain/foo.webp')
    def test_default_selection_is_lazy(self):
        level={'columns':10**9,'rows':10**9}
        with patch.object(black,'covered',return_value=True) as calls:
            self.assertEqual(black.select_coordinates(level,3),[(0,0),(1,0),(2,0)])
            self.assertEqual(calls.call_count,3)
    def test_invalid_selection_and_grid_rejected(self):
        level={'columns':2,'rows':2,'width':8,'height':8}
        for selected in ([[True,0]],[[0.5,0]],[[0]],[[2,0]],[[-1,0]],{'0/0':True}):
            with self.subTest(selected=selected),self.assertRaises(ValueError):
                black.select_coordinates(level,4,selected)
        for limit in (0,4097,True):
            with self.assertRaises(ValueError): black.select_coordinates(level,limit)
        for size,gutter in ((0,0),(True,0),(2049,0),(4,-1),(4,True)):
            with self.assertRaises(ValueError): black.validate_level(level,size,gutter)
        with self.assertRaises(ValueError): black.validate_level(dict(level,columns=3),4,1)

if __name__=='__main__': unittest.main()
