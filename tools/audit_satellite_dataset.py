# /// script
# requires-python = ">=3.11"
# dependencies = ["Pillow>=11,<13"]
# ///
"""Audit a completed satellite pyramid; optionally promote its descriptor.

Default reads only the dataset and writes the requested audit receipt. Promotion
is explicit with --variants and belongs after visual quality acceptance.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

from PIL import Image
from render_satellite_tiles import denoise_geometry
from uniform_water import eligible as uniform_water_eligible

MAP_BOUNDS = {"heartland-46396": (81920, 81920),
              "ignus-archipelago-46395": (163840, 81920)}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def file_sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def aggregate_content_sha256(records):
    """Hash sorted UTF-8 relative paths, NUL, ASCII SHA-256, LF."""
    digest = hashlib.sha256()
    for relative_path, content_hash in sorted(records):
        digest.update(relative_path.encode("utf-8") + b"\0" + content_hash.encode("ascii") + b"\n")
    return digest.hexdigest()


def atomic_write(path, value, original=None):
    """Same-directory replace; compare original immediately before promotion."""
    path = Path(path)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        if original is not None:
            require(path.read_bytes() == original, "Concurrent variants update; audit again with current metadata")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def latest_chunk_receipts(dataset, maximum):
    scratch = dataset / ".render-work"
    history = sorted(scratch.glob(f"level-{maximum}-receipt-history-*.json"),
                     key=lambda p: (p.stat().st_mtime_ns, p.name))
    current = scratch / f"level-{maximum}-receipt.json"
    require(current.is_file(), f"Missing current receipt: {current}")
    selected = {}
    for path in [*history, current]:
        receipts = read_json(path)
        require(isinstance(receipts, list), f"Receipt must be an array: {path}")
        local = set()
        for render in receipts:
            key = (render["column"], render["row"])
            require(key not in local, f"Duplicate chunk {key} in {path}")
            local.add(key)
            selected.pop(key, None)
            selected[key] = render
    return list(selected.values()), [str(p.resolve()) for p in [*history, current]]


def audit_chunks(renders, source, dataset, level, samples):
    span = min(4, level["columns"], level["rows"])
    expected = {(x, y) for x in range(0, level["columns"], span)
                for y in range(0, level["rows"], span)}
    seen, signatures = set(), set()
    mpp = source["worldWidth"] / level["width"]
    settings = source.get("cpuDenoise")
    require(settings is not None, "Requested satellite imagery must be CPU denoised")
    profile = settings["profile"]
    require(profile["device"] == "CPU" and profile["threads"] == 2, "Invalid CPU denoiser profile")
    if settings.get("linear"):
        require(profile.get("hdr") is True and profile.get("srgb") is False,
                "Linear denoising requires HDR true and sRGB false")
    for render in renders:
        key = (render["column"], render["row"])
        require(key in expected and key not in seen, f"Unknown/duplicate chunk {key}")
        seen.add(key)
        require(render["level"] == level["level"] and render["span"] == span, f"Chunk grid differs: {key}")
        size = span * 512 + 4
        require(render["size"] == size and render["metersPerPixel"] == mpp, f"Chunk scale differs: {key}")
        require(render["x"] == -source["worldWidth"] / 2 + (key[0] + span / 2) * 512 * mpp
                and render["z"] == source["worldDepth"] / 2 - (key[1] + span / 2) * 512 * mpp,
                f"Chunk center differs: {key}")
        require(Path(render["path"]).resolve() == (dataset / ".render-work" /
                f"{level['level']}-{key[0]}-{key[1]}.png").resolve(), f"Chunk path differs: {key}")
        if "shadowMarginMeters" in source:
            require(render.get("shadowMarginMeters") == source["shadowMarginMeters"], "Shadow margin differs")
        geometry = denoise_geometry(size, profile)
        edge = geometry["renderEdge"]
        scene = render["actualScene"]
        required = {"engine": "CYCLES", "device": "CPU", "samples": samples,
                    "threadsMode": "FIXED", "threads": 2, "compositorDevice": "CPU",
                    "denoising": False, "previewDenoising": False,
                    "timeLimitSeconds": 0, "width": edge, "height": edge,
                    "resolutionPercentage": 100}
        for key_name, value in required.items():
            require(scene.get(key_name) == value, f"Actual scene differs: {key} {key_name}")
        require("layerDenoising" in scene and not any(scene["layerDenoising"]), "Layer GPU denoising enabled/unrecorded")
        if settings.get("linear"):
            require(scene.get("enabledPasses") == ["use_pass_combined"], "Linear render must record Combined-only output")
        cleanup = render.get("cpuDenoise", {})
        for key_name, value in profile.items():
            require(cleanup.get(key_name) == value, f"CPU denoiser differs: {key} {key_name}")
        require(cleanup.get("padding") == geometry["padding"] and cleanup.get("renderEdge") == edge,
                f"Denoising padding differs: {key}")
        require(cleanup.get("width") == cleanup.get("height") == edge, "Denoiser input dimensions differ")
        require(cleanup.get("outputWidth") == cleanup.get("outputHeight") == size, "Denoised dimensions differ")
        if settings.get("linear"):
            require(cleanup.get("colorSpace") == "scene-linear", "Denoising color space differs")
        proof, reused = render.get("uniformWaterProof"), render.get("uniformWaterReused", False)
        if proof is not None or reused:
            require(source.get("reuseUniformWater") and proof is not None and uniform_water_eligible(proof), "Invalid water reuse proof")
            half = mpp * edge / 2
            require(proof["frame"] == [render["x"] - half, -render["z"] - half,
                                       render["x"] + half, -render["z"] + half], "Water frame differs")
            require(proof["shadowMargin"] == render["shadowMarginMeters"], "Water shadow margin differs")
            signature = json.dumps(proof["surfaceSignature"], sort_keys=True)
            if not reused:
                signatures.add(signature)
        require(math.isfinite(render["seconds"]) and render["seconds"] >= 0, "Invalid render duration")
    for render in renders:
        if render.get("uniformWaterReused"):
            require(json.dumps(render["uniformWaterProof"]["surfaceSignature"], sort_keys=True) in signatures,
                    "Reused water has no genuine source render in selected receipts")
    require(seen == expected, f"Missing source chunks: {sorted(expected - seen)}")


def audit_dataset(dataset, map_id, maximum=6, samples=4, expected_source_sha256=None):
    dataset = Path(dataset).resolve()
    require(map_id in MAP_BOUNDS and 0 <= maximum <= 6, "Unknown map or unsupported maximum level")
    metadata = read_json(dataset / "pyramid.json")
    levels = metadata["tiles"]["levels"]
    require([l["level"] for l in levels] == list(range(maximum + 1)), "Incomplete published levels")
    require(metadata["tiles"]["tileSize"] == 512 and metadata["tiles"]["gutter"] == 2, "Tile layout differs")
    source = read_json(dataset / ".render-work/source.json")
    require((source["worldWidth"], source["worldDepth"]) == MAP_BOUNDS[map_id], "World bounds differ")
    require(source["tileSize"] == 512 and source["gutter"] == 2, "Source tile layout differs")
    profile = source["renderProfile"]
    for key, value in {"engine": "CYCLES", "device": "cpu", "samples": samples,
                       "threads": 2, "denoising": False, "compositorDevice": "CPU"}.items():
        require(profile.get(key) == value, f"Source render profile differs: {key}")
    blend = Path(source["blend"])
    require(blend.is_file(), "Missing source scene")
    stat = blend.stat()
    require(stat.st_size == source["blendBytes"] and stat.st_mtime_ns == source["blendMtimeNs"], "Source scene changed")
    source_sha256 = file_sha256(blend)
    if source.get("blendSha256"):
        require(source_sha256 == source["blendSha256"], "Source scene hash differs")
    if expected_source_sha256 is not None:
        require(len(expected_source_sha256) == 64 and
                all(c in "0123456789abcdef" for c in expected_source_sha256), "Expected source SHA-256 must be 64 lowercase hex digits")
        require(source_sha256 == expected_source_sha256, "Source scene differs from expected integrity hash")
    version = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()[:16]
    require(metadata["image"] == f"overview.webp?v={version}", "Overview cache version differs")
    expected, total, content_hashes = set(), 0, []
    aspect = MAP_BOUNDS[map_id][0] // MAP_BOUNDS[map_id][1]
    for level in levels:
        columns, rows = aspect * 2 ** level["level"], 2 ** level["level"]
        require((level["width"], level["height"], level["columns"], level["rows"]) ==
                (columns * 512, rows * 512, columns, rows), "Pyramid dimensions differ")
        require(level["urlTemplate"] == f"{{z}}/{{x}}/{{y}}.webp?v={version}", "Tile cache version differs")
        for x in range(columns):
            for y in range(rows):
                path = dataset / str(level["level"]) / str(x) / f"{y}.webp"
                expected.add(path)
                with Image.open(path) as image:
                    require(image.format == "WEBP" and image.size == (516, 516), f"Invalid tile: {path}")
                    image.load()
                total += path.stat().st_size
                content_hashes.append((path.relative_to(dataset).as_posix(), file_sha256(path)))
    require(set(dataset.glob("*/*/*.webp")) == expected, "Unexpected/missing tile paths")
    with Image.open(dataset / "overview.webp") as image:
        require(image.format == "WEBP" and image.size == (levels[0]["width"], levels[0]["height"]), "Overview dimensions/format differ")
        image.load()
    overview_sha256 = file_sha256(dataset / "overview.webp")
    content_hashes.append(("overview.webp", overview_sha256))
    renders, receipt_files = latest_chunk_receipts(dataset, maximum)
    audit_chunks(renders, source, dataset, levels[-1], samples)
    final_stat = blend.stat()
    require((final_stat.st_size, final_stat.st_mtime_ns) == (stat.st_size, stat.st_mtime_ns),
            "Source scene changed during audit")
    return {"map": map_id, "dataset": str(dataset), "completeLevels": maximum + 1,
            "tiles": len(expected), "tileDimensions": [516, 516], "bytes": total,
            "renderProfile": profile, "cpuDenoise": source["cpuDenoise"], "cacheVersion": version,
            "currentReceiptChunks": len(renders), "receiptFiles": receipt_files,
            "actualScene": renders[0]["actualScene"],
            "sourceIntegrity": {"path": str(blend.resolve()), "bytes": stat.st_size,
                                "mtimeNs": stat.st_mtime_ns, "sha256": source_sha256,
                                "expectedSha256": expected_source_sha256,
                                "hashRecordedInRenderSource": bool(source.get("blendSha256")),
                                "scope": "Current file hash; historical render input binding exists only where independently recorded."},
            "finalContentIntegrity": {"algorithm": "SHA-256", "files": len(content_hashes),
                                      "overviewSha256": overview_sha256,
                                      "aggregateSha256": aggregate_content_sha256(content_hashes),
                                      "aggregateEncoding": "Sort relative POSIX paths; concatenate UTF-8 path, NUL, ASCII content SHA-256, LF."},
            "uniformWaterReusedChunks": sum(bool(r.get("uniformWaterReused")) for r in renders),
            "renderSecondsSelectedReceipts": sum(r["seconds"] for r in renders),
            "visualQualityAcceptedByAudit": False}, metadata


def promote_variants(path, map_id, dataset, metadata):
    original = path.read_bytes()
    before = json.loads(original)
    after = copy.deepcopy(before)
    entries = after["maps"][map_id]
    selected = [entry for entry in entries if entry.get("id") == "satellite"]
    require(len(selected) == 1, "Expected exactly one existing satellite descriptor")
    entry = selected[0]
    prefix = "/maps/tiles/satellite-static/" + dataset.name + "/"
    entry["image"] = prefix + metadata["image"]
    entry["tiles"] = copy.deepcopy(metadata["tiles"])
    for level in entry["tiles"]["levels"]:
        level["urlTemplate"] = prefix + level["urlTemplate"]
    finest = metadata["tiles"]["levels"][-1]
    entry["widthPixels"], entry["heightPixels"] = finest["width"], finest["height"]
    atomic_write(path, after, original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--map-id", required=True, choices=MAP_BOUNDS)
    parser.add_argument("--max-level", type=int, default=6)
    parser.add_argument("--samples", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variants", type=Path)
    parser.add_argument("--expected-source-sha256", help="Compare current blend contents against an independently captured hash")
    args = parser.parse_args()
    report, metadata = audit_dataset(args.dataset, args.map_id, args.max_level, args.samples,
                                     args.expected_source_sha256)
    atomic_write(args.output, report)
    if args.variants:
        promote_variants(args.variants, args.map_id, args.dataset, metadata)
        report["descriptorUpdated"] = True
        atomic_write(args.output, report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
