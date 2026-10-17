"""Plan deterministic front faces for coincident, opposite light triangles.

This module does not change meshes. It returns original triangle indices so a
caller can retain all corner data without interpolation or index guessing.
Coordinates and attributes compare exactly: nearby surfaces remain separate.
The caller must identify the light materials and the fixed camera direction.
"""
from dataclasses import dataclass
from math import isfinite, sqrt


@dataclass(frozen=True)
class Corner:
    position: tuple[float, float, float]
    uv_layers: tuple[tuple[float, float], ...]
    rgba: tuple[float, float, float, float]
    normal: tuple[float, float, float]


@dataclass(frozen=True)
class Triangle:
    corners: tuple[Corner, Corner, Corner]
    material: int | str


@dataclass(frozen=True)
class FacePlan:
    retained: tuple[int, ...]
    # Each pair is (retained camera-facing index, removed opposite index).
    removed_pairs: tuple[tuple[int, int], ...]
    world_determinant: float


IDENTITY = ((1., 0., 0., 0.), (0., 1., 0., 0.),
            (0., 0., 1., 0.), (0., 0., 0., 1.))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _unit(v):
    length = sqrt(_dot(v, v))
    if not length:
        raise ValueError("Zero direction or degenerate triangle")
    return tuple(x / length for x in v)


def _normal(points):
    a = tuple(points[1][k] - points[0][k] for k in range(3))
    b = tuple(points[2][k] - points[0][k] for k in range(3))
    return _unit((a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2],
                  a[0]*b[1]-a[1]*b[0]))


def _attributes(triangle):
    return tuple(sorted((c.position, c.uv_layers, c.rgba)
                        for c in triangle.corners))


def plan_camera_facing_faces(triangles, *, target_materials,
                             world_matrix=IDENTITY,
                             surface_to_camera=(0., 0., 1.),
                             min_facing_cosine=0., normal_tolerance=.001):
    """Return a removal plan for proved opposite duplicate triangles only.

    World orientation comes from transformed ordered positions, including a
    negative transform determinant. No winding is silently reversed. The caller
    must use the same world-bake convention when applying this plan.

    Equal-position target triangles with unequal UV/color data, unbalanced
    opposite multiplicities, or inconsistent normals refuse the operation.
    Custom-normal comparisons allow bounded encoding error; positions, every UV
    layer and RGBA use exact equality. Original data never changes here.
    """
    triangles = tuple(triangles)
    targets = frozenset(target_materials)
    matrix = tuple(tuple(row) for row in world_matrix)
    if (len(matrix) != 4 or any(len(r) != 4 for r in matrix)
            or any(not isfinite(v) for row in matrix for v in row)
            or matrix[3] != (0., 0., 0., 1.)):
        raise ValueError("Finite affine 4x4 world matrix required")
    a, b, c = (row[:3] for row in matrix[:3])
    determinant = (a[0]*(b[1]*c[2]-b[2]*c[1])
                   - a[1]*(b[0]*c[2]-b[2]*c[0])
                   + a[2]*(b[0]*c[1]-b[1]*c[0]))
    if determinant == 0 or not isfinite(determinant):
        raise ValueError("Invertible world matrix required")
    if (not 0 <= min_facing_cosine < 1 or not isfinite(min_facing_cosine)
            or not 0 <= normal_tolerance < .1):
        raise ValueError("Invalid facing threshold or normal tolerance")
    if len(surface_to_camera) != 3 or any(not isfinite(v) for v in surface_to_camera):
        raise ValueError("Finite camera direction required")
    direction = _unit(surface_to_camera)
    groups = {}
    for index, triangle in enumerate(triangles):
        if triangle.material not in targets:
            continue
        if len(triangle.corners) != 3:
            raise ValueError("Only triangles are supported")
        for corner in triangle.corners:
            if (len(corner.position) != 3 or len(corner.normal) != 3
                    or len(corner.rgba) != 4
                    or any(len(uv) != 2 for uv in corner.uv_layers)
                    or any(not isfinite(v) for values in
                           (corner.position, corner.normal, corner.rgba,
                            *(corner.uv_layers)) for v in values)):
                raise ValueError("Finite, complete source corner data required")
        key = (triangle.material,
               tuple(sorted(c.position for c in triangle.corners)))
        groups.setdefault(key, []).append(index)

    removed_pairs = []
    for indices in groups.values():
        if len(indices) == 1:
            continue
        reference = _attributes(triangles[indices[0]])
        if any(_attributes(triangles[i]) != reference for i in indices):
            raise ValueError("Coincident target faces have different UV or RGBA data")
        normals = {}
        facing = {}
        for i in indices:
            triangle = triangles[i]
            local = _normal([c.position for c in triangle.corners])
            for corner in triangle.corners:
                if _dot(_unit(corner.normal), local) < 1 - normal_tolerance:
                    raise ValueError("Custom normal disagrees with source winding")
            points = [tuple(sum(matrix[k][j]*c.position[j] for j in range(3))
                            + matrix[k][3] for k in range(3))
                      for c in triangle.corners]
            normals[i] = _normal(points)
            facing[i] = _dot(normals[i], direction)
        front = [i for i in indices if facing[i] > min_facing_cosine]
        back = [i for i in indices if facing[i] < -min_facing_cosine]
        if not front or not back:
            continue  # Edge-on or same-winding duplicates remain untouched.
        if len(front) != len(back) or len(front)+len(back) != len(indices):
            raise ValueError("Ambiguous opposite-face multiplicity")
        for kept, removed in zip(front, back):
            if _dot(normals[kept], normals[removed]) > -1 + 1e-10:
                raise ValueError("Coincident faces do not have opposite winding")
            one = {c.position: _unit(c.normal) for c in triangles[kept].corners}
            two = {c.position: _unit(c.normal) for c in triangles[removed].corners}
            if any(_dot(one[p], two[p]) > -1 + normal_tolerance for p in one):
                raise ValueError("Opposite faces have inconsistent custom normals")
            removed_pairs.append((kept, removed))
    removed = {back for _, back in removed_pairs}
    return FacePlan(tuple(i for i in range(len(triangles)) if i not in removed),
                    tuple(removed_pairs), determinant)
