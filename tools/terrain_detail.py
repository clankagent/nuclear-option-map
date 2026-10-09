"""Source-backed terrain microtexture, retaining the extracted macro geography.

This is an explicitly approximate detail blend, not a reconstruction of Unity's
compiled Shader Graph. RGB splat masks select only the material textures actually
referenced by the terrain material. High-pass luminance preserves macro colors.
"""
from pathlib import Path

LAYERS = (("_splat_rock", "rocky_b"), ("_splat_grass", "grassy_b"),
          ("_splat_lush", "lush_b"), ("_splat_fields", "fields_b"))
DEFAULT_STRENGTH = 0.20
DEFAULT_TILE_METERS = 1000.0


def build_upscaled_macro(nodes, links, texture, uv, load_image):
    """Split large derivatives into sampling tiles; total memory remains additive.

    This prevents single-texture dimension failures, not GPU overload. The current
    large scenes must render on CPU under render_safety.py.
    """
    base = Path(texture["export"]).parent.parent / "terrain-detail" / (texture["textureName"] + "-2x.png")
    if not base.exists():
        return None
    if max(texture.get("pixels", [0, 0])) <= 4096:
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = load_image(base, "sRGB")
        tex.interpolation = "Cubic"
        tex.label = "2x interpolated macro"
        links.new(uv, tex.inputs["Vector"])
        return tex.outputs["Color"]
    separate = nodes.new("ShaderNodeSeparateXYZ")
    links.new(uv, separate.inputs[0])
    import json
    spec = json.loads(base.with_suffix(".tiles.json").read_text(encoding="utf-8"))
    width, height = spec["size"]

    def mix_row(colors, axis, thresholds):
        value = colors[0]
        for color, threshold in zip(colors[1:], thresholds):
            compare = nodes.new("ShaderNodeMath")
            compare.operation = "GREATER_THAN"
            compare.inputs[1].default_value = threshold
            links.new(separate.outputs[axis], compare.inputs[0])
            mix = nodes.new("ShaderNodeMixRGB")
            links.new(compare.outputs[0], mix.inputs[0]); links.new(value, mix.inputs[1]); links.new(color, mix.inputs[2])
            value = mix.outputs[0]
        return value

    colors = []
    for y in reversed(range(3)):
        row = []
        for x in range(3):
            tile = next(t for t in spec["tiles"] if t["x"] == x and t["y"] == y)
            filename = base.parent / tile["file"]
            if not filename.exists():
                raise ValueError(f"Missing macro sampling tile {filename}")
            left, top, right, bottom = tile["box"]
            tw, th = right - left, bottom - top
            multiply = nodes.new("ShaderNodeVectorMath")
            multiply.operation = "MULTIPLY_ADD"
            multiply.inputs[1].default_value = (width / tw, height / th, 1)
            multiply.inputs[2].default_value = (-left / tw, -(height - bottom) / th, 0)
            links.new(uv, multiply.inputs[0])
            tex = nodes.new("ShaderNodeTexImage")
            tex.image = load_image(filename, "sRGB")
            tex.extension = "EXTEND"
            tex.interpolation = "Cubic"
            tex.label = f"2x macro tile {x}{y}; gutter4"
            links.new(multiply.outputs[0], tex.inputs["Vector"])
            row.append(tex.outputs["Color"])
        colors.append(mix_row(row, "X", [value / width for value in spec["xCuts"]]))
    return mix_row(colors, "Y", [(height - value) / height for value in reversed(spec["yCuts"])])


def prepare_detail(source, target):
    """Make a small, color-neutral high-pass asset; never upscale the macro map."""
    from PIL import Image, ImageFilter, ImageChops
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        gray = image.convert("L")
        # Difference around midpoint is neutral under multiply by 2. Contrast
        # remains bounded by the blend strength, even for saturated source noise.
        detail = ImageChops.subtract(gray, gray.filter(ImageFilter.GaussianBlur(16)),
                                     scale=1.0, offset=128)
        detail.save(target)
    return target


def build_detail(nodes, links, record, macro_uv, macro_color, load_image,
                 strength=DEFAULT_STRENGTH, tile_meters=DEFAULT_TILE_METERS):
    """Blender graph. Prepared detail files are intentionally external assets."""
    if not 0 <= strength <= 1 or tile_meters <= 0:
        raise ValueError("Invalid terrain detail parameters")
    textures = record.get("textures", [])
    geometry = nodes.new("ShaderNodeNewGeometry")
    position = nodes.new("ShaderNodeVectorMath")
    position.operation = "SCALE"
    position.inputs[3].default_value = 1.0 / tile_meters
    links.new(geometry.outputs["Position"], position.inputs[0])
    weighted = []
    for mask_name, detail_name in LAYERS:
        mask = next((t for t in textures if t.get("property") == mask_name and t.get("export")), None)
        source = next((t for t in textures if t.get("textureName") == detail_name and t.get("export")), None)
        if not mask or not source:
            continue
        path = Path(source["export"]).parent.parent / "terrain-detail" / (detail_name + "-highpass.png")
        if not path.exists():
            # Blender's bundled Python may lack Pillow: prepare with the CLI first.
            continue
        detail = nodes.new("ShaderNodeTexImage")
        detail.image = load_image(path, "Non-Color")
        detail.extension = "REPEAT"
        detail.label = "Recovered detail · " + detail_name
        links.new(position.outputs["Vector"], detail.inputs["Vector"])
        splat = nodes.new("ShaderNodeTexImage")
        splat.image = load_image(mask["export"], "Non-Color")
        splat.label = "Recovered mask · " + mask_name
        links.new(macro_uv, splat.inputs["Vector"])
        center = nodes.new("ShaderNodeMath")
        center.operation = "SUBTRACT"
        center.inputs[1].default_value = 128 / 255
        links.new(detail.outputs["Color"], center.inputs[0])
        weight = nodes.new("ShaderNodeMath")
        weight.operation = "MULTIPLY"
        links.new(center.outputs[0], weight.inputs[0])
        links.new(splat.outputs["Color"], weight.inputs[1])
        weighted.append(weight.outputs[0])
    if not weighted:
        return macro_color
    value = weighted[0]
    for other in weighted[1:]:
        add = nodes.new("ShaderNodeMath")
        add.operation = "ADD"
        links.new(value, add.inputs[0]); links.new(other, add.inputs[1])
        value = add.outputs[0]
    gain = nodes.new("ShaderNodeMath")
    gain.operation = "MULTIPLY_ADD"
    gain.inputs[1].default_value = 2 * strength
    gain.inputs[2].default_value = 1.0
    links.new(value, gain.inputs[0])
    multiply = nodes.new("ShaderNodeMixRGB")
    multiply.blend_type = "MULTIPLY"
    multiply.inputs[0].default_value = 1.0
    multiply.label = "Approximate source detail; macro geography retained"
    links.new(macro_color, multiply.inputs[1]); links.new(gain.outputs[0], multiply.inputs[2])
    return multiply.outputs[0]


if __name__ == "__main__":
    import argparse, json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8-sig"))
    seen = set()
    for material in report["materials"]:
        if material.get("shaderName") != "Shader Graphs/TerrainShader":
            continue
        for _, name in LAYERS:
            source = next((t for t in material["textures"] if t.get("textureName") == name and t.get("export")), None)
            if source and name not in seen:
                seen.add(name)
                target = Path(source["export"]).parent.parent / "terrain-detail" / (name + "-highpass.png")
                prepare_detail(source["export"], target)
                print(target)
