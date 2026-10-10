import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from crop_render_scene import clip_polygon, caster_offsets, projected_shadow_bounds, direct_caster_region, retained_material_slots, boxes_intersect, periodic_crop_axis, exact_zero_channels, exact_endpoint_channels, prune_zero_weight_branches, interpolation_gutter, recover_uv_affine, transformed_uvs, affine_row_product
from types import SimpleNamespace

class Nodes(dict):
    def __iter__(self): return iter(self.values())
    def remove(self,node): del self[node.name]

class Socket:
    def __init__(self,node,name,kind='VALUE',value=0):
        self.node=node; self.name=name; self.type=kind; self.default_value=value; self.links=[]
    @property
    def is_linked(self): return bool(self.links)

class Links:
    def new(self,source,target):
        link=SimpleNamespace(from_socket=source,to_socket=target,from_node=source.node,to_node=target.node)
        source.links.append(link); target.links.append(link)
    def remove(self,link):
        link.from_socket.links.remove(link); link.to_socket.links.remove(link)

def fixture_node(nodes,name,kind,inputs,outputs,**kwargs):
    node=SimpleNamespace(name=name,type=kind,**kwargs)
    node.inputs=[Socket(node,str(i),k,v) for i,(k,v) in enumerate(inputs)]
    node.outputs=[Socket(node,str(i),k) for i,k in enumerate(outputs)]
    nodes[name]=node
    return node

class CropTests(unittest.TestCase):
    def test_implicit_socket_conversion_fails_closed(self):
        nodes=Nodes(); links=Links()
        value=fixture_node(nodes,'value','VALUE',[],['VALUE'])
        value.outputs[0].default_value=1
        multiply=fixture_node(nodes,'multiply','VECT_MATH',[('VECTOR',(0,0,0)),('VECTOR',(1,1,1))],['VECTOR'],operation='MULTIPLY')
        links.new(value.outputs[0],multiply.inputs[0])
        self.assertIsNone(recover_uv_affine(multiply.outputs[0]))
        uv=fixture_node(nodes,'uv','TEX_COORD',[],['VECTOR']); uv.outputs[0].name='UV'
        scalar=fixture_node(nodes,'scalar','MATH',[('VALUE',0),('VALUE',1)],['VALUE'],operation='MULTIPLY')
        links.new(uv.outputs[0],scalar.inputs[0])
        self.assertIsNone(recover_uv_affine(scalar.outputs[0]))

    def test_tight_kernel_gutters_keep_all_source_neighbors_at_repeat_seam(self):
        import math
        for interpolation in ('Closest','Linear','Cubic'):
            indices,origin,full=periodic_crop_axis(.97,1.03,100,interpolation_gutter(interpolation))
            self.assertFalse(full)
            for step in range(101):
                uv=.97+.06*step/100
                center=math.floor(uv*100-.5)
                kernel=(math.floor(uv*100),) if interpolation=='Closest' else range(center,center+2) if interpolation=='Linear' else range(center-1,center+3)
                for source in kernel: self.assertEqual(indices[source-origin],source%100)

    def test_recovered_rotated_uvs_preserve_cross_axis_coefficients(self):
        import math
        nodes=Nodes(); links=Links()
        uv=fixture_node(nodes,'uv','TEX_COORD',[],['VECTOR']); uv.outputs[0].name='UV'
        scale=fixture_node(nodes,'scale','VECT_MATH',[('VECTOR',0),('VECTOR',0),('VECTOR',0),('VALUE',100)],['VECTOR'],operation='SCALE')
        subtract=fixture_node(nodes,'offset','VECT_MATH',[('VECTOR',0),('VECTOR',(.5,.5,0))],['VECTOR'],operation='SUBTRACT')
        split=fixture_node(nodes,'split','SEPXYZ',[('VECTOR',0)],['VALUE']*3)
        for socket,name in zip(split.outputs,'XYZ'): socket.name=name
        add=fixture_node(nodes,'sum','MATH',[('VALUE',0),('VALUE',0)],['VALUE'],operation='ADD')
        diff=fixture_node(nodes,'difference','MATH',[('VALUE',0),('VALUE',0)],['VALUE'],operation='SUBTRACT')
        c=math.sqrt(.5)
        x=fixture_node(nodes,'rotatex','MATH',[('VALUE',0),('VALUE',c)],['VALUE'],operation='MULTIPLY')
        y=fixture_node(nodes,'rotatey','MATH',[('VALUE',0),('VALUE',c)],['VALUE'],operation='MULTIPLY')
        combine=fixture_node(nodes,'combine','COMBXYZ',[('VALUE',0)]*3,['VECTOR'])
        final=fixture_node(nodes,'final','VECT_MATH',[('VECTOR',0),('VECTOR',(.5,.5,0))],['VECTOR'],operation='ADD')
        for source,target in ((uv.outputs[0],scale.inputs[0]),(scale.outputs[0],subtract.inputs[0]),(subtract.outputs[0],split.inputs[0]),(split.outputs[0],add.inputs[0]),(split.outputs[1],add.inputs[1]),(split.outputs[1],diff.inputs[0]),(split.outputs[0],diff.inputs[1]),(add.outputs[0],x.inputs[0]),(diff.outputs[0],y.inputs[0]),(x.outputs[0],combine.inputs[0]),(y.outputs[0],combine.inputs[1]),(combine.outputs[0],final.inputs[0])): links.new(source,target)
        rows=recover_uv_affine(final.outputs[0])
        self.assertEqual(rows,((100*c,100*c,.5-c),(-100*c,100*c,.5),(0,0,0)))
        coordinates=[(.64,.60),(.641,.603)]
        us,vs=transformed_uvs(rows[0]+rows[1],coordinates)
        for (u,v),tx,ty in zip(coordinates,us,vs):
            self.assertAlmostEqual(tx,((100*u-.5)+(100*v-.5))*c+.5)
            self.assertAlmostEqual(ty,((100*v-.5)-(100*u-.5))*c+.5)
        self.assertIsNone(recover_uv_affine(final.outputs[0],lambda node:False))
        self.assertIsNone(affine_row_product((1,0,0),(1,0,0)))

    def test_clip_endpoint_proof_rejected_without_inbounds_uv_proof(self):
        opaque_white=[(1,1,1,1)]
        self.assertEqual(exact_endpoint_channels(opaque_white,'sRGB','CLIP'),(None,)*4)
        self.assertEqual(exact_endpoint_channels(opaque_white,'sRGB','EXTEND'),(1,)*4)
        self.assertEqual(exact_endpoint_channels([(0,0,0,1)],'Non-Color','CLIP'),(None,)*4)

    def test_clamped_math_chain_does_not_create_false_endpoint(self):
        nodes=Nodes(); links=Links(); tree=SimpleNamespace(nodes=nodes,links=links)
        clamped=fixture_node(nodes,'clamped','MATH',[('VALUE',2),('VALUE',1)],['VALUE'],operation='MULTIPLY',use_clamp=True)
        weight=fixture_node(nodes,'weight','MATH',[('VALUE',0),('VALUE',.5)],['VALUE'],operation='MULTIPLY',use_clamp=False)
        mix=fixture_node(nodes,'mix','MIX_RGB',[('VALUE',0),('RGBA',0),('RGBA',0)],['RGBA'],blend_type='MIX',use_clamp=False,use_alpha=False)
        output=fixture_node(nodes,'output','OUTPUT_MATERIAL',[('RGBA',0)],[])
        links.new(clamped.outputs[0],weight.inputs[0]); links.new(weight.outputs[0],mix.inputs[0])
        links.new(mix.outputs[0],output.inputs[0])
        self.assertEqual(prune_zero_weight_branches(tree,[])['specializations'],[])
        self.assertTrue(mix.inputs[0].is_linked)
        self.assertIn('mix',nodes)

    def test_clipped_faces_drop_unused_material_residency_and_remap_assignments(self):
        used,remap=retained_material_slots([7,2,7,2],9)
        self.assertEqual(used,[2,7])
        self.assertEqual([remap[i] for i in [7,2,7,2]],[1,0,1,0])
        self.assertEqual(retained_material_slots([0],0),([],{0:0}))
        with self.assertRaises(ValueError): retained_material_slots([9],9)

    def test_giant_water_plane_does_not_expand_receiver_bounds(self):
        receiver=(10,20,30,40)
        plane=[(-10000,-10000,0),(10000,-10000,0),(0,10000,0)]
        region=direct_caster_region(plane,receiver,0,(.6,0,-.8),.01)
        self.assertEqual({tuple(round(v,8) for v in p[:2]) for p in region},{(10,20),(30,20),(30,40),(10,40)})
        self.assertTrue(all(p[2]==0 for p in region))

    def test_giant_high_plane_keeps_only_required_shadow_corridor(self):
        plane=[(-10000,-10000,100),(10000,-10000,100),(0,10000,100)]
        region=direct_caster_region(plane,(10,20,30,40),0,(3,0,-4))
        self.assertEqual({tuple(round(v,8) for v in p[:2]) for p in region},{(-65,20),(30,20),(30,40),(-65,40)})
        self.assertEqual(direct_caster_region(plane,(10,20,30,40),101,(3,0,-4)),[])

    def test_local_projection_uses_actual_receiver_height(self):
        triangle=[(0,0,150),(10,0,200),(0,10,150)]
        self.assertEqual(projected_shadow_bounds(triangle,100,(3,0,-4)),(0,0,85,10))
        self.assertIsNone(projected_shadow_bounds(triangle,201,(3,0,-4)))

    def test_sloped_triangle_shadow_points_inside_angular_box(self):
        import math
        triangle=[(-10,5,25),(30,-15,120),(5,35,180)]
        box=projected_shadow_bounds(triangle,10,(.6,0,-.8),.08)
        for a in (0,.25,.5,.75,1):
            point=[a*triangle[0][i]+(1-a)*triangle[2][i] for i in range(3)]
            for receiver_z in (10,15,point[2]):
                for delta in (-.04,0,.04):
                    ray=(.6*math.cos(delta)+.8*math.sin(delta),0,-.8*math.cos(delta)+.6*math.sin(delta))
                    height=point[2]-receiver_z
                    shadow=(point[0]+height*ray[0]/(-ray[2]),point[1])
                    self.assertLessEqual(box[0],shadow[0]); self.assertGreaterEqual(box[2],shadow[0])
                    self.assertLessEqual(box[1],shadow[1]); self.assertGreaterEqual(box[3],shadow[1])

    def test_shadow_intersection_keeps_touching_caster_boundary(self):
        shadow=projected_shadow_bounds([(0,0,4),(0,1,4),(1,0,4)],0,(1,0,-1))
        self.assertTrue(boxes_intersect(shadow,(5,0,6,1)))
        self.assertFalse(boxes_intersect(shadow,(5.01,0,6,1)))

    def test_local_receiver_minimum_interpolates_clipped_heights(self):
        clipped=clip_polygon([(-1,0,0),(1,0,20),(1,1,30)],(0,-1,2,2))
        self.assertEqual(min(p[2] for p in clipped),10)
        triangle=[(0,0,20),(1,0,20),(0,1,20)]
        self.assertEqual(projected_shadow_bounds(triangle,10,(0,0,-1)),(0,0,1,1))

    def test_opaque_srgb_endpoints_proved_without_promoting_near_endpoints(self):
        self.assertEqual(exact_endpoint_channels([(0,1,.999999,1),(0,1,1,1)],'sRGB'),(0,1,None,1))
        self.assertEqual(exact_endpoint_channels([(0,1,0,.5)],'sRGB'),(None,)*4)
        self.assertEqual(exact_endpoint_channels([(0,1,0,1)],'unknown'),(None,)*4)
        self.assertEqual(exact_endpoint_channels([(0,1,0,.5)],'Non-Color'),(0,None,0,None))

    def test_proved_endpoint_one_mix_keeps_only_second_color(self):
        nodes=Nodes(); links=Links(); tree=SimpleNamespace(nodes=nodes,links=links)
        proof=fixture_node(nodes,'proof','SEPXYZ',[],['VALUE'])
        first=fixture_node(nodes,'first','TEX_IMAGE',[],['RGBA'])
        second=fixture_node(nodes,'second','TEX_IMAGE',[],['RGBA'])
        mix=fixture_node(nodes,'mix','MIX_RGB',[('VALUE',.5),('RGBA',0),('RGBA',0)],['RGBA'],blend_type='MIX',use_clamp=False,use_alpha=False)
        output=fixture_node(nodes,'output','OUTPUT_MATERIAL',[('RGBA',0)],[])
        links.new(proof.outputs[0],mix.inputs[0]); links.new(first.outputs[0],mix.inputs[1])
        links.new(second.outputs[0],mix.inputs[2]); links.new(mix.outputs[0],output.inputs[0])
        result=prune_zero_weight_branches(tree,[(proof.outputs[0],1)])
        self.assertIs(output.inputs[0].links[0].from_node,second)
        self.assertIn('first',result['removedNodes'])

    def test_unproved_nonlinear_weight_remains_linked(self):
        nodes=Nodes(); links=Links(); tree=SimpleNamespace(nodes=nodes,links=links)
        nonlinear=fixture_node(nodes,'nonlinear','MATH',[('VALUE',.5)],['VALUE'],operation='SINE')
        mix=fixture_node(nodes,'mix','MIX_RGB',[('VALUE',0),('RGBA',0),('RGBA',0)],['RGBA'],blend_type='MIX',use_clamp=False,use_alpha=False)
        output=fixture_node(nodes,'output','OUTPUT_MATERIAL',[('RGBA',0)],[])
        links.new(nonlinear.outputs[0],mix.inputs[0]); links.new(mix.outputs[0],output.inputs[0])
        self.assertEqual(prune_zero_weight_branches(tree,[])['specializations'],[])
        self.assertTrue(mix.inputs[0].is_linked)
        self.assertIn('nonlinear',nodes)

    def test_zero_weight_prunes_only_unreachable_texture_branch(self):
        nodes=Nodes(); links=Links(); tree=SimpleNamespace(nodes=nodes,links=links)
        proof=fixture_node(nodes,'proof','SEPXYZ',[],['VALUE'])
        weight=fixture_node(nodes,'weight','MATH',[('VALUE',1),('VALUE',2)],['VALUE'],operation='MULTIPLY')
        texture=fixture_node(nodes,'texture','TEX_IMAGE',[],['VECTOR'])
        scale=fixture_node(nodes,'scale','VECT_MATH',[('VECTOR',(1,1,1)),('VECTOR',0),('VECTOR',0),('VALUE',1)],['VECTOR'],operation='SCALE')
        output=fixture_node(nodes,'output','OUTPUT_MATERIAL',[('VECTOR',0)],[])
        links.new(proof.outputs[0],weight.inputs[0]); links.new(weight.outputs[0],scale.inputs[3])
        links.new(texture.outputs[0],scale.inputs[0]); links.new(scale.outputs[0],output.inputs[0])
        result=prune_zero_weight_branches(tree,[proof.outputs[0]])
        self.assertEqual(scale.inputs[0].default_value,(0,0,0))
        self.assertFalse(scale.inputs[0].is_linked)
        self.assertIn('texture',result['removedNodes'])
        self.assertIn('scale',nodes)

    def test_zero_mix_bypasses_branch_but_shared_texture_survives(self):
        nodes=Nodes(); links=Links(); tree=SimpleNamespace(nodes=nodes,links=links)
        first=fixture_node(nodes,'first','TEX_IMAGE',[],['RGBA'])
        second=fixture_node(nodes,'shared','TEX_IMAGE',[],['RGBA'])
        mix=fixture_node(nodes,'mix','MIX_RGB',[('VALUE',0),('RGBA',0),('RGBA',0)],['RGBA'],blend_type='MIX',use_clamp=False,use_alpha=False)
        output=fixture_node(nodes,'output','OUTPUT_MATERIAL',[('RGBA',0),('RGBA',0)],[])
        links.new(first.outputs[0],mix.inputs[1]); links.new(second.outputs[0],mix.inputs[2])
        links.new(mix.outputs[0],output.inputs[0]); links.new(second.outputs[0],output.inputs[1])
        result=prune_zero_weight_branches(tree,[])
        self.assertIs(output.inputs[0].links[0].from_node,first)
        self.assertIn('shared',nodes)
        self.assertIn('mix',result['removedNodes'])

    def test_nonzero_mix_and_clamping_are_preserved(self):
        for factor,clamp in ((1e-40,False),(0,True)):
            nodes=Nodes(); links=Links(); tree=SimpleNamespace(nodes=nodes,links=links)
            mix=fixture_node(nodes,'mix','MIX_RGB',[('VALUE',factor),('RGBA',0),('RGBA',0)],['RGBA'],blend_type='MIX',use_clamp=clamp,use_alpha=False)
            output=fixture_node(nodes,'output','OUTPUT_MATERIAL',[('RGBA',0)],[])
            links.new(mix.outputs[0],output.inputs[0])
            self.assertEqual(prune_zero_weight_branches(tree,[])['specializations'],[])
            self.assertIn('mix',nodes)

    def test_zero_proof_checks_all_texels_without_epsilon(self):
        self.assertEqual(exact_zero_channels([(0,0,1,0),(0,1e-40,0,0)]),(True,False,False,True))
        self.assertEqual(exact_zero_channels([(0,float('nan'),0,float('inf'))]),(True,False,True,False))
        self.assertEqual(exact_zero_channels([]),(False,)*4)
    def test_repeat_seam_copies_exact_texels_with_both_gutters(self):
        indices,origin,full=periodic_crop_axis(.97,1.03,100,4)
        self.assertEqual(origin,93)
        self.assertEqual(indices,list(range(93,100))+list(range(7)))
        self.assertFalse(full)

    def test_negative_repeat_preserves_continuous_texel_coordinates(self):
        indices,origin,full=periodic_crop_axis(-.03,.03,100,4)
        self.assertEqual(origin,-7)
        self.assertEqual(indices,list(range(93,100))+list(range(7)))
        for uv in (-.03,-.01,0,.01,.03):
            remapped=(uv*100-origin)/len(indices)
            # Blender's center-of-texel coordinate, before kernel indexing.
            self.assertAlmostEqual(remapped*len(indices)-.5,uv*100-origin-.5)
        self.assertFalse(full)

    def test_full_period_and_gutter_coverage_retain_original_axis(self):
        for low,high in ((-.1,1.1),(4,5),(.04,.96)):
            indices,origin,full=periodic_crop_axis(low,high,100,4)
            self.assertEqual(indices,list(range(100)))
            self.assertEqual(origin,0)
            self.assertTrue(full)

    def test_periodic_crop_matches_linear_and_cubic_kernel_texels(self):
        import math
        for low,high in ((.97,1.03),(-2.03,-1.97),(4.1,4.2)):
            indices,origin,full=periodic_crop_axis(low,high,100,4)
            self.assertFalse(full)
            for step in range(101):
                uv=low+(high-low)*step/100
                center=math.floor(uv*100-.5)
                # Cubic's four source neighbors include linear's two.
                for source_index in range(center-1,center+3):
                    self.assertEqual(indices[source_index-origin],source_index%100)

    def test_invalid_periodic_domains_fail_closed(self):
        for args in ((1,0,100),(0,float('nan'),100),(0,1,0)):
            with self.assertRaises(ValueError): periodic_crop_axis(*args)

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
