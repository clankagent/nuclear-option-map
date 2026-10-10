"""Read-only GPU patch planning from CPU crop manifests; never loads Blender.

uv run tools/report_gpu_patch_eligibility.py work/gpu-cropped/*/manifest.json
Use --output PATH for a fresh private JSON report. This report does not grant
render permission: hash-bound loaded-scene inspection remains mandatory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from render_safety import (GPU_TRIAL_MAX_EDGE, GPU_TRIAL_MAX_TRIANGLES,
                           GPU_TRIAL_TEXTURE_BUDGET)


def report_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    failures, unknowns = [], []
    textures = []
    samples = {}
    for material in manifest.get("materials", []):
        for node in material.get("nodes", []):
            if node.get("type") == "TEX_IMAGE" and node.get("image"):
                samples.setdefault(node["image"], []).append({
                    "material": material["name"], "node": node["name"]})
    for image in manifest.get("images", []):
        w, h = image["width"], image["height"]
        if image["name"] == "Render Result" and w == h == 0 and image["name"] not in samples:
            continue  # Empty Blender render datablock is not an input texture.
        if (not isinstance(w, int) or isinstance(w, bool) or
                not isinstance(h, int) or isinstance(h, bool) or min(w, h) < 1):
            raise ValueError("Invalid image dimensions")
        size = (w * h * 64 + 2) // 3
        textures.append({"name": image["name"], "width": w, "height": h,
                         "conservativeBytes": size,
                         "exceedsEdge": max(w, h) > 1024,
                         "samplers": samples.get(image["name"], [])})
        if max(w, h) > 1024:
            failures.append(f"Texture {image['name']}: {w}x{h} exceeds 1024px")
    total = sum(t["conservativeBytes"] for t in textures)
    if "images" not in manifest:
        unknowns.append("No decoded image inventory")
    if total > GPU_TRIAL_TEXTURE_BUDGET:
        failures.append(f"Decoded texture bytes {total} exceed {GPU_TRIAL_TEXTURE_BUDGET}")
    meshes = manifest.get("meshes")
    geometry = {key: sum(m[key] for m in meshes) if meshes is not None else None
                for key in ("triangles", "vertices", "loops")}
    for key, limit, minimum in (("triangles", GPU_TRIAL_MAX_TRIANGLES, 1),
                                ("vertices", 150_000, 0), ("loops", 150_000, 0)):
        value = geometry[key]
        if value is None:
            unknowns.append(f"No {key} inventory")
        elif not minimum <= value <= limit:
            failures.append(f"{key}: {value} outside {minimum}..{limit}")
    edge = manifest.get("edge")
    if edge is None:
        unknowns.append("No output edge")
    elif not isinstance(edge, int) or isinstance(edge, bool) or not 1 <= edge <= GPU_TRIAL_MAX_EDGE:
        failures.append(f"Output edge {edge} exceeds 256px or is invalid")
    if manifest.get("imageReductions"):
        failures.append("Lossy texture reductions present; not exact-source production imagery")
    if not manifest.get("outputSha256"):
        unknowns.append("No packed candidate scene hash")
    if manifest.get("gpuInitialized") is not False:
        unknowns.append("CPU-only preparation not recorded")
    if not manifest.get("cropBoundsBlenderXY"):
        unknowns.append("No physical crop bounds")
    exact_crop_records = manifest.get("imageCrops", [])
    retained = manifest.get("retainedImageSamples", [])
    return {
        "manifest": str(path.resolve()), "sceneSha256": manifest.get("outputSha256"),
        "status": "cpu-required" if failures else
                  "incomplete-manifest" if unknowns else
                  "exact-source-candidate-requires-loaded-inspection",
        "failures": failures, "unknowns": unknowns, "geometry": geometry,
        "conservativeTextureBytes": total,
        "textureBudgetRatio": total / GPU_TRIAL_TEXTURE_BUDGET,
        "largestTextures": sorted(textures, key=lambda t: t["conservativeBytes"], reverse=True),
        "exactPhysicalImageCrops": len(exact_crop_records),
        "retainedSamplerReasons": retained,
        "nextActions": [
            "Prune dead shader branches only after proving their source material weights are identically zero throughout the retained domain.",
            "Crop affine macro/atlas samples at source texel resolution with filtering gutters and per-sampler coordinate remapping.",
            "Split periodic UV domains at wrap boundaries and retain exact wrapped texels; full-period coverage cannot be reduced by cropping.",
            "Preserve nonlinear/repeating graphs when exact crop equivalence is unproved; render these patches on CPU if caps fail.",
            "Prefer existing proven uniform-water chunk reuse over a new GPU render for eligible water.",
        ],
        "mandatoryLoadedInspection": [
            "Verify scene hash and actual live images, bounds, attributes<=32MiB, shader nodes<=1024 and unsupported dynamic features.",
            "Validate Combined-only output, selected single CUDA device, bounded queue states, exclusive slot and finite watchdog.",
            "Use four samples, CPU denoise with shared aligned neighboring context, and compare patch boundaries against full-scene CPU references.",
        ],
        "gpuInitialized": False, "renderAuthorizedByReport": False,
        "hardDeviceMemoryCap": False, "hardGpuDeadline": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    paths = []
    for path in args.manifests:
        # Windows shells do not expand wildcard arguments.
        if "*" in str(path) or "?" in str(path):
            import glob
            matched = [Path(p) for p in glob.glob(str(path))]
            if not matched:
                parser.error(f"No manifests match {path}")
            paths.extend(matched)
        else:
            paths.append(path)
    report = {"schemaVersion": 1, "policy": {
        "textureBytes": GPU_TRIAL_TEXTURE_BUDGET, "textureEdge": 1024,
        "outputEdge": GPU_TRIAL_MAX_EDGE, "triangles": GPU_TRIAL_MAX_TRIANGLES,
        "samples": 4, "estimate": "float32 RGBA plus full mip chain; input estimate, not driver allocation"},
        "patches": [report_manifest(p) for p in sorted(set(paths))]}
    text = json.dumps(report, indent=2)
    if args.output:
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
