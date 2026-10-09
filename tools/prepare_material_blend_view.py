"""Set map-scale Blender viewport/camera defaults in a private material blend."""
import math
import sys

import bpy
from mathutils import Euler
from render_safety import configure_scene


def prepare_view(width: float, height: float) -> dict[str, float | int | str | None]:
    """Set metric units, an overview camera and a map-scale orthographic viewport."""
    extent = max(float(width), float(height))
    clip_end = max(200000.0, 2.0 * extent)
    distance = 1.2 * extent
    scene = bpy.context.scene
    # Packed terrain scenes can contain gigabytes of decoded textures. Keep
    # opening the file in solid view and explicit rendering on the CPU.
    configure_scene(scene)
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "METERS"
    overview = next((obj for obj in bpy.data.objects
                     if obj.type == "CAMERA" and obj.name.startswith("Overview")), None)
    if overview:
        scene.camera = overview
        overview.data.clip_end = clip_end
    view_count = 0
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            view = area.spaces.active
            view.shading.type = "SOLID"
            view.clip_start = 1.0
            view.clip_end = clip_end
            region = view.region_3d
            region.view_distance = distance
            region.view_location = (0.0, 0.0, 0.0)
            region.view_rotation = Euler((math.radians(55), 0.0, math.radians(42)), "XYZ").to_quaternion()
            region.view_perspective = "ORTHO"
            view_count += 1
    result = {"width": float(width), "height": float(height), "viewCount": view_count,
              "distance": distance, "clipEnd": clip_end,
              "camera": scene.camera.name if scene.camera else None}
    print(f"VIEW_READY {bpy.data.filepath} {result}")
    return result


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:]
    if len(args) != 2:
        raise SystemExit("Pass map width and height in meters after --")
    prepare_view(*map(float, args))
    bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
