# Updating a static satellite region

A regional update has two distinct parts. Render the changed scene at the existing finest resolution, then rebuild the affected lower-resolution tiles on the CPU. Lower zoom levels do not need another Blender render.

Use a new candidate directory for every update. Keep the accepted baseline and its manifest available until the candidate passes review. The procedure below changes existing coverage; adding coverage is a separate operation.

## 1. Freeze the baseline and the scene change

Record the accepted dataset's descriptor and completion inventory, source scene and material profiles, renderer and denoiser versions, camera, lighting, sample settings and output resolution. Use the latest accepted dataset as the baseline so that earlier repairs remain included.

Identify the exact source objects and submeshes to change. Preserve each complete world transform, including parent rotation and scale. For repeated geometry, identify whole instances before replacing their component faces. A material can occur in both dedicated scenery and a mixed-material building; replacing the whole building is not a valid shortcut.

Record the new model and texture hashes. An isolated model preview establishes appearance at the inspected scale. A representative scene crop must establish placement, scale, lighting, shadows, material behavior and render cost before a large batch starts. Instance reuse in the source scene does not by itself establish that a cropper preserves instancing or keeps triangle counts bounded.

## 2. Select the render footprint

Find the union of the old and new geometry and their possible shadows on scene receivers. Include both footprints: removing an old shadow can change a tile that the new shadow does not reach. Use the actual light directions and receiver heights, not only the tree centers or crown bounds. Include the renderer's crop, shadow and denoising context, plus the delivery gutters.

Keep three coordinate systems separate:

| Unit | Purpose |
| --- | --- |
| Scene/render group | Physical input and render context; may contain several output owners |
| Output owner | Coherent set of finest tiles delivered by one render job |
| Raster tile | Browser file, with a core and filtering gutters |

Publish only the selected owners, even when a larger scene group is rendered for context. If the old or new scene influence reaches an area represented only by virtual fill, stop and qualify the coverage change. Do not silently treat unknown imagery as rendered water or expand a coverage mask.

## 3. Render and verify the finest replacements

Use the same north-up camera alignment, pixel spacing, source-texture profile, lighting and finalization pipeline as the baseline. Preserve accepted corrections to unrelated scene objects. Keep scene preparation, raw render completion, denoising and final tile delivery as separate recorded stages.

Use bounded render jobs with durable submission records. Recover completed output before considering another render. An uncertain call is not an instruction to resubmit it. Render only the selected region; the pyramid tool does not submit render jobs.

Verify each replacement's coordinate, dimensions, orientation, file hash and render receipt. Inspect a real scene crop at native pixel scale and inspect joins against unchanged neighbors. A complete file set does not establish visual acceptance.

## 4. Rebuild dependent zoom levels

For the current satellite pyramid, each parent pixel comes from a Lanczos reduction of child **core** pixels. Child gutters are not inputs. With tile core size `T`, gutter `g`, parent index `p` and actual parent core width `w`, the parent reads this half-open child-pixel interval before clamping to the map edge:

```text
left = (p*T - g)*2 - 6
right = left + (w + 2*g)*2 + 12
```

The same formula applies vertically. The six child pixels provide the filter support. Resize the support region by one half with Lanczos, then crop three parent pixels from each side. Preserve the original RGB conversion, sparse-fill policy and lossless encoding.

A changed core near a boundary can affect a neighboring parent, including a diagonal neighbor. Updating only `(x // 2, y // 2)` is insufficient. Intersect changed child-core bounds with these support rectangles and repeat toward level zero. A changed gutter must be staged, but a gutter-only change does not propagate upward. A regenerated parent whose core is unchanged also needs no further propagation.

Use `tools/prepare_raster_patch.py` for this CPU stage. It stages verified finest replacements, rebuilds dependent parents, refreshes the root overview and writes a new static descriptor and receipt. It retains the existing coverage masks and reads an explicit opaque fill only where the baseline declares virtual coverage. It does not render, modify the baseline, or promote the candidate.

### Command and inputs

Keep the baseline descriptor and its existing tile files local. By default, files are read from `BASE/<level>/<x>/<y>.webp`. The descriptor's URL templates are not used as filesystem paths, and the tool does not download tiles.

Write a replacement plan. Coordinates refer to the existing finest level; paths are relative to this JSON file. Each hash binds the actual completed replacement image, including its gutters.

```json
{
  "schemaVersion": 1,
  "tiles": [
    {"x": 12, "y": 8, "path": "rendered/12/8.webp", "sha256": "SHA256_OF_REPLACEMENT_FILE"}
  ]
}
```

Run the CPU stage in a fresh output directory:

```bash
uv run --script tools/prepare_raster_patch.py \
  --base-manifest BASE/pyramid.json \
  --base-root BASE \
  --replacements JOB/replacements.json \
  --output JOB/candidate-v1
```

Add `--plan-only` to the same command to validate inputs and print conservative parent counts without creating the candidate directory. This estimate uses the full replacement core bounds; the actual build stops propagation where decoded core pixels remain unchanged.

For baselines that store different levels in separate locations, add `--base-roots JOB/base-roots.json`. Each entry overrides the directory for one level; that directory contains `<x>/<y>.webp` directly. Relative paths resolve beside the roots JSON file. For example:

```json
{
  "10": "../../baseline-finest/10"
}
```

Unchanged tiles use hardlinks by default, so the candidate must be on the same filesystem as the baseline. Use `--copy-base` when an independent byte copy is required and storage permits it. Changed tiles always use new files. Do not edit, resave, chmod or truncate reused hardlinks after staging.

Required covered files must exist. The baseline must already be accepted and immutable; this command does not rehash every unchanged baseline file. Sparse absent tiles can contribute the descriptor's explicit opaque `fillRgba8`; a missing covered file is an error. Replacement images must match the existing tile dimensions and be opaque. Coverage remains unchanged.

The output contains a complete static file layout, `pyramid.json`, `overview.webp` and `patch-receipt.json`. The receipt distinguishes replacements, regenerated parents and reused files. The output directory must be absent before starting. A failed run has no successful completion receipt; preserve it for diagnosis and use a fresh directory for a corrected run.

## 5. Review and publish the candidate

Check the patch receipt, exact replacement coordinates, rebuilt parent coordinates and refreshed overview. Reused baseline files must retain their original bytes. Changed files must have independent inodes: never write through a hardlink to a baseline tile.

Use a fresh versioned URL for the candidate. Inspect the changed area at the finest resolution, several lower zooms and the overview. Pan across the patch boundary. Compare the surrounding roofs, roads, terrain and previously accepted repairs at matched world scale. Check gutters, color, shadow continuity and tile loading.

Only then update a viewer variant or active descriptor through its normal publication process. Retain the previous descriptor for rollback. The patch tool's completion receipt confirms its CPU work; it does not grant scene, coverage, appearance or publication acceptance.

## Verification

The regression tests compare selective output with a clean full pyramid rebuild on synthetic data. They cover filter support across boundaries, map-edge clamping, partial tiles, sparse virtual fill, gutter-only edits and baseline preservation. These tests verify the raster update mechanism. They do not verify a game's scene extraction, a replacement model or the rendered appearance.

```bash
uv run --with Pillow --with numpy python tests/test_raster_patch.py
```

Keep source assets, dataset-specific receipts, provider details and local paths in private job records. The reusable command and this workflow belong with the project source.
