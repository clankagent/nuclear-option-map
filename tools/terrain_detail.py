"""Source terrain color and layered normals from serialized material references.

Native node reconstruction retains original UV0 and macro geography. Small
procedural UV breakup and dynamic clouds remain outside this offline surface.
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


# Property identifiers come from referenced serialized material records. Texture
# names below are labels, never a search across unrelated scene assets.
DETAIL_PROPERTIES = {
    "dunes": "dacba2ee34424733acff4f8c92baea27",
    "sand": "8fd7ac9820c240c383887165ff5b5476",
    "grass": "674f68b33e3644b4acf5cea7b75e681f",
    "grass_fine": "92633bd0becc4b749d681a31beaffbaa",
    "rock": "5a49beebfe604041acd648420add582f",
    "lush": "f43950d91c9a4f2ea1cfb2f7a9ed314b",
    "fields": "1a4f7e08675c47d2a231a6bf2eacea9b",
    "field_angle": "3bd9a1d970ff4fbc9888fa54a498dd5f",
    "dunes_n": "a4670c2df1194ad9a22c4dbe817ab097",
    "sand_n": "aea52007ac564f6fbe5b78277e135f0b",
    "grass_n": "838acaebc3fd4aa599cb97870fc87e14",
    "grass_fine_n": "0c6460df7d7a4405aff8c488bbffc050",
    "rock_fine_n": "3f967dd12e9c49af9fa80c834d7c5a19",
    "rock_n": "959d2f3a5ff8423b93bfc1fd0a56dd49",
    "lush_n": "5ed7e9e7492147fe9dbd547a9ad528c1",
    "fields_n": "a867fd84b3f5415caa454567fa1e5506",
}


def texture_color_space(texture):
    """Unity TextureColorSpace: Linear=0, sRGB=1 (UnityCsReference).

    Normal data always remains linear. Older reports lack this metadata; the
    source material's noncolor detail textures are the explicit fallback.
    """
    value = texture.get("colorSpace")
    if value is not None:
        if value not in (0, 1):
            raise ValueError("Unknown serialized texture color space")
        return "sRGB" if value == 1 else "Non-Color"
    name = texture.get("textureName", "")
    return "Non-Color" if name.endswith("_n") or name in {
        "dunes_b", "sandy_b", "grassy_b", "grassy_detail_b", "rocky_b",
    } else "sRGB"


def overlay_channel(macro, detail):
    """CPU reference for the independently implemented node formula."""
    return 2 * macro * detail if macro <= .5 else 1 - 2 * (1 - macro) * (1 - detail)


def decode_normal(red, green, alpha):
    from math import sqrt
    x, y = 2 * red * alpha - 1, 2 * green - 1
    return x, y, sqrt(max(0, 1 - min(x*x + y*y, 1)))


class _Graph:
    def __init__(self, nodes, links):
        self.nodes, self.links = nodes, links

    def input(self, socket, value):
        if isinstance(value, (int, float, tuple, list)):
            socket.default_value = value
        else:
            self.links.new(value, socket)

    def math(self, operation, a, b=0):
        node = self.nodes.new("ShaderNodeMath"); node.operation = operation
        self.input(node.inputs[0], a); self.input(node.inputs[1], b)
        return node.outputs[0]

    def vector(self, operation, a, b=(0, 0, 0)):
        node = self.nodes.new("ShaderNodeVectorMath"); node.operation = operation
        self.input(node.inputs[0], a)
        self.input(node.inputs[3] if operation == "SCALE" else node.inputs[1], b)
        return node.outputs["Value" if operation == "DOT_PRODUCT" else "Vector"]

    def mix(self, a, b, factor=.5, mode="MIX"):
        node = self.nodes.new("ShaderNodeMixRGB"); node.blend_type = mode
        self.input(node.inputs[0], factor); self.input(node.inputs[1], a); self.input(node.inputs[2], b)
        return node.outputs[0]

    def split(self, value):
        node = self.nodes.new("ShaderNodeSeparateXYZ"); self.input(node.inputs[0], value)
        return tuple(node.outputs)

    def combine(self, x, y, z):
        node = self.nodes.new("ShaderNodeCombineXYZ")
        for socket, value in zip(node.inputs, (x, y, z)): self.input(socket, value)
        return node.outputs[0]

    def normal(self, texture):
        r, g, _ = self.split(texture.outputs["Color"])
        x = self.math("SUBTRACT", self.math("MULTIPLY", self.math("MULTIPLY", r, texture.outputs["Alpha"]), 2), 1)
        y = self.math("SUBTRACT", self.math("MULTIPLY", g, 2), 1)
        length = self.math("ADD", self.math("MULTIPLY", x, x), self.math("MULTIPLY", y, y))
        z = self.math("SQRT", self.math("SUBTRACT", 1, self.math("MINIMUM", length, 1)))
        return self.combine(x, y, z)

    def vector_lerp(self, a, b, t=.5):
        return self.vector("ADD", a, self.vector("SCALE", self.vector("SUBTRACT", b, a), t))


def build_surface(nodes, links, record, uv0, macro_color, load_image, *, normals=True):
    """Return (linear base-color socket, world normal socket or None).

    uv0 MUST be the mesh's original first UV set. The color/normal core follows
    source material references, including alpha and red-mask semantics. Small
    procedural macro/splat UV jitter and clouds are intentionally omitted.
    Missing detail assets return the macro unchanged. No upscale/noise is used.
    """
    graph = _Graph(nodes, links)
    textures = {t.get("property"): t for t in record.get("textures", []) if t.get("export")}
    def reference(key):
        prop = "_SampleTexture2D_" + DETAIL_PROPERTIES[key] + "_Texture_1_Texture2D" if key in DETAIL_PROPERTIES else key
        return textures.get(prop)
    required = ("dunes", "sand", "grass", "grass_fine", "rock", "fields", "lush", "field_angle",
                "_splat_grass", "_splat_rock", "_splat_fields", "_splat_lush")
    if any(reference(k) is None for k in required): return macro_color, None
    saved = {x["property"]: x["value"] for x in record.get("colors", [])}
    scale = saved.get("_detail_scale", {"r": 1, "g": 1})
    detail_uv = graph.vector("MULTIPLY", uv0, (scale["r"], scale["g"], 1))
    uv = {frequency: graph.vector("SCALE", detail_uv, frequency) for frequency in (100, 410, 1800)}
    def sample(key, coords):
        record_tex = reference(key)
        tex = nodes.new("ShaderNodeTexImage"); tex.image = load_image(record_tex["export"], texture_color_space(record_tex))
        tex.extension = "REPEAT"; tex.interpolation = "Linear"; tex.label = "Source terrain · " + key
        links.new(coords, tex.inputs["Vector"])
        return tex
    masks = {k: graph.split(sample("_splat_" + k, uv0).outputs["Color"])[0] for k in ("grass", "rock", "fields", "lush")}
    angle = graph.split(sample("field_angle", graph.vector("SCALE", uv0, 10)).outputs["Color"])[0]
    x, y, _ = graph.split(graph.vector("SUBTRACT", uv[100], (.5, .5, 0)))
    from math import sqrt
    c = sqrt(.5)
    rotated = graph.combine(graph.math("MULTIPLY", graph.math("ADD", x, y), c),
                            graph.math("MULTIPLY", graph.math("SUBTRACT", y, x), c), 0)
    rotated = graph.vector("ADD", rotated, (.5, .5, 0))
    field_uv = graph.vector_lerp(rotated, uv[100], angle)
    sand = graph.mix(sample("dunes", uv[100]).outputs["Color"], sample("sand", uv[1800]).outputs["Color"])
    grass = graph.mix(sample("grass", uv[410]).outputs["Color"], sample("grass_fine", uv[1800]).outputs["Color"])
    detail = graph.mix(sand, grass, masks["grass"])
    detail = graph.mix(detail, sample("rock", uv[410]).outputs["Color"], masks["rock"])
    # Blender Overlay has the same per-channel base<=.5 branch as the source.
    color = graph.mix(macro_color, detail, 1, "OVERLAY")
    fields = sample("fields", field_uv)
    color = graph.mix(color, fields.outputs["Color"], graph.math("MULTIPLY", fields.outputs["Alpha"], masks["fields"]))
    lush = sample("lush", uv[410])
    color = graph.mix(color, lush.outputs["Color"], graph.math("MULTIPLY", lush.outputs["Alpha"], masks["lush"]))
    normal_required = ("dunes_n", "sand_n", "grass_n", "grass_fine_n", "rock_n", "rock_fine_n", "fields_n", "lush_n", "_macro_normal")
    if not normals or any(reference(k) is None for k in normal_required): return color, None
    def n(key, coords): return graph.normal(sample(key, coords))
    sand_n = graph.vector_lerp(n("dunes_n", uv[100]), n("sand_n", uv[1800]))
    grass1, grass2 = n("grass_n", uv[410]), n("grass_fine_n", uv[1800])
    a, b = graph.split(grass1), graph.split(grass2)
    whiteout = graph.vector("NORMALIZE", graph.combine(graph.math("ADD", a[0], b[0]), graph.math("ADD", a[1], b[1]), graph.math("MULTIPLY", a[2], b[2])))
    normal = graph.vector_lerp(sand_n, whiteout, masks["grass"])
    rock = graph.vector_lerp(n("rock_fine_n", uv[1800]), n("rock_n", uv[410]))
    normal = graph.vector_lerp(normal, rock, masks["rock"])
    normal = graph.vector_lerp(normal, n("lush_n", uv[410]), masks["lush"])
    normal = graph.vector_lerp(normal, n("fields_n", field_uv), graph.math("MULTIPLY", masks["fields"], .7))
    # Reoriented normal mapping: t=macro+(0,0,1),u=detail*(-1,-1,1),
    # result=t*dot(t,u)/t.z-u. Geometry provides the actual tangent basis.
    macro = n("_macro_normal", uv0)
    t = graph.vector("ADD", macro, (0, 0, 1)); u = graph.vector("MULTIPLY", normal, (-1, -1, 1))
    tz = graph.split(t)[2]
    reoriented = graph.vector("SUBTRACT", graph.vector("SCALE", t, graph.math("DIVIDE", graph.vector("DOT_PRODUCT", t, u), graph.math("MAXIMUM", tz, 1e-6))), u)
    reoriented = graph.vector("NORMALIZE", reoriented)
    encoded = graph.vector("ADD", graph.vector("SCALE", reoriented, .5), (.5, .5, .5))
    node = nodes.new("ShaderNodeNormalMap"); node.space = "TANGENT"; node.label = "Source terrain layered tangent normals"
    links.new(encoded, node.inputs["Color"])
    return color, node.outputs["Normal"]


def build_detail(nodes, links, record, macro_uv, macro_color, load_image, **unused):
    """Compatibility color-only entry point; pass original UV0 as macro_uv."""
    return build_surface(nodes, links, record, macro_uv, macro_color, load_image, normals=False)[0]


if __name__ == "__main__":
    import argparse, json
    parser = argparse.ArgumentParser(description="Legacy high-pass preparation (not used by source terrain surface)")
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8-sig"))
    print("Source terrain graph uses original referenced RGBA textures; no high-pass preparation required.")
