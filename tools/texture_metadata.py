"""Retain serialized texture sampling metadata alongside exported pixels."""


def sampling_metadata(texture):
    """Optional additive fields keep older Unity texture records compatible."""
    result = {}
    color_space = getattr(texture, "m_ColorSpace", None)
    if color_space is not None:
        result["colorSpace"] = int(color_space)
    settings = getattr(texture, "m_TextureSettings", None)
    if settings is not None:
        result["textureSettings"] = {
            name: getattr(settings, name)
            for name in ("m_FilterMode", "m_Aniso", "m_MipBias", "m_WrapMode",
                         "m_WrapU", "m_WrapV", "m_WrapW")
            if getattr(settings, name, None) is not None
        }
    return result
