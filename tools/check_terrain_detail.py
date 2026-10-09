"""Render matched source macro / enhanced ground at verified scene coordinates."""
import json
import sys
from pathlib import Path
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parent))
from terrain_detail import build_upscaled_macro
from render_safety import guarded_render


def check(samples_file, directory, report_file=None):
    samples = json.loads(Path(samples_file).read_text(encoding="utf-8-sig"))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    camera = scene.camera
    old_location = camera.location.copy()
    old_scale = camera.data.ortho_scale
    old_size = (scene.render.resolution_x, scene.render.resolution_y)
    wiring = []
    macro_images = []
    records = json.loads(Path(report_file).read_text(encoding="utf-8-sig"))["materials"] if report_file else []
    for mat in bpy.data.materials:
        if not mat.use_nodes:
            continue
        enhanced = next((n for n in mat.node_tree.nodes if n.label == "Approximate source detail; macro geography retained"), None)
        macro = next((n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.label.startswith("_macro_basecolor")), None)
        if enhanced and macro:
            targets = [link.to_socket for link in list(mat.node_tree.links) if link.from_socket == enhanced.outputs[0]]
            wiring.append((mat.node_tree.links, macro.outputs["Color"], enhanced.outputs[0], targets))
            record = next((r for r in records if r["name"] == mat.name), None)
            if record:
                texture = next(t for t in record["textures"] if t.get("property") == "_macro_basecolor" and t.get("export"))
                prepared = Path(texture["export"]).parent.parent / "terrain-detail" / (texture["textureName"] + "-2x.png")
                if not prepared.exists():
                    raise ValueError("Missing 2x macro derivative")
                low = bpy.data.images.load(texture["export"], check_existing=True)
                failed = macro.image
                macro.image = low
                if failed != low and failed.size[0] > 8192 and failed.users == 0:
                    bpy.data.images.remove(failed)
                def loader(path, space):
                    image = bpy.data.images.load(str(Path(path).resolve()), check_existing=True)
                    image.colorspace_settings.name = space
                    return image
                uv = macro.inputs["Vector"].links[0].from_socket
                high = build_upscaled_macro(mat.node_tree.nodes, mat.node_tree.links, texture, uv, loader)
                mat.node_tree.links.new(high, enhanced.inputs[1])
                macro_images.append((macro, low, high))
    if not wiring:
        raise ValueError("No enhanced terrain materials")
    scene.render.resolution_x = scene.render.resolution_y = 768
    camera.data.ortho_scale = 1536
    for sample in samples:
        if sample["name"] not in ("mountain", "urban", "coast", "city"):
            continue
        camera.location = (sample["x"], -sample["z"], 12000)
        for label, enhanced in (("before", False), ("after", True)):
            for macro, low, high in macro_images:
                macro.interpolation = "Cubic" if enhanced else "Linear"
            for links, macro, output, targets in wiring:
                for target in targets:
                    links.new(output if enhanced else macro, target)
            scene.render.filepath = str((directory / (sample["name"] + "-" + label + ".png")).resolve())
            guarded_render(scene)
    for image in bpy.data.images:
        if image.source == "FILE" and image.packed_file is None:
            image.pack()
    if any(image.source == "FILE" and image.packed_file is None for image in bpy.data.images):
        raise AssertionError("Scene has unpacked images")
    camera.location = old_location
    camera.data.ortho_scale = old_scale
    scene.render.resolution_x, scene.render.resolution_y = old_size
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
    print("DETAIL_VERIFIED_AND_PACKED", bpy.data.filepath)


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:]
    if len(args) not in (2, 3):
        raise SystemExit("Expected SAMPLES OUTPUT_DIRECTORY [MATERIAL_REPORT]")
    check(*args)
