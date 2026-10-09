"""Blender background entry point: scene.json -> a transformed .blend scene.

blender --background --python tools/import_blender.py -- scene.json output.blend MAP_ID
"""
import json
import sys
from pathlib import Path
import bpy
from mathutils import Matrix, Quaternion, Vector


def key(ref):
    return (ref["assetFile"], ref["pathId"])


def load(scene_path, destination, map_id, mesh_filter=None):
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    records = [r for r in scene["objects"] if r["map"] == map_id]
    transforms = {key(r): r for r in records if r["type"] == "Transform"}
    world = {}

    def matrix(identity, chain=()):
        ident = key(identity)
        if ident in world:
            return world[ident]
        if ident in chain:
            raise ValueError("Cycle in serialized Transform hierarchy")
        record = transforms[ident]
        t = record["transform"]
        p, q, s = t["m_LocalPosition"], t["m_LocalRotation"], t["m_LocalScale"]
        local = Matrix.LocRotScale(Vector((p["x"], p["y"], p["z"])),
            Quaternion((q["w"], q["x"], q["y"], q["z"])), Vector((s["x"], s["y"], s["z"])))
        parent = record["parent"]
        if parent is not None:
            if key(parent) not in transforms:
                raise ValueError(f"Transform parent outside exported graph: {parent}")
            local = matrix(parent, chain + (ident,)) @ local
        world[ident] = local
        return local

    # Unity world X/Y/Z -> Blender X/-Z/Y. UnityPy OBJ reflects local X.
    conversion = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
    meshes = {}
    collection = bpy.data.collections.new(map_id)
    bpy.context.scene.collection.children.link(collection)
    for r in records:
        if "mesh" not in r:
            continue
        if mesh_filter is not None and not mesh_filter(r):
            continue
        filename = r["file"]
        if Path(filename).name != filename:
            raise ValueError("Geometry filename must stay within scene folder")
        if filename not in meshes:
            vertices, faces = [], []
            with (scene_path.parent / filename).open(encoding="utf-8") as stream:
                for line in stream:
                    parts = line.split()
                    if parts and parts[0] == "v":
                        x, y, z = (float(x) for x in parts[1:4])
                        vertices.append((-x, y, z))
                    elif parts and parts[0] == "f":
                        faces.append(tuple(int(x.split("/")[0]) - 1 for x in reversed(parts[1:])))
            mesh = bpy.data.meshes.new(filename)
            mesh.from_pydata(vertices, [], faces)
            mesh.update()
            meshes[filename] = mesh
        obj = bpy.data.objects.new(r["name"] or filename, meshes[filename])
        collection.objects.link(obj)
        obj.matrix_world = conversion @ matrix(r["transform"])
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1
    if destination is not None:
        bpy.ops.wm.save_as_mainfile(filepath=str(destination.resolve()))
    return collection


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:]
    if len(args) != 3:
        raise SystemExit("Expected scene.json output.blend MAP_ID after --")
    load(Path(args[0]), Path(args[1]), args[2])
