"""Bind source vertex colors to an OBJ without changing its vertex order."""
from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path

RG_LAYER = "__UnityVertexColor_RG"
BA_LAYER = "__UnityVertexColor_BA"


def _float32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def validate_rgba8(colors, count):
    if not isinstance(colors, list) or len(colors) != count:
        raise ValueError("Source color count differs from OBJ vertex count")
    if any(not isinstance(row, (list, tuple)) or len(row) != 4 or
           any(type(v) is not int or not 0 <= v <= 255 for v in row) for row in colors):
        raise ValueError("Source colors must be four UNorm8 channels per vertex")
    return colors


def validate_linear_colors(colors, count):
    if not isinstance(colors, list) or len(colors) != count or any(
        not isinstance(row, (list, tuple)) or len(row) != 4 or any(
            type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
            for v in row) for row in colors
    ):
        raise ValueError("Linear source colors must be four finite 0..1 channels per vertex")
    return [[float(v) for v in row] for row in colors]


def color_binding(obj_path, source_vertices, colors):
    """Prove the source-index association before writing an optional sidecar."""
    obj_path = Path(obj_path)
    positions = []
    with obj_path.open(encoding="utf-8") as stream:
        for line in stream:
            fields = line.split()
            if fields and fields[0] == "v":
                positions.append([-float(fields[1]), float(fields[2]), float(fields[3])])
    if len(positions) != len(source_vertices) or any(
        not all(math.isfinite(v) for v in actual) or
        list(map(_float32, actual)) != list(map(_float32, expected))
        for actual, expected in zip(positions, source_vertices)
    ):
        raise ValueError("Source vertices do not match the OBJ source-index order")
    byte_colors = all(type(v) is int for row in colors for v in row)
    colors = (validate_rgba8 if byte_colors else validate_linear_colors)(colors, len(positions))
    return {"schemaVersion": 1, "objSha256": hashlib.sha256(obj_path.read_bytes()).hexdigest(),
            "vertexCount": len(positions), "rgba8" if byte_colors else "rgbaLinear": colors}


def load_color_binding(path, obj_path, count):
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    if record.get("schemaVersion") != 1 or record.get("vertexCount") != count or \
            record.get("objSha256") != hashlib.sha256(Path(obj_path).read_bytes()).hexdigest():
        raise ValueError("Vertex-color binding does not match this OBJ")
    if ("rgba8" in record) == ("rgbaLinear" in record):
        raise ValueError("Vertex-color binding must declare one channel encoding")
    if "rgba8" in record:
        return validate_rgba8(record["rgba8"], count)
    return validate_linear_colors(record["rgbaLinear"], count)


def attach_color_uvs(mesh, colors):
    """Keep linear RGBA through physical croppers that interpolate UV corners.

    These are data channels, not texture coordinates. The active texture UV
    layer is retained. UNorm8 values are divided by 255 without sRGB decoding.
    """
    byte_colors = all(type(v) is int for row in colors for v in row)
    colors = (validate_rgba8 if byte_colors else validate_linear_colors)(colors, len(mesh.vertices))
    if mesh.uv_layers.get(RG_LAYER) or mesh.uv_layers.get(BA_LAYER):
        raise ValueError("Reserved vertex-color UV layer already exists")
    active = mesh.uv_layers.active_index
    rendered = [layer.name for layer in mesh.uv_layers if layer.active_render]
    rg = mesh.uv_layers.new(name=RG_LAYER)
    ba = mesh.uv_layers.new(name=BA_LAYER)
    # Creating another layer can invalidate an earlier RNA data handle.
    rg, ba = mesh.uv_layers[RG_LAYER], mesh.uv_layers[BA_LAYER]
    for loop in mesh.loops:
        r, g, b, a = colors[loop.vertex_index]
        divisor = 255 if byte_colors else 1
        rg.data[loop.index].uv = (r / divisor, g / divisor)
        ba.data[loop.index].uv = (b / divisor, a / divisor)
    mesh.uv_layers.active_index = active
    for layer in mesh.uv_layers:
        layer.active_render = layer.name in rendered
