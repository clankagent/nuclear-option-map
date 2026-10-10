"""Controlled CUDA experiment on a hash-bound, physically cropped private scene.

Run inside Blender with CYCLES_CONCURRENT_STATES_FACTOR=0.000001. This is
not the full-map baker. It never alters source files or publishes imagery.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import os

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_safety import (configure_cropped_gpu_trial,
                           lower_process_priority, render_slot, scene_evidence,
                           validate_cropped_gpu_trial, cuda_queue_plan)


def inspect_scene(bpy, manifest):
    """Inspect loaded inputs before querying or initializing any GPU backend."""
    unsupported = []
    x0, y0, x1, y1 = manifest["cropBoundsBlenderXY"]
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or not (0 < x1-x0 <= 4096 and 0 < y1-y0 <= 4096):
        raise ValueError("Trial crop must be finite and at most 4096 meters per edge including halo")
    triangles = vertices = loops = attribute_bytes = 0
    if bpy.data.libraries:
        unsupported.append("linked libraries")
    for obj in bpy.context.scene.objects:
        if obj.type not in {"MESH", "LIGHT", "CAMERA", "EMPTY"}:
            unsupported.append(f"{obj.name}: {obj.type}")
        if obj.modifiers or obj.particle_systems or obj.instance_type != "NONE":
            unsupported.append(f"{obj.name}: generated/instanced geometry")
        if obj.animation_data or (obj.data and getattr(obj.data, "animation_data", None)):
            unsupported.append(f"{obj.name}: animated inputs")
        if obj.type == "MESH":
            if obj.data.shape_keys:
                unsupported.append(f"{obj.name}: shape keys")
            obj.data.calc_loop_triangles()
            triangles += len(obj.data.loop_triangles)
            vertices += len(obj.data.vertices)
            loops += len(obj.data.loops)
            attribute_bytes += sum(len(a.data) * 16 for a in obj.data.attributes)
            for vertex in obj.data.vertices:
                point = obj.matrix_world @ vertex.co
                if not all(math.isfinite(v) for v in point) or not (x0-.1 <= point.x <= x1+.1 and y0-.1 <= point.y <= y1+.1):
                    unsupported.append(f"{obj.name}: geometry outside physical crop")
                    break
        if obj.type == "LIGHT" and obj.data.type not in {"SUN", "POINT", "AREA", "SPOT"}:
            unsupported.append(f"{obj.name}: unsupported light type")
    if sum(obj.type == "LIGHT" for obj in bpy.context.scene.objects) > 4:
        unsupported.append("more than four lights")
    if triangles != sum(m["triangles"] for m in manifest["meshes"]):
        raise ValueError("Loaded geometry does not match cropped scene manifest")
    trees = []
    for material in bpy.data.materials:
        if material.users and material.use_nodes:
            trees.append(material.node_tree)
    if bpy.context.scene.world and bpy.context.scene.world.use_nodes:
        trees.append(bpy.context.scene.world.node_tree)
    for obj in bpy.context.scene.objects:
        if obj.type == "LIGHT" and obj.data.use_nodes:
            trees.append(obj.data.node_tree)
    seen, node_count = set(), 0
    while trees:
        tree = trees.pop()
        if tree.as_pointer() in seen:
            continue
        seen.add(tree.as_pointer())
        if tree.animation_data:
            unsupported.append(f"{tree.name}: animated shader")
        node_count += len(tree.nodes)
        for node in tree.nodes:
            if node.type in {"SCRIPT", "TEX_SKY", "VOLUME_PRINCIPLED", "VOLUME_SCATTER", "VOLUME_ABSORPTION"}:
                unsupported.append(f"{tree.name}: {node.type}")
            if node.type == "GROUP" and node.node_tree:
                trees.append(node.node_tree)
            if node.type in {"OUTPUT_MATERIAL", "OUTPUT_WORLD"} and node.inputs.get("Volume") and node.inputs["Volume"].is_linked:
                unsupported.append(f"{tree.name}: volume output")
            if node.type == "OUTPUT_MATERIAL" and node.inputs["Displacement"].is_linked:
                unsupported.append(f"{tree.name}: displacement output")
    for image in bpy.data.images:
        if image.type in {"RENDER_RESULT", "COMPOSITING"}:
            continue
        if image.users and image.source == "FILE" and not image.packed_file:
            unsupported.append(f"{image.name}: unpacked file image")
        if image.users and (image.source not in {"FILE", "GENERATED"} or image.type not in {"IMAGE", "RENDER_RESULT", "COMPOSITING"}):
            unsupported.append(f"{image.name}: dynamic/unsupported image")
    return {"physicallyCropped": True, "triangles": triangles, "vertices": vertices,
            "loops": loops, "attributeBytesConservative": attribute_bytes,
            "shaderNodes": node_count, "unsupported": unsupported,
            "cropBoundsBlenderXY": [x0, y0, x1, y1]}


def main(argv):
    import bpy
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--edge", type=int, default=256)
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--cpu-control", action="store_true")
    parser.add_argument("--queue-info-only", type=Path)
    parser.add_argument("--queue-states", type=int, choices=(1024,8192), default=1024)
    args = parser.parse_args(argv)
    source = Path(bpy.data.filepath).resolve()
    output = args.output.resolve()
    if output.exists():
        parser.error("Use a fresh output directory")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    with source.open("rb") as handle:
        source_hash = hashlib.file_digest(handle, "sha256").hexdigest()
    if manifest.get("outputSha256") != source_hash:
        parser.error("Input scene hash does not match physical crop manifest")
    if manifest.get("gpuInitialized") is not False:
        parser.error("Physical crop must have been prepared on CPU")
    scene = bpy.context.scene
    scene.render.resolution_x = scene.render.resolution_y = args.edge
    scene.render.resolution_percentage = 100
    scene.render.use_border = scene.render.use_crop_to_border = False
    scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
    # Inherited AOV/cryptomatte/motion-blur passes must not expand buffers.
    first_layer = scene.view_layers[0]
    for layer in list(scene.view_layers)[1:]:
        scene.view_layers.remove(layer)
    first_layer.use = True
    for holder in (first_layer, first_layer.cycles):
        for prop in holder.bl_rna.properties:
            if prop.identifier.startswith("use_pass_") and prop.type == "BOOLEAN" and not prop.is_readonly:
                setattr(holder, prop.identifier, prop.identifier == "use_pass_combined")
    for aov in list(first_layer.aovs):
        first_layer.aovs.remove(aov)
    for group in list(first_layer.lightgroups):
        first_layer.lightgroups.remove(group)
    inventory = inspect_scene(bpy, manifest)
    planning = validate_cropped_gpu_trial(inventory, bpy.data.images, args.edge, args.edge, args.samples)
    if not scene.camera or scene.camera.data.type != "ORTHO":
        parser.error("Cropped scene must have an orthographic camera")
    if abs(scene.camera.data.ortho_scale-manifest["footprintMeters"]) > .01:
        parser.error("Camera does not match cropped scene footprint")
    scene.cycles.seed = 0
    scene.render.filter_size = 1.0
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    output.mkdir(parents=True)
    scene.render.filepath = str(output / "raw.png")
    # Match the source overhead convention, including north-up output.
    scene.use_nodes = True
    scene.node_tree.nodes.clear()
    layers = scene.node_tree.nodes.new("CompositorNodeRLayers")
    flip = scene.node_tree.nodes.new("CompositorNodeFlip")
    flip.axis = "Y"
    final = scene.node_tree.nodes.new("CompositorNodeComposite")
    scene.node_tree.links.new(layers.outputs["Image"], flip.inputs[0])
    scene.node_tree.links.new(flip.outputs[0], final.inputs[0])
    import _cycles
    _cycles.enable_print_stats()
    with render_slot():
        lower_process_priority()
        planning = configure_cropped_gpu_trial(scene, inventory, bpy.data.images, samples=args.samples)
        selected = []
        if args.cpu_control:
            scene.cycles.device = "CPU"
            scene.cycles.time_limit = 0
        else:
            preferences = bpy.context.preferences.addons["cycles"].preferences
            preferences.compute_device_type = "CUDA"
            preferences.get_devices()
            devices = [d for d in preferences.devices if d.type == "CUDA"]
            if not devices:
                raise RuntimeError("CUDA device unavailable; no automatic backend fallback")
            for device in preferences.devices:
                device.use = device.id == devices[0].id and device.type == "CUDA"
                if device.use:
                    selected.append({"id": device.id, "name": device.name, "type": device.type})
            if len(selected) != 1:
                raise RuntimeError("GPU trial requires exactly one selected CUDA device")
            # Read attributes without a context or allocation. Require the
            # sole CUDA ordinal to remove ambiguity from virtual DXGI adapters.
            import ctypes
            driver = ctypes.WinDLL("nvcuda.dll")
            count = ctypes.c_int()
            if driver.cuDeviceGetCount(ctypes.byref(count)) or count.value != 1:
                raise RuntimeError("Trial requires one unambiguous CUDA ordinal")
            sm, threads = ctypes.c_int(), ctypes.c_int()
            if driver.cuDeviceGetAttribute(ctypes.byref(sm), 16, 0) or driver.cuDeviceGetAttribute(ctypes.byref(threads), 39, 0):
                raise RuntimeError("CUDA device attribute query failed")
            queue = cuda_queue_plan(sm.value, threads.value, args.queue_states)
            if args.queue_info_only:
                args.queue_info_only.write_text(json.dumps({**queue, "sceneSha256": source_hash,
                                                             "deviceSelection": selected}, indent=2))
                print("CUDA_QUEUE_PLAN", json.dumps(queue), flush=True)
                return
            import struct
            factor = float(os.environ["CYCLES_CONCURRENT_STATES_FACTOR"])
            f32 = lambda value: struct.unpack("f", struct.pack("f", value))[0]
            states = max(int(f32(queue["defaultStates"] * f32(factor))), 1024)
            if states > args.queue_states:
                raise RuntimeError("Actual selected-device queue exceeds requested bound")
            planning["queueStatesExpected"] = states
        settings = {"scene": str(source), "sceneSha256": source_hash,
                    "cropManifest": str(args.manifest.resolve()), "inventory": inventory,
                    "planning": planning, "deviceSelection": selected,
                    "centerX": manifest["centerX"], "centerZ": manifest["centerZ"],
                    "footprintMeters": manifest["footprintMeters"],
                    "metersPerPixel": manifest["footprintMeters"] / args.edge,
                    "textureReductions": manifest.get("imageReductions", []),
                    "sourceFidelityAccepted": False, **scene_evidence(scene)}
        (output / "settings.json").write_text(json.dumps(settings, indent=2))
        print("CROPPED_TRIAL_PREFLIGHT", json.dumps(settings), flush=True)
        started = time.monotonic()
        bpy.ops.render.render(write_still=True)
        elapsed = time.monotonic() - started
        proof = {**settings, "seconds": elapsed,
                 "outputBytes": (output / "raw.png").stat().st_size,
                 "outputSha256": hashlib.sha256((output / "raw.png").read_bytes()).hexdigest()}
        (output / "proof.json").write_text(json.dumps(proof, indent=2))
        print("CROPPED_TRIAL_COMPLETE", json.dumps(proof), flush=True)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1:])
