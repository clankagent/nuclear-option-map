"""Pure scalar helpers for the Unity DXT5nm tangent-normal channel layout."""
from math import sqrt


def decode_dxt5nm_rg(alpha: float, green: float) -> tuple[float, float, float]:
    """Return Blender Normal Map node RGB from Unity's alpha-X / green-Y channels.

    Inputs and outputs are normalized [0, 1] values. X and Y are remapped to
    [-1, 1], Z is reconstructed as positive sqrt(max(0, 1 - X² - Y²)), then
    components are encoded back to [0, 1] for Blender's Normal Map node. The
    node normalizes vectors that land outside the unit disk due to compression.
    """
    alpha = min(1.0, max(0.0, float(alpha)))
    green = min(1.0, max(0.0, float(green)))
    x, y = alpha * 2.0 - 1.0, green * 2.0 - 1.0
    z = sqrt(max(0.0, 1.0 - x * x - y * y))
    return ((x + 1.0) * 0.5, (y + 1.0) * 0.5, (z + 1.0) * 0.5)


def build_dxt5nm_encoded(nodes, links, alpha_input, green_input):
    """Build the same bounded decode as Blender nodes and return encoded RGB."""
    def math_node(operation, a, b=None):
        node = nodes.new("ShaderNodeMath")
        node.operation = operation
        links.new(a, node.inputs[0])
        if b is not None:
            links.new(b, node.inputs[1])
        return node.outputs[0]

    def remap(value):
        result = nodes.new("ShaderNodeMath")
        result.operation = "MULTIPLY_ADD"
        links.new(value, result.inputs[0])
        result.inputs[1].default_value = 2.0
        result.inputs[2].default_value = -1.0
        return result.outputs[0]

    x, y = remap(alpha_input), remap(green_input)
    squared = math_node("ADD", math_node("MULTIPLY", x, x), math_node("MULTIPLY", y, y))
    one = nodes.new("ShaderNodeValue")
    one.outputs[0].default_value = 1.0
    one_minus_squared = math_node("SUBTRACT", one.outputs[0], squared)
    z_node = nodes.new("ShaderNodeMath")
    z_node.operation = "MAXIMUM"
    links.new(one_minus_squared, z_node.inputs[0])
    z_node.inputs[1].default_value = 0.0
    z = math_node("SQRT", z_node.outputs[0])

    encoded = nodes.new("ShaderNodeCombineXYZ")
    for component, value in zip(("X", "Y", "Z"), (x, y, z)):
        result = nodes.new("ShaderNodeMath")
        result.operation = "MULTIPLY_ADD"
        links.new(value, result.inputs[0])
        result.inputs[1].default_value = 0.5
        result.inputs[2].default_value = 0.5
        links.new(result.outputs[0], encoded.inputs[component])
    return encoded.outputs[0]
