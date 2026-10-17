import copy
import importlib.util
import math
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location('mesh_light_material', Path(__file__).parents[2]/'tools/mesh_light_material.py')
light = importlib.util.module_from_spec(spec); spec.loader.exec_module(light)


def record():
    return {'shaderName': light.SHADER_NAME,
            'floats': [{'property': k, 'value': v} for k,v in
                       (('_Brightness',5),('_Frequency',0),('_OnOffFrequency',0),('_Delay',0))],
            'textures': [{'property':'_LightShape','export':'shape.png'}]}


class MeshLightMaterialTests(unittest.TestCase):
    def test_unrelated_shader_does_not_intercept(self):
        self.assertIsNone(light.static_blinking_profile({'shaderName':'Shader Graphs/MeshLight'}))
        self.assertIsNone(light.build_static_blinking_light(None,None,{'shaderName':'unknown'},None))

    def test_static_profile_temporal_gate_one(self):
        value=record(); value['floats'][-1]['value']=-123.5
        profile=light.static_blinking_profile(value)
        self.assertEqual(profile,{'brightness':5,'delay':-123.5,'temporalGate':1,'texture':'shape.png'})
        value['floats'][0]['value']=0
        self.assertEqual(light.static_blinking_profile(value)['brightness'],0)

    def test_nonstatic_nonfinite_and_missing_properties_rejected(self):
        for index in range(4):
            for bad in (math.inf,-math.inf,math.nan,'0',True):
                value=record(); value['floats'][index]['value']=bad
                with self.subTest(index=index,bad=bad), self.assertRaises(ValueError):
                    light.static_blinking_profile(value)
        for index,bad in ((0,-1),(1,1e-30),(2,-1e-30)):
            value=record(); value['floats'][index]['value']=bad
            with self.assertRaises(ValueError): light.static_blinking_profile(value)
        value=record(); value['floats'].pop()
        with self.assertRaises(ValueError): light.static_blinking_profile(value)
        value=record(); value['floats'].append(copy.deepcopy(value['floats'][0]))
        with self.assertRaises(ValueError): light.static_blinking_profile(value)

    def test_missing_shape_export_rejected(self):
        for textures in ([],[{'property':'_LightShape'}],[{'property':'_LightShape','export':''}],
                         [{'property':'_LightShape','export':' '}],record()['textures']*2):
            value=record(); value['textures']=textures
            with self.assertRaises(ValueError): light.static_blinking_profile(value)

    def test_float32_constants_distance_bounds_and_facing(self):
        offset=struct.unpack('<f',struct.pack('<f',0.1))[0]
        slope=struct.unpack('<f',bytes.fromhex('75f8893a'))[0]
        self.assertEqual(light.FACING_OFFSET,offset)
        self.assertEqual(light.DISTANCE_SLOPE,slope)
        self.assertEqual(light.light_factor(offset,300),0)
        self.assertLess(light.light_factor(0.1,300),0) # Source float32 offset matters.
        self.assertEqual(light.light_factor(1,0),10*(1-offset))
        self.assertAlmostEqual(light.light_factor(1,300+3/slope),40*(1-offset))
        self.assertEqual(light.light_factor(1,1e8),70*(1-offset))
        self.assertLess(light.light_factor(-1,300),0)

    def test_alpha_backface_and_emission(self):
        self.assertEqual(light.light_sample(0,1,300,5),(0,0))
        alpha,strength=light.light_sample(1,1,300,5)
        self.assertEqual(alpha,1); self.assertAlmostEqual(strength,50*(1-light.FACING_OFFSET))
        self.assertEqual(light.light_sample(1,1,300,5,True),(0,strength))
        self.assertEqual(light.light_sample(1,-1,300,5),(0,0))
        self.assertEqual(light.light_sample(1,1,300,0),(1,0))

    def test_graph_preserves_source_color_in_crop_safe_uv_layers(self):
        # Minimal Blender-free graph fixture checks the transport contract.
        class Socket:
            def __init__(self): self.default_value=None
        class Sockets(dict):
            def __missing__(self,key):
                self[key]=Socket(); return self[key]
        class Node:
            def __init__(self,kind): self.kind=kind; self.inputs=Sockets(); self.outputs=Sockets()
        class Nodes(list):
            def new(self,kind):
                value=Node(kind); self.append(value); return value
        class Links(list):
            def new(self,a,b): self.append((a,b))
        nodes=Nodes(); links=Links(); calls=[]
        def load_image(path,space): calls.append((path,space)); return object()
        surface=light.build_static_blinking_light(nodes,links,record(),load_image)
        self.assertIsNotNone(surface)
        self.assertEqual(calls,[('shape.png','sRGB')])
        self.assertEqual([n.uv_map for n in nodes if n.kind=='ShaderNodeUVMap'],
                         ['UVMap',light.RG_LAYER,light.BA_LAYER])
        self.assertFalse(any(n.kind=='ShaderNodeVertexColor' for n in nodes))
        texture=next(n for n in nodes if n.kind=='ShaderNodeTexImage')
        self.assertEqual((texture.extension,texture.interpolation),('EXTEND','Linear'))
        self.assertFalse(any(n.kind=='ShaderNodeOutputMaterial' for n in nodes))


if __name__ == '__main__': unittest.main()
