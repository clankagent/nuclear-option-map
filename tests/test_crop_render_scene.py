import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from crop_render_scene import clip_polygon, caster_offsets

class CropTests(unittest.TestCase):
    def test_casters_are_opposite_incoming_horizontal_ray(self):
        self.assertEqual(caster_offsets((3,0,-4),100),( -75.,0.,0.,0.))
        self.assertEqual(caster_offsets((-3,0,-4),100),(0.,0.,75.,0.))

    def test_angular_cone_enlarges_both_axis_bounds(self):
        zero=caster_offsets((0,0,-1),100)
        cone=caster_offsets((0,0,-1),100,.1)
        self.assertEqual(zero,(0.,0.,0.,0.))
        self.assertLess(cone[0],0); self.assertLess(cone[1],0)
        self.assertGreater(cone[2],0); self.assertGreater(cone[3],0)

    def test_exact_shadow_receiver_is_inside_conservative_bounds(self):
        import math
        axis=(.6,0,-.8); angle=.08; box=caster_offsets(axis,200,angle)
        for delta in (-angle/2,0,angle/2):
            ray=(.6*math.cos(delta)+.8*math.sin(delta),0,-.8*math.cos(delta)+.6*math.sin(delta))
            for height in (0,50,200):
                offset=-height*ray[0]/(-ray[2])
                self.assertLessEqual(box[0],offset); self.assertGreaterEqual(box[2],offset)

    def test_upward_or_horizon_sun_rejected(self):
        for ray in ((0,0,1),(1,0,-.01)):
            with self.assertRaises(ValueError): caster_offsets(ray,100,.1)

    def test_crossing_triangle_interpolates_uv_and_normal(self):
        points=[(-1,0,3,0,0,1,0,0),(1,0,3,0,0,1,1,0),(1,1,3,0,0,1,1,1)]
        result=clip_polygon(points,(0,-2,2,2))
        self.assertEqual(len(result),4)
        cut=[p for p in result if p[0]==0]
        self.assertEqual(len(cut),2)
        self.assertTrue(all(p[6]==.5 and p[2:6]==(3,0,0,1) for p in cut))

    def test_all_planes_bound_large_polygon(self):
        result=clip_polygon([(-10,-10),(10,-10),(10,10),(-10,10)],(-1,-2,3,4))
        self.assertEqual(set(result),{(-1,-2),(3,-2),(3,4),(-1,4)})

    def test_outside_is_empty_and_boundary_is_kept(self):
        self.assertEqual(clip_polygon([(5,5),(6,5),(5,6)],(0,0,1,1)),[])
        poly=[(0,0),(1,0),(0,1)]
        self.assertEqual(clip_polygon(poly,(0,0,1,1)),poly)

if __name__=='__main__': unittest.main()
