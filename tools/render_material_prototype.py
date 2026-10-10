"""Build a private UV/material-preserving Blender scene from exported Unity geometry.

Run inside Blender: blender-runtime.exe --background --python this.py -- scene.json material-report.json outdir MAP_ID
TerrainShader combines referenced macro and detail textures, masks and normals
on a lit surface so terrain slopes receive sunlight and cast shadows.
"""
import json
import math
import re
import sys
from pathlib import Path

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector
sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_material_blend_view import prepare_view
from unity_normal_map import build_dxt5nm_encoded
from terrain_detail import build_surface, build_variant_macro, texture_color_space
from render_safety import guarded_render


CONVERSION = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))


def ident(ref):
    return (ref["assetFile"], int(ref["pathId"]))


def world_matrix(ref, transforms, cache, chain=()):
    key = ident(ref)
    if key in cache:
        return cache[key]
    if key in chain or key not in transforms:
        raise ValueError(f"Missing/cyclic transform {key}")
    record = transforms[key]
    t = record["transform"]
    p, q, s = t["m_LocalPosition"], t["m_LocalRotation"], t["m_LocalScale"]
    local = Matrix.LocRotScale(Vector((p["x"], p["y"], p["z"])),
                               Quaternion((q["w"], q["x"], q["y"], q["z"])),
                               Vector((s["x"], s["y"], s["z"])))
    parent = record.get("parent")
    if parent is not None:
        local = world_matrix(parent, transforms, cache, chain + (key,)) @ local
    cache[key] = local
    return local


def _obj_index(raw, count):
    value = int(raw)
    return value - 1 if value > 0 else count + value


def read_obj(path, name, material_count):
    positions, uvs, normals = [], [], []
    keyed = {}
    vertices, loop_uvs, loop_normals = [], [], []
    faces, face_materials = [], []
    current_group = -1
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for raw in stream:
            parts = raw.split()
            if not parts:
                continue
            if parts[0] == "v" and len(parts) >= 4:
                # UnityPy's OBJ export reflects local X; undo before transforms.
                positions.append((-float(parts[1]), float(parts[2]), float(parts[3])))
            elif parts[0] == "vt" and len(parts) >= 3:
                uvs.append((float(parts[1]), float(parts[2])))
            elif parts[0] == "vn" and len(parts) >= 4:
                normals.append((-float(parts[1]), float(parts[2]), float(parts[3])))
            elif parts[0] == "g":
                current_group += 1
            elif parts[0] == "f" and len(parts) >= 4:
                corners = []
                for token in parts[1:]:
                    indices = token.split("/")
                    vi = _obj_index(indices[0], len(positions))
                    ti = _obj_index(indices[1], len(uvs)) if len(indices) > 1 and indices[1] else -1
                    ni = _obj_index(indices[2], len(normals)) if len(indices) > 2 and indices[2] else -1
                    key = (vi, ti, ni)
                    if key not in keyed:
                        keyed[key] = len(vertices)
                        vertices.append(positions[vi])
                        loop_uvs.append(uvs[ti] if ti >= 0 else (0.0, 0.0))
                        loop_normals.append(normals[ni] if ni >= 0 else (0.0, 0.0, 0.0))
                    corners.append(keyed[key])
                corners.reverse()  # OBJ faces are reversed by UnityPy's reflected X export.
                faces.append(tuple(corners))
                face_materials.append(min(max(current_group - 1, 0), max(material_count - 1, 0)))
    if not vertices or not faces:
        raise ValueError(f"No mesh faces found in {path}")
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for poly in mesh.polygons:
        poly.material_index = face_materials[poly.index]
        poly.use_smooth = True
        for loop_i in poly.loop_indices:
            vertex_i = mesh.loops[loop_i].vertex_index
            uv_layer.data[loop_i].uv = loop_uvs[vertex_i]
    if normals and hasattr(mesh, "normals_split_custom_set"):
        mesh.normals_split_custom_set([loop_normals[loop.vertex_index] for loop in mesh.loops])
    return mesh


def load_image(path, color_space):
    image = bpy.data.images.load(str(Path(path).resolve()), check_existing=True)
    try:
        image.colorspace_settings.name = color_space
    except Exception:
        pass
    return image


def make_material(record, image_cache, texture_variant=None):
    mat = bpy.data.materials.new(record["name"])
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    terrain_shader = record.get("shaderName") == "Shader Graphs/TerrainShader"
    surface = nodes.new("ShaderNodeBsdfPrincipled")
    links.new(surface.outputs["BSDF"], output.inputs["Surface"])
    target_color = surface.inputs["Base Color"]
    if terrain_shader:
        surface.inputs["Roughness"].default_value = 1.0
        surface.inputs["Metallic"].default_value = 0.0
        surface.inputs["Specular IOR Level"].default_value = 0.0
    textures = record.get("textures", [])
    preferred = ("_macro_basecolor", "_BaseMap", "_BaseColor", "_Basecolor", "_MainTex", "_ColorMap")
    base = next((t for name in preferred for t in textures if t.get("property") == name and t.get("export")), None)
    if base is None:
        base = next((t for t in textures if t.get("export") and not re.search(r"normal|bump|metal|rough|ao|splat|mask|depth", t.get("textureName", ""), re.I)), None)
    if base:
        path = base["export"]
        image = image_cache.setdefault(path, load_image(path, texture_color_space(base)))
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = image
        tex.label = f"{base['property']} · {base.get('textureName')}"
        uv = nodes.new("ShaderNodeTexCoord")
        scale, offset = base.get("scale") or {"x": 1.0, "y": 1.0}, base.get("offset") or {"x": 0.0, "y": 0.0}
        scale_vector = nodes.new("ShaderNodeCombineXYZ")
        scale_vector.inputs["X"].default_value = float(scale.get("x", 1))
        scale_vector.inputs["Y"].default_value = float(scale.get("y", 1))
        scale_vector.inputs["Z"].default_value = 1.0
        scaled = nodes.new("ShaderNodeVectorMath")
        scaled.operation = "MULTIPLY"
        links.new(uv.outputs["UV"], scaled.inputs[0])
        links.new(scale_vector.outputs["Vector"], scaled.inputs[1])
        translated = nodes.new("ShaderNodeVectorMath")
        translated.operation = "ADD"
        translated.inputs[1].default_value = (float(offset.get("x", 0)), float(offset.get("y", 0)), 0.0)
        links.new(scaled.outputs["Vector"], translated.inputs[0])
        links.new(translated.outputs["Vector"], tex.inputs["Vector"])
        base_color = tex.outputs["Color"]
        if terrain_shader:
            tex.interpolation = "Linear"
            if texture_variant:
                asset = texture_variant.texture_for(record, base, "macro")
                if asset:
                    base_color = build_variant_macro(nodes, links, asset, translated.outputs["Vector"], load_image)
                mat["terrainTextureVariant"] = str(texture_variant.path)
                mat["terrainTextureVariantSha256"] = texture_variant.sha256
            base_color, terrain_normal = build_surface(nodes, links, record, uv.outputs["UV"],
                                                       base_color, load_image, texture_variant=texture_variant)
            if terrain_normal is not None:
                links.new(terrain_normal, surface.inputs["Normal"])
        colors = {item["property"]: item["value"] for item in record.get("colors", [])}
        color = colors.get("_BaseColor", colors.get("_Color"))
        if color and any(abs(color[c] - 1.0) > 1e-6 for c in ("r", "g", "b")):
            multiply = nodes.new("ShaderNodeMixRGB")
            multiply.blend_type = "MULTIPLY"
            multiply.inputs[0].default_value = 1.0
            multiply.inputs[1].default_value = (color["r"], color["g"], color["b"], 1.0)
            links.new(base_color, multiply.inputs[2])
            links.new(multiply.outputs["Color"], target_color)
        else:
            links.new(base_color, target_color)
        floats = {item["property"]: item["value"] for item in record.get("floats", [])}
        transparent = floats.get("_Surface", 0.0) == 1.0 or floats.get("_Mode", 0.0) in (1.0, 2.0, 3.0)
        alpha_clip = floats.get("_AlphaClip", 0.0) > 0.5
        if image.channels == 4 and "Alpha" in surface.inputs and (transparent or alpha_clip):
            # In opaque URP materials, texture alpha commonly carries smoothness;
            # do not interpret that packed channel as transparency.
            links.new(tex.outputs["Alpha"], surface.inputs["Alpha"])
        elif terrain_shader and texture_variant and asset:
            nodes.remove(tex)
    normal = next((t for t in textures if t.get("export") and
                   (t.get("property") in ("_BumpMap", "_Normal", "_NormalMap") or
                    re.search(r"_n(?:ormal)?$", t.get("textureName", ""), re.I))), None)
    # Terrain normals are layered by build_surface above.
    if normal and record.get("shaderName") != "Shader Graphs/TerrainShader":
        path = normal["export"]
        image = image_cache.setdefault(path, load_image(path, "Non-Color"))
        tex = nodes.new("ShaderNodeTexImage")
        tex.image, tex.label = image, f"Normal · {normal.get('textureName')}"
        uv = nodes.new("ShaderNodeTexCoord")
        node = nodes.new("ShaderNodeNormalMap")
        links.new(uv.outputs["UV"], tex.inputs["Vector"])
        # Unity's common DXT5nm export stores tangent X in alpha and Y in green;
        # red is constant and blue is not a usable XYZ normal channel.
        if image.channels == 4:
            sep = nodes.new("ShaderNodeSeparateXYZ")
            links.new(tex.outputs["Color"], sep.inputs["Vector"])
            decoded = build_dxt5nm_encoded(nodes, links, tex.outputs["Alpha"], sep.outputs["Y"])
            links.new(decoded, node.inputs["Color"])
        else:
            links.new(tex.outputs["Color"], node.inputs["Color"])
        if "Normal" in surface.inputs:
            links.new(node.outputs["Normal"], surface.inputs["Normal"])
    colors = {item["property"]: item["value"] for item in record.get("colors", [])}
    color = colors.get("_BaseColor", colors.get("_Color"))
    if color and not base:
        target_color.default_value = (color["r"], color["g"], color["b"], color["a"])
    floats = {item["property"]: item["value"] for item in record.get("floats", [])}
    if not terrain_shader:
        if "Metallic" in surface.inputs:
            surface.inputs["Metallic"].default_value = min(1.0, max(0.0, floats.get("_Metallic", 0.0)))
        if "Roughness" in surface.inputs:
            surface.inputs["Roughness"].default_value = min(1.0, max(0.05, 1.0 - floats.get("_Smoothness", 0.2)))
    mat.diffuse_color = (0.4, 0.4, 0.4, 1)
    return mat


def build(scene_file, report_file, output, map_id, texture_variant=None):
    scene_data = json.loads(Path(scene_file).read_text(encoding="utf-8"))
    report = json.loads(Path(report_file).read_text(encoding="utf-8"))
    manifest = json.loads((Path(scene_file).parents[1] / "manifest.json").read_text(encoding="utf-8"))
    map_spec = next(m for m in manifest["maps"] if m["id"] == map_id)
    map_width, map_height = float(map_spec["widthMeters"]), float(map_spec["heightMeters"])
    mesh_records = [r for r in scene_data["objects"] if r.get("map") == map_id and r.get("type") == "MeshFilter"]
    transform_records = {(r["assetFile"], r["pathId"]): r for r in scene_data["objects"]
                         if r.get("map") == map_id and r.get("type") == "Transform"}
    map_materials = next(m for m in report["maps"] if m["mapId"] == map_id)
    material_rows = {(m["assetFile"], m["pathId"]): m for m in report["materials"]}
    by_gameobject = {(r["assetFile"], r["pathId"]): r for r in map_materials["renderedObjects"]}
    image_cache, material_cache = {}, {}
    mesh_cache = {}
    matrix_cache = {}
    obj_collection = bpy.data.collections.new(map_id)
    bpy.context.scene.collection.children.link(obj_collection)
    loaded, skipped, skipped_lod, skipped_surface = 0, 0, 0, 0
    airfield_bounds = []
    for record in mesh_records:
        go_ref = record.get("gameObject", {})
        # LOD1 renderers are separate children under the primary mesh object.
        # Keep them only when the hierarchy has no primary mesh to render.
        go_info = by_gameobject.get(ident(go_ref), {})
        if go_info.get("gameObject", "").casefold() == "lod1":
            parent_ref = record.get("transform", {})
            parent_xform = transform_records.get(ident(parent_ref))
            parent_ref = (parent_xform or {}).get("parent")
            has_primary_parent = False
            while parent_ref:
                parent_xform = transform_records.get(ident(parent_ref))
                parent_go = (parent_xform or {}).get("gameObject")
                if parent_go and ident(parent_go) in by_gameobject:
                    parent_name = by_gameobject[ident(parent_go)].get("gameObject", "")
                    if parent_name.casefold() != "lod1":
                        has_primary_parent = True
                        break
                parent_ref = (parent_xform or {}).get("parent")
            if has_primary_parent:
                skipped_lod += 1
                continue
        renderer = by_gameobject.get(ident(go_ref))
        assigned = [mat for comp in (renderer or {}).get("components", [])
                    for mat in comp.get("materials", [])]
        assigned = [mat for mat in assigned if ident(mat) in material_rows]
        if any(material_rows[ident(mat)].get("shaderName") == "Shader Graphs/surf" for mat in assigned):
            # The Surf graph animates waves/foam; its serialized _Foam texture
            # is not a static albedo. Keep flat ocean underlay for map imagery.
            skipped_surface += 1
            continue
        if not assigned:
            skipped += 1
            continue
        mats = []
        for material_ref in assigned:
            key = ident(material_ref)
            if key not in material_cache:
                material_cache[key] = make_material(material_rows[key], image_cache, texture_variant)
            mats.append(material_cache[key])
        mesh_path = Path(scene_file).parent / record["file"]
        cache_key = (record["file"], tuple(ident(m) for m in assigned))
        if cache_key not in mesh_cache:
            mesh_cache[cache_key] = read_obj(mesh_path, record["name"], len(mats))
            for mat in mats:
                mesh_cache[cache_key].materials.append(mat)
        obj = bpy.data.objects.new(record["name"], mesh_cache[cache_key])
        obj_collection.objects.link(obj)
        obj.matrix_world = CONVERSION @ world_matrix(record["transform"], transform_records, matrix_cache)
        loaded += 1
        if re.search(r"airbase_city", record["name"], re.I):
            obj_bound = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
            airfield_bounds.extend(obj_bound)
    # A sea-level plane fills open-water and unknown-height regions without adding terrain elevation.
    bpy.ops.mesh.primitive_plane_add(size=max(map_width, map_height) * 1.2, location=(0, 0, -1.0))
    sea = bpy.context.object
    sea.name = "Sea level · uniform blue underlay"
    sea_mat = bpy.data.materials.new("Water · physical blue underlay")
    sea_mat.diffuse_color = (0.018, 0.115, 0.19, 1)
    sea_mat.use_nodes = True
    sea_bsdf = sea_mat.node_tree.nodes.get("Principled BSDF")
    sea_bsdf.inputs["Base Color"].default_value = (0.018, 0.115, 0.19, 1)
    sea_bsdf.inputs["Roughness"].default_value = 0.32
    sea_bsdf.inputs["Metallic"].default_value = 0.04
    sea.data.materials.append(sea_mat)
    # Ground plane stays below scene objects in its own collection.
    for linked_collection in list(sea.users_collection):
        linked_collection.objects.unlink(sea)
    obj_collection.objects.link(sea)

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.resolution_x = 1536
    scene.render.resolution_y = 1536
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.view_transform = "AgX"
    scene.world.color = (0.018, 0.11, 0.18)
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.018, 0.11, 0.18, 1)
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.7
    sun_data = bpy.data.lights.new("Sun", "SUN")
    sun_data.energy = 2.0
    sun = bpy.data.objects.new("Sun", sun_data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = Euler((math.radians(25), math.radians(-20), math.radians(18)), "XYZ")

    def add_camera(name, center, z, scale, output_path, res):
        camera_data = bpy.data.cameras.new(name)
        camera = bpy.data.objects.new(name, camera_data)
        scene.collection.objects.link(camera)
        camera.location = (center[0], center[1], z)
        # Keep world X pointing right; Blender Y is -Unity Z, so flip the
        # finished render vertically to put north (+Unity Z) at the top.
        camera.rotation_euler = Euler((0, 0, 0), "XYZ")
        camera_data.type = "ORTHO"
        camera_data.ortho_scale = scale
        camera_data.lens = 50
        camera_data.clip_start = 1.0
        camera_data.clip_end = 100000.0
        scene.camera = camera
        if isinstance(res, tuple):
            scene.render.resolution_x, scene.render.resolution_y = res
        else:
            scene.render.resolution_x = scene.render.resolution_y = res
        scene.render.filepath = str(output_path.resolve())
        scene.use_nodes = True
        nodes, links = scene.node_tree.nodes, scene.node_tree.links
        nodes.clear()
        render_layer = nodes.new("CompositorNodeRLayers")
        flip = nodes.new("CompositorNodeFlip")
        flip.axis = "Y"
        composite = nodes.new("CompositorNodeComposite")
        links.new(render_layer.outputs["Image"], flip.inputs[0])
        links.new(flip.outputs[0], composite.inputs["Image"])
        guarded_render(scene)
        return camera

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    overview_res = (1536, max(1, round(1536 * map_height / map_width)))
    add_camera(f"Overview · {map_spec['name']}", (0, 0), 12000, map_width,
               output / f"{map_id}-overview.png", overview_res)
    if airfield_bounds:
        cx = sum(v.x for v in airfield_bounds) / len(airfield_bounds)
        cy = sum(v.y for v in airfield_bounds) / len(airfield_bounds)
        cz = max(v.z for v in airfield_bounds) + 8000
        add_camera(f"Airfield detail · {map_spec['name']}", (cx, cy), cz, 3840,
                   output / f"{map_id}-airfield-detail.png", 1536)
    for image in bpy.data.images:
        if image.source == "FILE" and image.packed_file is None:
            try:
                image.pack()
            except Exception as exc:
                print(f"PACK WARNING {image.name}: {exc}")
    blend = output / f"{map_id}-material-prototype.blend"
    prepare_view(map_width, map_height)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(blend.resolve()))
    print(f"IMPORTED {loaded} objects; skipped {skipped}; skipped LOD1 {skipped_lod}; skipped animated water surfaces {skipped_surface}; meshes {len(mesh_cache)}; materials {len(material_cache)}; packed images {len(image_cache)}")
    print(f"BLEND {blend.resolve()}")


if __name__ == "__main__":
    import argparse
    from terrain_texture_variant import TerrainTextureVariant
    args = sys.argv[sys.argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("scene")
    parser.add_argument("report")
    parser.add_argument("output")
    parser.add_argument("map_id")
    parser.add_argument("--terrain-textures")
    options = parser.parse_args(args)
    variant = TerrainTextureVariant(options.terrain_textures) if options.terrain_textures else None
    build(options.scene, options.report, options.output, options.map_id, variant)
