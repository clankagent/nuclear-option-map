"""Read-only Blender audit: -- report.json selection.json receipt.json."""
import hashlib
import json
from pathlib import Path
import sys
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from terrain_texture_variant import TerrainTextureVariant, ALLOWED
from terrain_detail import DETAIL_PROPERTIES, texture_color_space


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def geometry_fingerprint(scene):
    """Hash geometry, original UV layers and placements independently of shading."""
    import array
    digest = hashlib.sha256()
    meshes = {}
    for obj in sorted((o for o in scene.objects if o.type == 'MESH'), key=lambda o: o.name):
        mesh = obj.data
        if mesh.name not in meshes:
            part = hashlib.sha256()
            for collection, prop, width, kind in ((mesh.vertices, 'co', 3, 'f'),
                                                  (mesh.loops, 'vertex_index', 1, 'i'),
                                                  (mesh.polygons, 'loop_start', 1, 'i'),
                                                  (mesh.polygons, 'loop_total', 1, 'i')):
                values = array.array(kind, [0]) * (len(collection) * width)
                collection.foreach_get(prop, values)
                part.update(values.tobytes())
            for layer in mesh.uv_layers:
                part.update(layer.name.encode())
                values = array.array('f', [0]) * (len(layer.data) * 2)
                layer.data.foreach_get('uv', values)
                part.update(values.tobytes())
            meshes[mesh.name] = part.hexdigest()
        digest.update(json.dumps([obj.name, meshes[mesh.name], [list(row) for row in obj.matrix_world]],
                                 sort_keys=True).encode())
    return digest.hexdigest()


def main(report_path, manifest_path, output_path):
    variant = TerrainTextureVariant(manifest_path)
    report = json.loads(Path(report_path).read_text(encoding='utf-8-sig'))
    errors, materials = [], []

    def check(condition, message):
        if not condition:
            errors.append(message)

    def image_path(image):
        return str(Path(bpy.path.abspath(image.filepath)).resolve())

    def vector_source(node):
        links = list(node.inputs['Vector'].links)
        return links[0].from_socket if len(links) == 1 else None

    for record in report['materials']:
        if record.get('shaderName') != 'Shader Graphs/TerrainShader':
            continue
        mat = bpy.data.materials.get(record['name'])
        if mat is None or not mat.users:
            continue
        check(mat.get('terrainTextureVariantSha256') == variant.sha256, mat.name + ': manifest SHA mismatch')
        nodes = [n for n in mat.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image]
        selected = []
        for texture in record['textures']:
            prop = texture['property']
            if prop not in ALLOWED:
                continue
            asset = variant.texture_for(record, texture, ALLOWED[prop])
            check(asset is not None, mat.name + ': missing selected binding ' + prop)
            if asset is None:
                continue
            paths = [t['path'] for t in asset.get('macroTiles', [])] or [asset['rgbExport']]
            rgb_nodes = [n for n in nodes if image_path(n.image) in paths]
            check(set(image_path(n.image) for n in rgb_nodes) == set(paths), mat.name + ': RGB image missing ' + prop)
            for node in rgb_nodes:
                check(node.image.colorspace_settings.name == texture_color_space(texture), mat.name + ': RGB color space ' + prop)
                check(node.outputs['Color'].is_linked, mat.name + ': disconnected RGB ' + prop)
                check(vector_source(node) is not None, mat.name + ': RGB UV missing ' + prop)
                check(node.interpolation == 'Linear', mat.name + ': RGB interpolation ' + prop)
                if asset['role'] == 'detail':
                    check(node.extension == 'REPEAT', mat.name + ': detail wrap ' + prop)
            key = next((k for k in ('fields', 'lush') if prop == '_SampleTexture2D_' + DETAIL_PROPERTIES[k] + '_Texture_1_Texture2D'), None)
            if key:
                alpha_nodes = [n for n in nodes if image_path(n.image) == asset['source'] and n.outputs['Alpha'].is_linked]
                check(len(alpha_nodes) == 1 and len(rgb_nodes) == 1, mat.name + ': ambiguous alpha sampler ' + key)
                if len(alpha_nodes) == len(rgb_nodes) == 1:
                    alpha, rgb = alpha_nodes[0], rgb_nodes[0]
                    check(not alpha.outputs['Color'].is_linked, mat.name + ': original RGB used for ' + key)
                    check(not rgb.outputs['Alpha'].is_linked, mat.name + ': derivative alpha used for ' + key)
                    check(vector_source(alpha) == vector_source(rgb), mat.name + ': alpha/RGB UV differs ' + key)
                    check(alpha.extension == rgb.extension and alpha.interpolation == rgb.interpolation, mat.name + ': alpha sampling differs ' + key)
                    check(alpha.image.colorspace_settings.name == texture_color_space(texture), mat.name + ': alpha color space differs ' + key)
                    links = list(alpha.outputs['Alpha'].links)
                    check(all(l.to_node.type == 'MATH' and l.to_node.operation == 'MULTIPLY' for l in links), mat.name + ': alpha blend-weight connection differs ' + key)
            selected.append({'property': prop, 'images': paths, 'originalAlpha': asset['source'] if key else None})
        check(len(selected) == 8, mat.name + ': expected eight RGB bindings')
        # Check every normal, mask and field-angle source actually sampled by this graph.
        protected = ['_macro_normal'] + ['_splat_' + k for k in ('grass', 'rock', 'fields', 'lush')]
        protected += ['_SampleTexture2D_' + DETAIL_PROPERTIES[k] + '_Texture_1_Texture2D' for k in DETAIL_PROPERTIES if k.endswith('_n') or k == 'field_angle']
        originals = []
        for texture in record['textures']:
            if texture['property'] not in protected or not texture.get('export'):
                continue
            path = str(Path(texture['export']).resolve())
            matching = [n for n in nodes if image_path(n.image) == path]
            check(bool(matching), mat.name + ': protected source missing ' + texture['property'])
            for node in matching:
                check(node.image.colorspace_settings.name == texture_color_space(texture), mat.name + ': protected color space differs ' + texture['property'])
                check(node.outputs['Color'].is_linked, mat.name + ': protected source disconnected ' + texture['property'])
            originals.append({'property': texture['property'], 'image': path})
        materials.append({'material': mat.name, 'selected': selected, 'protected': originals})
    check(bool(materials), 'No used terrain material audited')
    scene = bpy.context.scene
    fingerprint = geometry_fingerprint(scene)
    check(scene.get('terrainSourceGeometrySha256') == fingerprint, 'Geometry or original UVs changed')
    check(scene.get('terrainTextureVariantSha256') == variant.sha256, 'Scene manifest SHA mismatch')
    check(len(bpy.data.libraries) == 0, 'Linked libraries present')
    images = []
    for image in bpy.data.images:
        if image.source != 'FILE' or not image.users:
            continue
        check(bool(image.packed_file or image.packed_files), 'Unpacked image: ' + image.name)
        check(max(image.size) <= 8192, 'Oversized image: ' + image.name)
        images.append({'name': image.name, 'path': image_path(image), 'pixels': list(image.size), 'packed': bool(image.packed_file or image.packed_files), 'colorSpace': image.colorspace_settings.name})
    meshes = [obj for obj in scene.objects if obj.type == 'MESH']
    result = {'passed': not errors, 'errors': errors, 'scene': bpy.data.filepath,
              'sceneSha256': sha(bpy.data.filepath), 'manifest': str(variant.path), 'manifestSha256': variant.sha256,
              'materialReportSha256': sha(report_path), 'materials': materials, 'images': images,
              'geometry': {'meshObjects': len(meshes), 'vertices': sum(len(o.data.vertices) for o in meshes),
                           'polygons': sum(len(o.data.polygons) for o in meshes), 'loops': sum(len(o.data.loops) for o in meshes),
                           'sha256': fingerprint},
              'rendered': False, 'sceneModifiedOrSaved': False}
    Path(output_path).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'passed': result['passed'], 'errors': errors, 'receipt': output_path}))
    if errors:
        raise RuntimeError('Terrain texture scene audit failed')


if __name__ == '__main__':
    main(*sys.argv[sys.argv.index('--') + 1:])
