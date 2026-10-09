# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.3.4", "pillow==12.3.0", "numba==0.62.1"]
# ///
"""Small source-data tests for optional native-ground topography tiles."""
import unittest
import numpy as np
from build_topography_tiles import northwest_shade,pack_ground
class TopographyTests(unittest.TestCase):
    def test_northwest_orientation(self):
        y,x=np.mgrid[:5,:5];nw_facing=x.astype(float)*.5+y*.5;se_facing=-nw_facing
        self.assertGreater(northwest_shade(nw_facing,1)[2,2],northwest_shade(se_facing,1)[2,2])
        self.assertEqual(northwest_shade(np.ones((5,5)),1)[2,2],180)
    def test_unknown_neighbors_not_sea_walls(self):
        ground=np.full((5,5),100.);ground[:,0]=np.nan
        self.assertEqual(northwest_shade(ground,1)[2,1],180)
    def test_ground_mask_quantization(self):
        ground=np.array([[np.nan,-2,.1,258.04]])
        packed=pack_ground(ground,np.full(ground.shape,180),.1)
        self.assertEqual(packed[0,:2,3].tolist(),[0,0]);self.assertEqual(packed[0,2,3],255)
        self.assertAlmostEqual((int(packed[0,3,0])*256+int(packed[0,3,1]))*.1,258.0)
        self.assertTrue(np.isnan(ground[0,0]));self.assertEqual(ground[0,3],258.04)
if __name__=='__main__':unittest.main()


