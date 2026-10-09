# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.3.4", "numba==0.62.1", "pillow==12.3.0"]
# ///
"""Analytic geometry checks independent of supplied game assets."""
import unittest
import tempfile
from pathlib import Path
import numpy as np
from raster_terrain import rasterize, local_matrix
from build_terrain_tiles import tin_contours,stitch_contours,simplify_path,tile_index,raster_tile,obj_face_slots


class RasterTests(unittest.TestCase):
    def test_unitypy_obj_material_slots_follow_groups_and_triangulation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"mesh.obj"
            path.write_text("g mesh\ng mesh_0\nf 1/1 2/2 3/3\ng mesh_1\nf 1/1 2/2 3/3 4/4\n")
            np.testing.assert_array_equal(obj_face_slots(path),[0,1,1])

    def test_north_south_and_pixel_center_elevation(self):
        vertices = np.array([[-1.,-1.,-1.],[1.,-1.,-1.],[1.,1.,1.],[-1.,1.,1.]])
        faces = np.array([[0,1,2],[0,2,3]],dtype=np.int32)
        heights = np.full((2,2),-np.inf,dtype=np.float32)
        rasterize(vertices,faces,heights,2.,2.)
        np.testing.assert_allclose(heights,[[.5,.5],[-.5,-.5]])

    def test_topmost_surface_wins_and_missing_stays_missing(self):
        vertices = np.array([[-1.,2.,-1.],[1.,2.,-1.],[-1.,2.,1.],[-1.,5.,-1.],[1.,5.,-1.],[-1.,5.,1.]])
        faces = np.array([[0,1,2],[3,4,5]],dtype=np.int32)
        heights = np.full((2,2),-np.inf,dtype=np.float32)
        rasterize(vertices,faces,heights,2.,2.)
        self.assertEqual(heights[1,0],5)
        self.assertFalse(np.isfinite(heights[0,1]))

    def test_serialized_rotation_scale_translation(self):
        tree = {"m_LocalPosition":{"x":10,"y":20,"z":30},
                "m_LocalRotation":{"x":0,"y":0,"z":2**-.5,"w":2**-.5},
                "m_LocalScale":{"x":2,"y":3,"z":4}}
        result = local_matrix(tree) @ np.array([1,0,0,1])
        np.testing.assert_allclose(result,[10,22,30,1],atol=1e-12)

    def test_native_contours_clip_stitch_and_ignore_roof_heights(self):
        vertices=np.array([[0.,0.,0.],[1.,1.,0.],[1.,2.,1.],[0.,1.,1.],
                           [0.,100.,0.],[1.,101.,0.],[1.,102.,1.]],dtype=np.float32)
        faces=np.array([[0,1,2],[0,2,3],[4,5,6]],dtype=np.int32)
        classes=np.array([1,1,2],dtype=np.uint8)
        segments=tin_contours(vertices,faces,classes,np.arange(3,dtype=np.int32),1.,np.array([.2,.2,.8,.8]))
        self.assertTrue(np.all(segments[:,4]==1))
        paths=stitch_contours(segments,precision=1)
        self.assertEqual(len(paths),1)
        simplified=simplify_path(paths[0][1],.01)
        self.assertEqual(len(simplified),2)
        np.testing.assert_allclose(np.sum(np.array(simplified),axis=1),[1,1])
        self.assertTrue(all(.2<=value<=.8 for point in simplified for value in point))

    def test_tile_footprints_do_not_invent_ground_under_roof(self):
        vertices=np.array([[-1.,10.,-1.],[1.,10.,-1.],[-1.,10.,1.]],dtype=np.float32)
        faces=np.array([[0,1,2]],dtype=np.int32);classes=np.array([2],dtype=np.uint8)
        ground=np.full((2,2),-np.inf,dtype=np.float32)
        visual=ground.copy();categories=np.zeros((2,2),dtype=np.uint8)
        raster_tile(vertices,faces,classes,np.array([0],dtype=np.int32),visual,categories,ground,-1.,1.,1.)
        self.assertFalse(np.any(np.isfinite(ground)))
        self.assertTrue(np.any(categories==2))
        self.assertTrue(np.any(np.isfinite(visual)))

    def test_adjacent_native_contour_tiles_share_exact_boundary_crossing(self):
        vertices=np.array([[0.,0.,0.],[2.,2.,0.],[2.,4.,2.],[0.,2.,2.]],dtype=np.float32)
        faces=np.array([[0,1,2],[0,2,3]],dtype=np.int32)
        classes=np.array([4,1],dtype=np.uint8);ids=np.array([0,1],dtype=np.int32)
        crossings=[]
        for bounds in ([0.,0.,1.,2.],[1.,0.,2.,2.]):
            segments=tin_contours(vertices,faces,classes,ids,2.,np.array(bounds))
            points=segments[:,:4].reshape(-1,2)
            crossings.append(points[np.isclose(points[:,0],1.)])
        np.testing.assert_allclose(crossings[0],crossings[1])
        np.testing.assert_allclose(crossings[0],[[1.,1.]])


if __name__ == "__main__":
    unittest.main()
