# /// script
# requires-python = ">=3.11"
# dependencies = ["Pillow>=11,<13"]
# ///
"""Package a completed north-up quality probe as precomputed sparse raster detail.

No render, base-tile copy, or live manifest change. Run with uv run --script.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urljoin, urlparse

from PIL import Image


def prepare(source, proof_path, manifest_path, manifest_url, output, world_width, world_depth):
    source, proof_path, manifest_path, output = map(Path, (source, proof_path, manifest_path, output))
    if output.exists():
        raise ValueError("Use a fresh candidate output directory")
    if urlparse(manifest_url).scheme not in ("http", "https", "file"):
        raise ValueError("Base manifest URL must be absolute")
    if not all(math.isfinite(v) and v > 0 for v in (world_width, world_depth)):
        raise ValueError("Map dimensions must be positive and finite")
    proof = json.loads(proof_path.read_text(encoding="utf-8-sig"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if proof.get("northUpFlip") != "Y" or proof.get("rawDenoised") is not False or not math.isfinite(proof.get("seconds", float("nan"))):
        raise ValueError("Require a completed raw north-up probe proof")
    if "outputBytes" in proof and proof["outputBytes"] != source.stat().st_size:
        raise ValueError("Source byte count differs from proof")
    if "outputSha256" in proof:
        with source.open("rb") as handle:
            source_hash = hashlib.file_digest(handle, "sha256").hexdigest()
        if proof["outputSha256"] != source_hash:
            raise ValueError("Source SHA-256 differs from proof")
    tiles = manifest["tiles"]
    size, gutter = tiles["tileSize"], tiles.get("gutter", 0)
    if not isinstance(size, int) or size < 64 or size > 2048 or not isinstance(gutter, int) or not 0 <= gutter <= 8:
        raise ValueError("Invalid tile size or gutter")
    levels = tiles["levels"]
    for index, level in enumerate(levels):
        if level["level"] != index or level.get("coverage") is not None or level["columns"] * size != level["width"] or level["rows"] * size != level["height"]:
            raise ValueError("Base must contain complete, aligned levels")
        if index and (level["width"] != levels[index-1]["width"]*2 or level["height"] != levels[index-1]["height"]*2):
            raise ValueError("Base levels must double")
    last = levels[-1]
    width, height = last["width"]*2, last["height"]*2
    mpp = world_width / width
    if not math.isclose(mpp, world_depth / height, rel_tol=1e-9):
        raise ValueError("Candidate requires square world pixels")
    with Image.open(source) as image:
        image.load()
        if [image.width, image.height] != [proof["width"], proof["height"]]:
            raise ValueError("Source dimensions differ from proof")
        cx, cz, footprint, nominal_depth = (proof[k] for k in ("centerX", "centerZ", "worldWidth", "worldDepth"))
        if not all(math.isfinite(v) for v in (cx, cz, footprint, nominal_depth)) or min(footprint, nominal_depth) <= 0:
            raise ValueError("Invalid probe footprint")
        if "cameraRotation" in proof:
            rotation = proof["cameraRotation"]
            if not isinstance(rotation, list) or len(rotation) != 3 or not all(isinstance(v, (int, float)) and math.isfinite(v) and abs(v) <= 1e-6 for v in rotation):
                raise ValueError("Probe camera rotation must look straight down")
        if "pixelAspect" in proof:
            aspect = proof["pixelAspect"]
            if not isinstance(aspect, list) or len(aspect) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) and math.isclose(v, 1, abs_tol=1e-6) for v in aspect):
                raise ValueError("Probe pixel aspect must be square")
        if "cameraLocation" in proof:
            location = proof["cameraLocation"]
            if not isinstance(location, list) or len(location) != 3 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in location) or not math.isclose(location[0], cx, abs_tol=1e-5) or not math.isclose(location[1], -cz, abs_tol=1e-5):
                raise ValueError("Probe camera location disagrees with footprint center")
        source_mpp = footprint / image.width
        # Blender orthographic square pixels preserve width, with rounded output height.
        depth = source_mpp * image.height
        if abs(depth - nominal_depth) > source_mpp * .51:
            raise ValueError("Probe footprint is inconsistent with square pixels")
        if source_mpp > mpp or not math.isclose(proof["metersPerPixel"], source_mpp, rel_tol=1e-6):
            raise ValueError("Source has insufficient native detail for the next level")
        if "cameraScale" in proof and not math.isclose(proof["cameraScale"], max(footprint, depth), rel_tol=1e-6):
            raise ValueError("Probe camera scale disagrees with footprint")
        left = cx-footprint/2 + world_width/2
        top = world_depth/2 - (cz+depth/2)
        # Keep Lanczos's complete source support as well as every delivered gutter.
        support = 3*mpp
        x0 = max(0, math.ceil((left + support + gutter*mpp)/(size*mpp)))
        y0 = max(0, math.ceil((top + support + gutter*mpp)/(size*mpp)))
        x1 = min(width//size, math.floor((left+footprint-support-gutter*mpp)/(size*mpp)))
        y1 = min(height//size, math.floor((top+depth-support-gutter*mpp)/(size*mpp)))
        if x1 <= x0 or y1 <= y0:
            raise ValueError("Source fully covers no finer tiles including filter support and gutters")
        digest = hashlib.sha256()
        for path in (source, proof_path, manifest_path):
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024*1024), b""):
                    digest.update(block)
        digest.update(json.dumps([world_width, world_depth, manifest_url]).encode())
        version = digest.hexdigest()[:20]
        candidate = copy.deepcopy(manifest)
        candidate["image"] = urljoin(manifest_url, manifest["image"])
        for level in candidate["tiles"]["levels"]:
            level["urlTemplate"] = urljoin(manifest_url, level["urlTemplate"])
        new_level = {"level": len(levels), "width": width, "height": height,
                     "columns": width//size, "rows": height//size,
                     "urlTemplate": f"{{z}}/{{x}}/{{y}}.webp?v={version}",
                     "coverage": [{"x": x0, "y": y0, "columns": x1-x0, "rows": y1-y0}]}
        candidate["tiles"]["levels"].append(new_level)
        output.mkdir(parents=True)
        rgb = image.convert("RGB")
        for x in range(x0, x1):
            directory = output / str(len(levels)) / str(x)
            directory.mkdir(parents=True, exist_ok=True)
            for y in range(y0, y1):
                box = ((x*size-gutter)*mpp-left, (y*size-gutter)*mpp-top,
                       ((x+1)*size+gutter)*mpp-left, ((y+1)*size+gutter)*mpp-top)
                box = tuple(v/source_mpp for v in box)
                patch = rgb.resize((size+2*gutter, size+2*gutter), Image.Resampling.LANCZOS, box=box)
                patch.save(directory / f"{y}.webp", lossless=True)
                patch.close()
        rgb.close()
    provenance = {"sourceImage": str(source.resolve()), "sourceProof": str(proof_path.resolve()),
                  "baseManifest": str(manifest_path.resolve()), "baseManifestUrl": manifest_url,
                  "version": version, "sourceMetersPerPixel": source_mpp,
                  "candidateMetersPerPixel": mpp, "baseMetersPerPixel": world_width/last["width"],
                  "sourceActualDepth": depth, "sourceNominalDepth": nominal_depth,
                  "coverage": new_level["coverage"], "sampling": "mixed: existing complete base plus precomputed quality region",
                  "resampling": "Lanczos with source filter support; lossless WebP",
                  "tileCount": (x1-x0)*(y1-y0), "rendered": False, "promoted": False}
    (output / "pyramid.json").write_text(json.dumps(candidate, indent=2), encoding="utf-8")
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return candidate, provenance


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "proof", "manifest", "output"):
        parser.add_argument("--"+name, required=True, type=Path)
    parser.add_argument("--manifest-url", required=True)
    parser.add_argument("--world-width", required=True, type=float)
    parser.add_argument("--world-depth", required=True, type=float)
    args = parser.parse_args()
    _, receipt = prepare(args.source, args.proof, args.manifest, args.manifest_url, args.output, args.world_width, args.world_depth)
    print(json.dumps(receipt))
