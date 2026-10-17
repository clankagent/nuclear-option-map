# Updating a static satellite region

A regional update has two distinct parts. Render the changed scene at the existing finest resolution, then rebuild the affected lower-resolution tiles on the CPU. Lower zoom levels do not need another Blender render.

Use a new candidate directory for every update. Keep the accepted baseline and its manifest available until the candidate passes review. The procedure below changes existing coverage; adding coverage is a separate operation.

## 1. Freeze the baseline and the scene change

Record the accepted dataset's descriptor and completion inventory, source scene and material profiles, renderer and denoiser versions, camera, lighting, sample settings and output resolution. Use the latest accepted dataset as the baseline so that earlier repairs remain included.

Identify the exact source objects and submeshes to change. Preserve each complete world transform, including parent rotation and scale. For repeated geometry, identify whole instances before replacing their component faces. A material can occur in both dedicated scenery and a mixed-material building; replacing the whole building is not a valid shortcut.

For mixed meshes, verify the exact retained non-target polygons, material assignments, UV layers, active UV layer and custom split normals after saving and reopening the prepared scene. Check vertex colors when the source has them. Compare retained faces as a multiset so duplicate faces remain accounted for. Measure and bound any normal-encoding round-trip difference instead of claiming bitwise identity. An in-memory comparison alone does not verify the saved input. If retaining a whole building as context preserves these attributes better than clipping it, explicitly count and bound its full geometry, verify camera and output coverage, and record that this context mesh remains unclipped.

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

Before dispatch, audit every job against its actual physical crop bounds. Check the exact old and new geometry, their shadows and all source objects assigned to the job. A conservative publication halo can select an owner that contains no changed geometry or shadow within its physical crop. Give such an owner an explicit no-op disposition and retain its baseline pixels; do not submit an empty replacement set. Reconcile the dispositions with the complete source inventory so no changed object or physical shadow influencer disappears from the plan. Qualify finite indirect-light context separately; a geometry/shadow audit does not prove that context is sufficient.

## 3. Render and verify the finest replacements

Overlap immutable CPU preparation with GPU rendering. Use one preparation worker and a bounded queue of prepared scenes so the next scene is ready before the GPU needs it. Give preparation its own lifetime lock; it must not hold the render or publication locks while reading frozen inputs and writing private scene packets. Keep one GPU call unresolved at a time. Publish and verify its outputs before admitting the next paid call. A dataset change or publication still requires the appropriate source and publication locks.

Measure preparation, GPU processing and transfers, encoding, durable commits, collection and verification separately. Reduce repeated setup and redundant commits while preserving a durable begin record before each processing group and a completion record afterward. Keep the GPU session warm only within finite windows and the configured idle limit. GPU utilization alone does not establish useful throughput or billable efficiency.

Use the same north-up camera alignment, pixel spacing, source-texture profile, lighting and finalization pipeline as the baseline. Preserve accepted corrections to unrelated scene objects. Keep scene preparation, raw render completion, denoising and final tile delivery as separate recorded stages.

Use bounded render jobs with durable submission records. Recover completed output before considering another render. An uncertain call is not an instruction to resubmit it. Render only the selected region; the pyramid tool does not submit render jobs.

Distinguish the client's submission-to-result deadline from the provider's function deadline. Allocation, startup or queue delays can consume the client wait before useful processing starts. Budget the client wait for the full function deadline plus a bounded startup/result allowance. Reserve additional parent-process time for archive downloads, local verification and controller drain; receiving the result does not mean its output has been downloaded.

Recheck the remaining controller budget after input/provider preflight and immediately before paid allocation. If it cannot cover the result wait, download and drain allowances, record that no call was made and stop. If a wait or download expires, inspect the original call and its committed output before declaring work failed or submitting anything again. Recover that output without replaying the paid work.

Keep terminal evidence for each call. Provider app lists can omit older stopped apps; absence from a recent list does not establish closure. Retain a verified terminal call result or a hash-bound stopped-app receipt before the provider's history expires.

Make collection resumable too. Save bounded chunks with hashes and exact job IDs, and commit completed chunks before processing more files. After a timeout, recover and verify those chunks, derive the remaining IDs from the frozen job set, and collect only those IDs. A collection failure does not require another render or a fresh pass over every completed result.

Bound concurrent reads, in-flight payloads and chunk sizes. Build each archive chunk locally, then copy it sequentially to remote storage with a bounded buffer; measure read, archive, copy and commit time separately. Stop admitting new work early enough to finish and commit the current chunk within the function deadline. Verify that recovered and newly collected job sets form the exact frozen set, with no duplicates or omissions, before staging.

For a persistent Blender preparation process, clear scene-dependent and image-pointer caches whenever a file is loaded. Blender can reuse an image pointer for a different image. Reuse content-hash caches only after checking the actual image header and both dimensions; equal pixel counts do not establish equal width and height. Bind prepared scenes and receipts to their input and helper hashes. If a cache defect is found, independently audit earlier outputs that used it before retaining them.

If a correction retires no-op jobs, write a new plan and disposition record. Prove that each retained job's payload is unchanged before carrying forward its completed preparation or render receipt. Record no-ops as baseline reuse, never as provider-completed renders. Stop new submissions, resolve or drain in-flight work and release the exclusive render lane before starting a corrected controller. Keep uncertain submissions unresolved until their actual status is known. The new controller must retain the remaining resource budget. Keep it responsive during paid calls so it can record completion and support cancellation. Hold the render lane for the full call.

Preflight the complete job plan and exact helpers before scaling. Then run a representative scene through preparation, rendering, tile delivery and the real CPU pyramid stage. When a worker reuses one process, also exercise different scenes and image dimensions in the same batch. Include split baseline roots when production uses them. Record preparation, render completion, verified tile delivery and completed zoom levels separately. Count actual published tiles from verified coordinates; do not assume a fixed number per job. Use monotonic elapsed time for resource deadlines, and check observer clocks before comparing timestamps from different machines.

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

The Python API uses the same string level keys: `base_roots={"10": Path("BASE-FINEST/10")}`. Nonstring keys are rejected. Each value points directly to the level directory containing `<x>/<y>.webp`, not its parent. Validate the actual split-root layout with `--plan-only` (or `prepare(..., plan_only=True)`) before the full stage; a synthetic single-root test does not check this integration.

Check the complete finishing input set on the machine that will run it, including source records used for verification and visual comparisons. Files uploaded to render storage are not automatically present on that machine's filesystem. Verify required paths and hashes, then freeze the inputs and measure the files to collect before allocating a collection worker. A successful render-input check alone does not establish that collection, staging and review inputs are ready.

Test path checks against the actual mounted storage. Resolve the trusted mount root once, then check the owned relative paths beneath it and reject unexpected links inside those paths. Walking every ancestor can incorrectly reject a provider-managed mount alias and block valid results. Keep exact file-size and hash checks after resolving paths.

Unchanged tiles use hardlinks by default, so the candidate must be on the same filesystem as the baseline. Use `--copy-base` when an independent byte copy is required and storage permits it. Changed tiles always use new files. Do not edit, resave, chmod or truncate reused hardlinks after staging.

Keep the CPU stage within its qualified memory bound. Report process RSS separately from cgroup memory, which can include file cache; reaching the cgroup limit does not by itself establish an out-of-memory failure. Check memory events and exit status before deciding whether the bound needs to change.

Required covered files must exist. The baseline must already be accepted and immutable; this command does not rehash every unchanged baseline file. Sparse absent tiles can contribute the descriptor's explicit opaque `fillRgba8`; a missing covered file is an error. Replacement images must match the existing tile dimensions and be opaque. Coverage remains unchanged.

The output contains a complete static file layout, `pyramid.json`, `overview.webp` and `patch-receipt.json`. The receipt distinguishes replacements, regenerated parents and reused files. The output directory must be absent before starting. A failed run has no successful completion receipt; preserve it for diagnosis and use a fresh directory for a corrected run.

If concurrent changes use the same baseline, compare their finest replacement coordinates before combining them. Disjoint coordinates can enter one replacement plan. An overlapping coordinate needs one tile containing both changes: render the overlap with both source changes, or use a validated composite that preserves occlusion and shadows. Copying one complete tile over the other loses a change. Record how each overlap was resolved, then rebuild the dependent zoom levels from the final union of finest replacements. Keep interim variants clearly labelled until that combined candidate passes review.

## 5. Review and publish the candidate

Check the patch receipt, exact replacement coordinates, rebuilt parent coordinates and refreshed overview. Reused baseline files must retain their original bytes. Changed files must have independent inodes: never write through a hardlink to a baseline tile.

Before building or transferring delivery archives, measure the encoded changed-file set, archive overhead, extracted disk use and decoded image memory. A per-chunk transfer limit is separate from the total delivery size. Split a larger delivery into preflighted bounded chunks, with one manifest binding every file's path, size and hash to its chunk and binding each chunk's hash. Verify the exact complete inventory across chunks before review or publication. Resume by transferring only missing verified chunks from the completed candidate; a delivery packaging or transfer failure does not require rendering or staging again.

Use a fresh versioned URL for the candidate. Inspect the changed area at the finest resolution, several lower zooms and the overview. Pan across the patch boundary. Compare the surrounding roofs, roads, terrain and previously accepted repairs at matched world scale. Check gutters, color, shadow continuity and tile loading.

A viewer can serve only the changed closure without copying baseline files. Keep each level's immutable baseline `urlTemplate` and add optional `urlOverrides`, for example `{"416/165":"patch-v2/10/416/165.webp"}`. Keys are canonical nonnegative `x/y` coordinates inside that level and its declared coverage; all raster levels together accept at most 100,000 overrides per pyramid. Override URLs resolve relative to the catalog, allow HTTP, HTTPS or file URLs, and select complete tile files with the same core/gutter dimensions as the baseline. Every other coordinate keeps its baseline URL. Include changed parents and use the refreshed overview as the candidate's `image`. Publish changed files at fresh versioned URLs before the candidate descriptor. Load the new descriptor through `setMap` to replace the viewer's tile cache; do not mutate an active descriptor in place.

Only then update a viewer variant or active descriptor through its normal publication process. Retain the previous descriptor for rollback. The patch tool's completion receipt confirms its CPU work; it does not grant scene, coverage, appearance or publication acceptance.

## Verification

The regression tests compare selective output with a clean full pyramid rebuild on synthetic data. They cover filter support across boundaries, map-edge clamping, partial tiles, sparse virtual fill, gutter-only edits and baseline preservation. These tests verify the raster update mechanism. They do not verify a game's scene extraction, a replacement model or the rendered appearance.

```bash
uv run --with Pillow --with numpy python tests/test_raster_patch.py
```

Keep source assets, dataset-specific receipts, provider details and local paths in private job records. The reusable command and this workflow belong with the project source.
