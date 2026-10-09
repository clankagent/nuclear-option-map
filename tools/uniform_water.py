"""Fail-closed translation-invariant plane eligibility, without Blender imports."""

import math
import shutil
from pathlib import Path


def eligible(snapshot):
    objects = snapshot.get("geometry", [])
    if (len(objects) != 1 or snapshot.get("volume") or snapshot.get("materialOverride")
            or snapshot.get("holdout") or not snapshot.get("uniformWorld") or not snapshot.get("orthoDown")):
        return False
    obj = objects[0]
    if not all(obj.get(k) for k in ("mesh", "horizontalQuad", "convexQuad", "axisAligned", "constantOpaqueMaterial")):
        return False
    bounds, frame = obj["bounds"], snapshot["frame"]
    if not (bounds[0] <= frame[0] and bounds[1] <= frame[1] and bounds[2] >= frame[2] and bounds[3] >= frame[3]):
        return False
    for light in snapshot.get("lights", []):
        if light.get("type") != "SUN" or not light.get("constant"):
            return False
        dx, dy, dz = light["direction"]
        if abs(dz) < .05:
            return False
        reach = max(0, snapshot["sceneTop"] - obj["height"]) * math.hypot(dx, dy) / abs(dz)
        if snapshot["shadowMargin"] < reach:
            return False
    return True


def copy_verified_chunk(cache, destination, snapshot, *, cached_signature=None, render_signature=None):
    """Only reuse an already rendered chunk when current actual scene qualifies."""
    if (not cached_signature or cached_signature != render_signature
            or not eligible(snapshot) or not Path(cache).is_file()):
        return False
    shutil.copyfile(cache, destination)
    return True
