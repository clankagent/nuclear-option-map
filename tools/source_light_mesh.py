"""Preserve source light attributes while selecting fixed-camera front faces.

Blender is imported only inside mesh operations. This module never renders,
loads scenes, or changes material graphs. Callers must identify source light
materials explicitly. All other faces and retained attribute streams are checked.
"""

import hashlib
import json
import struct
from collections import Counter
from source_light_faces import Corner, Triangle, plan_camera_facing_faces

FACING_OFFSET = struct.unpack("<f", struct.pack("<f", 0.1))[0]
NORMAL_TOLERANCE = 0.001
TRANSPORT = {
    153: ("__MeshLight153_source_RG", "__MeshLight153_source_BA"),
    154: ("__UnityVertexColor_RG", "__UnityVertexColor_BA"),
}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _value(value):
    if isinstance(value, bytes):
        return {"bytesHex": value.hex()}
    if isinstance(value, (float, int, str, bool)):
        return value
    return [_value(x) for x in value]


ATTRIBUTE_FIELDS = {
    "FLOAT": "value",
    "INT": "value",
    "INT8": "value",
    "BOOLEAN": "value",
    "STRING": "value",
    "FLOAT_VECTOR": "vector",
    "FLOAT2": "vector",
    "FLOAT_COLOR": "color",
    "BYTE_COLOR": "color_srgb",
    "QUATERNION": "value",
    "FLOAT4X4": "value",
    "INT32_2D": "value",
}


def _attributes(mesh):
    fields = ATTRIBUTE_FIELDS
    result = {}
    for a in mesh.attributes:
        if a.name in {
            "position",
            ".edge_verts",
            ".corner_vert",
            ".corner_edge",
            "custom_normal",
            ".custom_normal",
        }:
            continue
        if a.data_type not in fields:
            raise ValueError("Unprovedattributeformat:" + a.name + ":" + a.data_type)
        field = fields[a.data_type]
        result[a.name] = {
            "domain": a.domain,
            "type": a.data_type,
            "values": [_value(getattr(v, field)) for v in a.data],
        }
    return result


def snapshot(mesh):
    return {
        "vertexGroups": [
            [[g.group, g.weight] for g in v.groups] for v in mesh.vertices
        ],
        "positions": [list(v.co) for v in mesh.vertices],
        "edges": [list(e.vertices) for e in mesh.edges],
        "polygons": [list(p.vertices) for p in mesh.polygons],
        "materials": [m.name if m else None for m in mesh.materials],
        "materialIndices": [p.material_index for p in mesh.polygons],
        "smooth": [p.use_smooth for p in mesh.polygons],
        "attributes": _attributes(mesh),
        "uvLayerNames": [u.name for u in mesh.uv_layers],
        "activeUV": mesh.uv_layers.active_index,
        "renderUV": [u.name for u in mesh.uv_layers if u.active_render],
        "colorActive": getattr(mesh.color_attributes, "active_color_index", None),
        "colorRender": getattr(mesh.color_attributes, "render_color_index", None),
    }


def _restore_attributes(before, mesh, retained, loop_indices):
    # BMesh can omit an all-false sharp_face layer. Restore the exact retained
    # attribute stream rather than weaken the equality proof.
    for name, a in before["attributes"].items():
        ids = (
            retained
            if a["domain"] == "FACE"
            else loop_indices
            if a["domain"] == "CORNER"
            else range(len(a["values"]))
        )
        values = [a["values"][i] for i in ids]
        existing = mesh.attributes.get(name)
        if existing is None:
            existing = mesh.attributes.new(
                name=name, type=a["type"], domain=a["domain"]
            )
        if (
            existing.data_type != a["type"]
            or existing.domain != a["domain"]
            or len(existing.data) != len(values)
        ):
            raise ValueError("Attributeallocationdiff:" + name)
        field = ATTRIBUTE_FIELDS[a["type"]]
        for item, value in zip(existing.data, values):
            if _value(getattr(item, field)) != value:
                setattr(
                    item,
                    field,
                    bytes.fromhex(value["bytesHex"])
                    if isinstance(value, dict) and "bytesHex" in value
                    else value,
                )


def _filtered(before, mesh, retained, loop_indices):
    after = snapshot(mesh)
    expected = dict(before)
    for k in ["polygons", "materialIndices", "smooth"]:
        expected[k] = [before[k][i] for i in retained]
    # FACES_ONLY preserves vertices and edges. Refuse rather than silently remap.
    if after["positions"] != before["positions"] or after["edges"] != before["edges"]:
        raise ValueError("Pointoredgeorderchanged")
    expected["attributes"] = {}
    for name, a in before["attributes"].items():
        ids = (
            retained
            if a["domain"] == "FACE"
            else loop_indices
            if a["domain"] == "CORNER"
            else range(len(a["values"]))
        )
        expected["attributes"][name] = {**a, "values": [a["values"][i] for i in ids]}
    if expected != after:
        raise ValueError(
            "Retainedsemanticmeshdiff:"
            + str([k for k in expected if expected[k] != after.get(k)])
        )
    return after


def canonicalize_object(obj, material_ids):
    import bpy, bmesh

    bpy.context.view_layer.update()
    old = obj.data
    if old.shape_keys or obj.modifiers:
        raise ValueError("Unprovedlightgeometrymodifierorshapekey")
    source_matrix = [list(r) for r in obj.matrix_world]
    before = snapshot(old)
    normals = [list(n.vector) for n in old.corner_normals]
    uvs = {u.name: [tuple(d.uv) for d in u.data] for u in old.uv_layers}
    triangles = []
    ids = []
    for p in old.polygons:
        material = (
            old.materials[p.material_index]
            if p.material_index < len(old.materials)
            else None
        )
        kind = material_ids.get(material.name) if material else None
        ids.append(kind)
        corners = []
        for li in p.loop_indices:
            rgba = (0.0, 0.0, 0.0, 0.0)
            if kind in TRANSPORT:
                rg, ba = TRANSPORT[kind]
                if rg not in uvs or ba not in uvs:
                    raise ValueError("Missingexactsourcecolortransport")
                rgba = uvs[rg][li] + uvs[ba][li]
            corners.append(
                Corner(
                    tuple(old.vertices[old.loops[li].vertex_index].co),
                    tuple(uvs[n][li] for n in uvs),
                    rgba,
                    tuple(normals[li]),
                )
            )
        triangles.append(
            Triangle(
                tuple(corners),
                kind if kind is not None else "non-target:" + str(p.material_index),
            )
        )
    plan = plan_camera_facing_faces(
        triangles,
        target_materials={153, 154},
        world_matrix=source_matrix,
        surface_to_camera=(0.0, 0.0, 1.0),
        min_facing_cosine=FACING_OFFSET,
    )
    removed = {b for _, b in plan.removed_pairs}
    assert not any(ids[i] not in (153, 154) for i in removed)
    retained = list(plan.retained)
    loopids = [li for i in retained for li in old.polygons[i].loop_indices]
    expected_normals = [normals[i] for i in loopids]
    before_non_target = Counter(
        digest(
            (
                before["polygons"][i],
                before["materialIndices"][i],
                [
                    list(triangles[i].corners[j].position)
                    + [triangles[i].corners[j].uv_layers]
                    for j in range(len(triangles[i].corners))
                ],
            )
        )
        for i in range(len(triangles))
        if ids[i] is None
    )
    if removed:
        new = old.copy()
        bm = bmesh.new()
        try:
            bm.from_mesh(new)
            bm.faces.ensure_lookup_table()
            bmesh.ops.delete(
                bm, geom=[bm.faces[i] for i in sorted(removed)], context="FACES_ONLY"
            )
            bm.to_mesh(new)
        finally:
            bm.free()
        new.normals_split_custom_set(expected_normals)
        _restore_attributes(before, new, retained, loopids)
        new.update()
        obj.data = new
        bpy.context.view_layer.update()
        after = _filtered(before, new, retained, loopids)
    else:
        after = before
    error = max(
        (
            abs(x - y)
            for actual, expected in zip(obj.data.corner_normals, expected_normals)
            for x, y in zip(actual.vector, expected)
        ),
        default=0,
    )
    if error > NORMAL_TOLERANCE:
        raise ValueError("Retainednormalerror:" + str(error))
    if source_matrix != [list(r) for r in obj.matrix_world]:
        raise ValueError("Objecttransformchanged")
    if len(obj.data.loops) != len(expected_normals):
        raise ValueError("Retainedloopcountchanged")
    non_target_after = Counter(
        digest(
            (
                list(p.vertices),
                p.material_index,
                [
                    list(obj.data.vertices[obj.data.loops[li].vertex_index].co)
                    + [tuple(tuple(u.data[li].uv) for u in obj.data.uv_layers)]
                    for li in p.loop_indices
                ],
            )
        )
        for p, oldid in zip(obj.data.polygons, retained)
        if ids[oldid] is None
    )
    if non_target_after != before_non_target:
        raise ValueError("Nontargetfacemultisetchanged")
    return {
        "object": obj.name,
        "sourceMatrix": source_matrix,
        "worldDeterminant": plan.world_determinant,
        "sourceFaceCount": len(triangles),
        "retainedFaceCount": len(retained),
        "removedPairs": [
            {
                "keptSourceFace": a,
                "removedSourceFace": b,
                "material": ids[a],
                "exactPositionUVRGBA": True,
            }
            for a, b in plan.removed_pairs
        ],
        "retainedSourceFaceIndices": retained,
        "retainedCornerNormals": expected_normals,
        "retainedExactMeshSha256": digest(after),
        "normalMaxAbsError": error,
        "nonTargetFaceMultisetSha256": digest(sorted(before_non_target.items())),
        "sourceVisibleSurfaceAndColorSemanticsPreserved": True,
        "geometryIdentityPreserved": not removed,
        "intentionalBacksideRemoval": bool(removed),
        "allOtherAttributesExact": True,
    }


def apply(scene, material_ids, object_names=None):
    import bpy

    bpy.context.view_layer.update()
    if not set(material_ids.values()) <= {153, 154}:
        raise ValueError("Onlyexplicit153and154materialIDsrequired")
    selected = [
        o
        for o in scene.objects
        if o.type == "MESH"
        and (object_names is None or o.name in object_names)
        and any(m and m.name in material_ids for m in o.data.materials)
    ]
    untouched = {
        o.name: (o.data.as_pointer(), [list(r) for r in o.matrix_world])
        for o in scene.objects
        if o.type == "MESH" and o not in selected
    }
    records = [canonicalize_object(o, material_ids) for o in selected]
    if any(
        (
            scene.objects[n].data.as_pointer(),
            [list(r) for r in scene.objects[n].matrix_world],
        )
        != v
        for n, v in untouched.items()
    ):
        raise ValueError("Unrelatedobjectmodified")
    return {
        "kind": "source-proved-coincident-opposite-light-faces-v1",
        "objects": records,
        "untouchedMeshObjects": len(untouched),
        "untouchedMeshReferencesAndTransformsExact": True,
        "sourceVisibleSurfaceAndColorSemanticsPreserved": True,
        "geometryIdentityClaim": False,
        "backfaceShaderGateChanged": False,
        "cameraDirectionBlender": [0, 0, 1],
        "minimumFacingCosine": FACING_OFFSET,
        "rendered": False,
        "gpuInitialized": False,
    }


def verify_saved(scene, proof):
    import bpy

    bpy.context.view_layer.update()
    rows = []
    for r in proof["objects"]:
        o = scene.objects[r["object"]]
        actual = snapshot(o.data)
        error = max(
            (
                abs(x - y)
                for n, e in zip(o.data.corner_normals, r["retainedCornerNormals"])
                for x, y in zip(n.vector, e)
            ),
            default=0,
        )
        if (
            digest(actual) != r["retainedExactMeshSha256"]
            or [list(v) for v in o.matrix_world] != r["sourceMatrix"]
            or len(o.data.loops) != len(r["retainedCornerNormals"])
            or error > NORMAL_TOLERANCE
        ):
            raise ValueError("Serializedcanonicalfaceproofdiffers:" + o.name)
        rows.append(
            {
                "object": o.name,
                "retainedExactMeshSha256": digest(actual),
                "normalMaxAbsError": error,
                "intentionalRemovedPairs": len(r["removedPairs"]),
                "savedReopenValid": True,
            }
        )
    return {
        "kind": "saved-reopened-source-light-face-proof-v1",
        "objects": rows,
        "allSavedMeshesValid": True,
        "rendered": False,
        "gpuInitialized": False,
    }


def material_ids(materials):
    """Recognize explicit reconstruction metadata, not arbitrary material names."""
    result = {}
    for material in materials:
        if material is None:
            continue
        if (
            material.get("sourceMaterial") == "resources.assets:153"
            and material.get("sourceShader") == "resources.assets:4022"
        ):
            result[material.name] = 153
        elif material.get("staticMeshLight") == (
            "zero-frequency additive camera effect; secondary rays transparent"
        ):
            result[material.name] = 154
    return result
