"""Static MeshLightBlinking reconstruction for downward orthographic imagery.

The caller must supply source corner color channels in the reserved UV layers
__UnityVertexColor_RG and __UnityVertexColor_BA. Store UNorm8 channels divided
by 255 directly as linear values, without an sRGB conversion. UV layers survive
the physical cropper; color attributes do not. Preserve original UVMap. A missing
stream is an importer error, not a white fallback. No Blender or I/O at import.

The original shader's static zero-frequency gate is one. Temporal profiles are
rejected rather than sampled at an arbitrary time. The black Principled lobe
approximates URP Lit specular; it is not an exact URP lighting implementation.
"""
import math
import struct


SHADER_NAME = 'Shader Graphs/MeshLightBlinking'
RG_LAYER = '__UnityVertexColor_RG'
BA_LAYER = '__UnityVertexColor_BA'
FACING_OFFSET = struct.unpack('<f', struct.pack('<f', 0.1))[0]
DISTANCE_SLOPE = struct.unpack('<f', bytes.fromhex('75f8893a'))[0]


def _finite(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f'{name} must be finite')
    return float(value)


def static_blinking_profile(record):
    """Return validated static metadata, or None for an unrelated shader.

    Texture export means a nonempty exported-image reference in the metadata.
    The supplied image loader resolves and checks that reference when building.
    """
    if record.get('shaderName') != SHADER_NAME:
        return None
    properties = {}
    required = ('_Brightness', '_Frequency', '_OnOffFrequency', '_Delay')
    for item in record.get('floats', []):
        name = item.get('property')
        if name in required:
            if name in properties:
                raise ValueError(f'Duplicate light property {name}')
            properties[name] = _finite(item.get('value'), name)
    if any(name not in properties for name in required):
        raise ValueError('Static blinking light requires brightness, both frequencies, and delay')
    if properties['_Brightness'] < 0:
        raise ValueError('_Brightness must be nonnegative')
    if properties['_Frequency'] != 0 or properties['_OnOffFrequency'] != 0:
        raise ValueError('Temporal MeshLightBlinking is unsupported; both frequencies must equal zero')
    textures = [t for t in record.get('textures', []) if t.get('property') == '_LightShape']
    if len(textures) != 1 or not isinstance(textures[0].get('export'), str) or not textures[0]['export'].strip():
        raise ValueError('Exactly one exported _LightShape texture is required')
    return {'brightness': properties['_Brightness'], 'delay': properties['_Delay'],
            'temporalGate': 1.0, 'texture': textures[0]['export']}


def light_factor(normal_dot, view_distance):
    """Scalar reference for the recovered float32 constants and facing factor."""
    normal_dot = _finite(normal_dot, 'normal_dot')
    view_distance = _finite(view_distance, 'view_distance')
    distance_factor = min(7.0, max(1.0, 1.0 + (view_distance - 300.0) * DISTANCE_SLOPE))
    return 10.0 * (normal_dot - FACING_OFFSET) * distance_factor


def light_sample(red, normal_dot, view_distance, brightness, backface=False):
    """Scalar alpha and emission-strength reference; RGB comes from source color.

    The caller multiplies emission by sourceRGB and alpha for camera compositing.
    Non-camera rays are completely transparent in the Blender graph.
    """
    red = _finite(red, 'red'); brightness = _finite(brightness, 'brightness')
    if not 0 <= red <= 1 or brightness < 0:
        raise ValueError('Red must be 0..1 and brightness must be nonnegative')
    factor = light_factor(normal_dot, view_distance)
    alpha = min(1.0, max(0.0, red * factor)) * (0.0 if backface else 1.0)
    return alpha, brightness * red * max(factor, 0.0)


def build_static_blinking_light(nodes, links, record, load_image):
    """Return the final surface socket, or None for an unrelated shader.

    nodes and links are Blender node-tree collections. load_image(path, 'sRGB')
    loads the exported shape texture. The caller validates that every consuming
    mesh has the required source corner RG/BA UV layers; nodes cannot inspect
    mesh attributes. This builder never creates an output node or renders.
    """
    profile = static_blinking_profile(record)
    if profile is None:
        return None
    image = load_image(profile['texture'], 'sRGB')

    def node(kind):
        return nodes.new(kind)

    def scalar(operation, *values):
        result = node('ShaderNodeMath'); result.operation = operation
        for index, value in enumerate(values):
            if isinstance(value, (int, float)):
                result.inputs[index].default_value = value
            else:
                links.new(value, result.inputs[index])
        return result.outputs[0]

    geometry = node('ShaderNodeNewGeometry')
    camera_data = node('ShaderNodeCameraData')
    facing = node('ShaderNodeVectorMath'); facing.operation = 'DOT_PRODUCT'
    links.new(geometry.outputs['Normal'], facing.inputs[0])
    facing.inputs[1].default_value = (0, 0, 1)
    distance = scalar('ADD', 1, scalar('MULTIPLY',
        scalar('SUBTRACT', camera_data.outputs['View Distance'], 300), DISTANCE_SLOPE))
    distance = scalar('MINIMUM', scalar('MAXIMUM', distance, 1), 7)
    factor = scalar('MULTIPLY', scalar('MULTIPLY',
        scalar('SUBTRACT', facing.outputs['Value'], FACING_OFFSET), 10), distance)

    uv = node('ShaderNodeUVMap'); uv.uv_map = 'UVMap'
    texture = node('ShaderNodeTexImage'); texture.image = image
    texture.extension = 'EXTEND'; texture.interpolation = 'Linear'
    links.new(uv.outputs['UV'], texture.inputs['Vector'])
    separate = node('ShaderNodeSeparateColor'); separate.mode = 'RGB'
    links.new(texture.outputs['Color'], separate.inputs[0])
    red = separate.outputs['Red']
    alpha = scalar('MULTIPLY', scalar('MINIMUM', scalar('MAXIMUM',
        scalar('MULTIPLY', red, factor), 0), 1),
        scalar('SUBTRACT', 1, geometry.outputs['Backfacing']))

    rg = node('ShaderNodeUVMap'); rg.uv_map = RG_LAYER
    ba = node('ShaderNodeUVMap'); ba.uv_map = BA_LAYER
    rg_channels = node('ShaderNodeSeparateXYZ'); ba_channels = node('ShaderNodeSeparateXYZ')
    links.new(rg.outputs['UV'], rg_channels.inputs[0])
    links.new(ba.outputs['UV'], ba_channels.inputs[0])
    color = node('ShaderNodeCombineColor'); color.mode = 'RGB'
    links.new(rg_channels.outputs['X'], color.inputs['Red'])
    links.new(rg_channels.outputs['Y'], color.inputs['Green'])
    links.new(ba_channels.outputs['X'], color.inputs['Blue'])
    emission = node('ShaderNodeEmission')
    links.new(color.outputs['Color'], emission.inputs['Color'])
    strength = scalar('MULTIPLY', profile['brightness'],
        scalar('MULTIPLY', red, scalar('MAXIMUM', factor, 0)))
    links.new(strength, emission.inputs['Strength'])
    specular = node('ShaderNodeBsdfPrincipled')
    specular.inputs['Base Color'].default_value = (0, 0, 0, 1)
    specular.inputs['Roughness'].default_value = 0.5
    specular.inputs['Metallic'].default_value = 0
    specular.inputs['IOR'].default_value = 1.5
    lit = node('ShaderNodeAddShader')
    links.new(emission.outputs[0], lit.inputs[0]); links.new(specular.outputs[0], lit.inputs[1])
    zero = node('ShaderNodeEmission'); zero.inputs['Strength'].default_value = 0
    weighted = node('ShaderNodeMixShader')
    links.new(alpha, weighted.inputs[0]); links.new(zero.outputs[0], weighted.inputs[1])
    links.new(lit.outputs[0], weighted.inputs[2])
    transparent = node('ShaderNodeBsdfTransparent')
    transparent.inputs[0].default_value = (1, 1, 1, 1)
    additive = node('ShaderNodeAddShader')
    links.new(transparent.outputs[0], additive.inputs[0]); links.new(weighted.outputs[0], additive.inputs[1])
    rays = node('ShaderNodeLightPath')
    camera_surface = node('ShaderNodeMixShader')
    links.new(rays.outputs['Is Camera Ray'], camera_surface.inputs[0])
    links.new(transparent.outputs[0], camera_surface.inputs[1])
    links.new(additive.outputs[0], camera_surface.inputs[2])
    return camera_surface.outputs[0]
