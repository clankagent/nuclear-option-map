# Offline extraction

Install the pinned extraction environment with `uv sync`. Supply a local, unpacked client Unity Data directory; keep it under ignored `work/`. Originals remain untouched.

```powershell
uv run python tools/inspect_assets.py work/game-data --report work/extraction/inventory.json
uv run python tools/extract_map.py work/game-data --geometry
uv run python -m unittest discover -s tests/extraction
```

The extractor identifies `MapSettings` through its serialized MonoScript reference, resolves its `MapImage` Sprite, and uses UnityPy's Sprite image export to preserve atlas cropping, rotation and alpha. Consumer map names follow `MapSettingsManager.Maps` prefab/detail references. Dimensions and grid offsets come from the same serialized settings. Coordinates are centered world X/Z in meters; the extraction does not shift them. Grid columns/rows count 10 km cells, independently of map image dimensions. The browser decides how to display the image's pixel orientation.

The manifest is written to ignored `demo/public/maps/manifest.json`. Detailed serialized references and provenance stay in the private report under ignored `work/extraction/`. A successful metadata test does not establish game behavior. Missing type trees, unresolved dependencies and unsupported objects appear in that report; no map is fabricated.

`--geometry` begins an explicitly partial geometry export: it walks each MapSettings prefab's GameObject/Transform descendants, exports referenced MeshFilter meshes as local-space OBJ, and records the exact transform graph in `geometry/scene.json`. It does not claim a complete map scene, a baked whole-map heightfield, or material fidelity. Scene-loaded terrain outside those descendants requires further reference-based investigation.

Meshes use stable asset-file/path-ID identities and are shared between instances and maps. Transform records resolve their parent identities across asset files. UnityPy OBJ exports reflect local X; the provided Blender entry point compensates this and composes the full parent chain, using meters and Blender Z-up. Run it with Blender 4+:

```text
blender --background --python tools/import_blender.py -- demo/public/maps/geometry/scene.json work/extraction/heartland.blend heartland-46396
```

This importer constructs geometry without game shaders or UV/material fidelity. Blender execution must be verified separately on a Blender-equipped machine; Python extraction tests do not verify its rendering.

For a private overhead scene with serialized materials and UVs, run `tools/render_material_prototype.py` in Blender 4+ with `geometry/scene.json`, the matching `work/extraction/material-report.json`, an ignored output folder, and a manifest map ID. It saves a packed `.blend` plus exact-bounds overview and a 3.84 km close-up when an `airbase_city` object exists. It preserves OBJ UVs and resolves complete transform hierarchies into world placements, excludes LOD1 child meshes when a primary ancestor mesh is present, ignores alpha from opaque URP textures, and omits `Shader Graphs/surf` because its `_Foam` input is an animated foam mask rather than a static base color. Unity DXT5nm normals use alpha for tangent X and green for Y; Blender reconstructs positive Z before normal mapping (`tests/extraction/test_dxt5nm.py` verifies bounded decoding). Terrain combines the referenced macro imagery with full-color sand, grass, rock, field and lush textures, red-channel masks and layered normals. A rough lit surface receives sunlight and casts terrain shadows. Source color-space flags, UV frequencies and alpha blends are retained. Small procedural UV breakup and dynamic clouds are omitted, so the offline scene remains an approximation of the complete game shader. All proprietary source textures and resulting scenes stay in ignored `work/`.

`tools/prepare_satellite_batch.py` can prepare a compressed, packed camera animation for an offline renderer without rendering or uploading anything:

```text
blender --background work/heartland.blend --python tools/prepare_satellite_batch.py -- heartland-46396 81920 81920 work/heartland-batch.blend
```

Each frame covers two-by-two 512-pixel tiles at 5 meters per pixel, with 2-pixel gutters and 110 pixels of cleanup context on each side. The saved frame is 1248×1248 pixels; crop the context before splitting tiles. The companion `.batch.json` records frame positions and verified city, airport, mountain and coast preview cameras. A 736×736 preview of those same camera extents is 8.48 meters per pixel; overriding the render dimensions changes the pixel scale. Camera animation uses constant keys and the same north-up compositor as the local tile baker. Heartland has 256 frames and Ignus has 512; split these according to the chosen renderer's frame limit. Start from the original scene and use a distinct output path. The saved scene retains CPU rendering and two-thread settings; an external renderer controls its own resource policy.

Create the material report from the same client data and extraction provenance before building a scene:

```powershell
uv run python tools/inspect_map_materials.py work/game-data --map-report work/extraction/provenance.json --report work/extraction/material-report.json
```

Build the packed Blender scene from that report and the geometry scene:

```text
blender --background --python tools/render_material_prototype.py -- demo/public/maps/geometry/scene.json work/extraction/material-report.json work/material-prototype heartland-46396
```

Verify the normal decoder through Blender's actual node graph:

```text
blender --background --factory-startup --python-exit-code 1 --python tools/test_dxt5nm_blender.py -- work/normal-fixture.png
```

The fixture renders flat and tilted normals through the same node builder used by the scene importer, then asserts the resulting pixels. Ordinary Python decoding tests do not establish correct Blender node wiring.

## Elevation and terrain scenes

The elevation pipeline selects the Heartland base tile and Ignus base island families. Separate road, decal, city-block and scenery meshes are excluded from this terrain-only scene. It composes every parent transform before rasterizing the surface:

```text
blender --background --python tools/render_terrain.py -- --scene demo/public/maps/geometry/scene.json --manifest demo/public/maps/manifest.json --map heartland-46396 --output work/terrain/heartland --resolution 1024 --glb
```

Run maps sequentially. Outputs include a terrain `.blend`, optional `.glb`, topographic PNG with contours in meters, float32 elevation EXR, a NumPy height array in meters, and a separate coverage array. Negative elevations are retained only where source geometry exists; missing coverage stays unknown (NaN height, false coverage). The arrays and PNG use north-to-south rows and west-to-east columns over the centered manifest bounds. The EXR retains the camera's south-up orientation; its red channel plus the recorded offset gives elevation. `terrain.json` records exact bounds, chosen objects, coverage and independent pixel-center ray checks. Increasing `--resolution` sets the horizontal pixel count and preserves the map's aspect ratio.

The PNG and shader are elevation diagnostics with a shaded height palette. They are not the library's topographic or satellite presentation. The terrain-only GLB retains geometric elevation; its custom Blender shader may not translate to glTF materials.

For exact pixel-center surface rasterization without Blender, run the pinned standalone Python script through `uv run --script`:

```powershell
uv run --script tools/raster_terrain.py --scene demo/public/maps/geometry/scene.json --manifest demo/public/maps/manifest.json --map heartland-46396 --output work/terrain/heartland-highres --resolution 8192
uv run --script tools/test_terrain_raster.py
```

The CPU rasterizer interpolates the highest actual transformed triangle intersecting each pixel center. It exports float32 meter heights and independent coverage; cartographic coloring uses blue below world elevation zero, green/brown above zero, hillshade and 100 m contours by default. Unknown pixels remain transparent. The CLI bounds raster allocations and validates map bounds and contour intervals.

## Tiled map delivery

Split built-in sprites into spatial raster pyramids without enlarging them:

```powershell
uv run python tools/tile_image.py demo/public/maps/heartland-46396.png demo/public/maps/tiles/builtin/heartland --base-width 512 --base-height 512
uv run python tools/tile_image.py demo/public/maps/ignus-archipelago-46395.png demo/public/maps/tiles/builtin/ignus --base-width 1024 --base-height 512
```

Each output includes a small overview, lossless WebP tiles with two-pixel edge gutters, and `pyramid.json`. Resolve its `image` and tile URL templates relative to that output folder before adding them to the map catalog. The built-in sources' native resolution remains the detail limit; smoothing does not recover terrain detail.

`build_terrain_tiles.py` generates a separate contour-cartography dataset. It uses a cached spatial triangle index, rasterizes bounded 512-pixel regions, separates ground elevations from roof footprints, and exports contour vectors directly from the transformed ground triangles. Its raster backdrop uses neutral ground, light-blue water, roads and gray buildings. Ground cuts require an independently verified land/water mask for visual fill; missing elevation must remain unknown rather than be interpolated into contour geometry. Check coastlines, platforms, airfields and cities before using a newly generated backdrop.

```powershell
uv run --script tools/build_terrain_tiles.py --scene demo/public/maps/geometry/scene.json --manifest demo/public/maps/manifest.json --map heartland-46396 --cache work/terrain/tiles-heartland --output demo/public/maps/terrain/heartland-46396 --max-level 6 --intervals 10,20,50,100
```

Add `--land-mask work/extraction/composition-textures/heartland-46396-oceandepthmap.png` for a locally exported, visually verified OceanDepthmap. Its conservative bright-land threshold fills the neutral visual backdrop beneath missing terrain/platform coverage; it never supplies elevations. Add `--material-report work/extraction/material-report.json` to project actual asphalt, runway and road material submesh triangles as neutral pavement. The report must correspond to the same scene/assets. Ignus uses its corresponding map ID and mask, with `--max-level 5`. A single `--preview-tile 6/42/16` produces a private labeled close-up without declaring that fine level complete. `--reuse-contours` is appropriate only when the source geometry and contour settings are unchanged.

Tiles are generated coarse first in bounded regions; no complete 32768-pixel raster is allocated. Contours use native ground triangles, exclude roof heights and preserve clipped tile endpoints. Pavement uses measured ground geometry, while markings and texture-only details remain separate recovery work. Negative elevations are retained only where source geometry exists; missing coverage stays unknown. Raster backdrops and contour vectors remain independent, allowing contour intervals and screen-space line/label styles to change without regenerating the backdrop.

The output `tiles.json` supplies `image`, `tiles` and `contours` for a map definition or demo variant. Add a variant's `id`, `label`, `kind: "terrain"` and finest `widthPixels`/`heightPixels` in `variants.json`. The browser draws minor/index contours and elevation labels independently of raster resolution and offers only generated contour intervals. Use a verified complete backdrop before adding the view to the demo; geometry-export success alone does not establish visual completeness.

### Terrain material preparation

Optional ground tint and northwest relief use a separate lossless height pyramid:

```powershell
uv run --script tools/build_topography_tiles.py --cache work/terrain/tiles-heartland --manifest demo/public/maps/manifest.json --output demo/public/maps/terrain/heartland-46396/topography --levels 5
uv run --script tools/test_topography_tiles.py
```

Use the matching native-geometry cache for each map. This CPU-only producer samples ground classes, excludes roofs, and keeps water/unknown transparent. It encodes measured height in RG, northwest illumination in B, and a binary coverage mask in A. Each tile is generated independently; five levels are bounded to an 8192-pixel maximum axis. Add `descriptor.json` as the map's `topography` field, resolving its templates relative to the containing terrain directory. The browser selects the fill/relief and strength without changing measured geometry or contour coordinates.

The satellite importer uses original referenced terrain textures directly. It combines native full-color detail and normals with the macro geography; interpolating a larger macro image alone cannot recover this detail. To update terrain in an existing packed material scene without importing geometry again:

```text
blender --background existing-scene.blend --threads 2 --python tools/relight_terrain_scene.py -- work/extraction/material-report.json work/source-lit-scene.blend
```

This prepares and packs a CPU scene without rendering. Keep the material report, source assets and generated scenes local. The older `upscale_terrain_macro.py` tool is an optional deterministic interpolation experiment; source-lit scenes do not require its derivatives.

### Satellite scene tiles

Render a material-preserving scene into small WebP tiles **before browsing**. Static files avoid waiting for scene renders while panning or zooming. Supply your Blender executable with `--blender` when it is outside the default location:

```powershell
uv run --script tools/render_satellite_tiles.py --blend work/extraction/heartland-scene.blend --output demo/public/maps/tiles/satellite/heartland --world-width 81920 --world-depth 81920 --max-level 5 --tile-budget 1365 --chunk-tiles 4 --derive-parents --device cpu --threads 2 --samples 16 --cpu-denoise --blender PATH/TO/blender
```

This renders bounded 2048 px chunks, slices them into 512 px tiles with edge gutters, and releases each intermediate image before continuing. `--derive-parents` renders the finest level once and derives lower levels with bounded neighboring-tile resampling. Only complete levels appear in `pyramid.json`. Heartland level 6 is 2.5 m/px and includes 5461 tiles; Ignus at the same level uses 10922 tiles over its 163840 by 81920 m world. Level 5 provides a smaller 5 m/px dataset (1365 and 2730 tiles respectively). Always use a fresh output folder after changing scene materials or render settings.

Rendering defaults to Cycles CPU with two threads, 16 samples and reduced process priority. The example applies explicit CPU Open Image Denoise after rendering, before cropping and splitting tiles. Its runtime-reported overlap and alignment provide context around every chunk; denoised eight-tile chunks exceed the output bound and are rejected. The CPU filter uses two threads and a soft 256 MiB memory request, with explicitly bounded input/output buffers. CPU compositing is enforced and GPU denoising is disabled. OS locks permit one active renderer and retain exclusion if its parent process exits. The reserved GPU CLI mode fails closed because texture limits alone cannot establish a verified total scene memory/time bound. Engine, device, sample count and thread settings are recorded in provenance and actual scene settings appear in render receipts; incompatible caches cannot be mixed.

Add `--reuse-uniform-water` in a fresh output folder to reuse a genuine rendered water chunk only after inspecting actual scene data: one unmodified, convex, horizontal quad covering the full chunk and gutters, an opaque constant material, uniform world lighting, directional lights and no potential shadows within conservative reach. Overrides, holdouts, volumes, texture/procedural shader inputs, local lights and uncertain geometry prevent reuse. Cache signatures bind the exact surface, lighting, resolution and render settings. Land and coastal chunks render normally; eligibility and reuse are recorded in receipts.

The optional development cache can render finer regions as requested:

```powershell
uv run --script tools/render_satellite_tiles.py --blend work/extraction/heartland-scene.blend --output demo/public/maps/tiles/satellite/heartland --world-width 81920 --world-depth 81920 --max-level 6 --serve 47833 --blender PATH/TO/blender
```

The service binds to loopback and caches requested renders. Cold requests incur Blender render time, so this mode is for development rather than instant map browsing. Serving the complete static pyramid requires no running Blender. Fine render sampling does not recover geographic details missing from the macro texture; the native terrain Shader Graph is not fully reconstructed.

Resolve descriptor image and tile templates against the directory serving the completed pyramid. Asset URLs include a source/settings version to prevent stale tiles after scene changes. The viewer keeps its overview and decoded parents visible during refinement. The demo serves static files directly; it does not proxy browser navigation to Blender. In development-cache mode, a request canceled before rendering leaves the queue, while an active render completes and is cached. Use a fresh output directory after changing the scene or render settings.

Queue ownership and disconnect checks run without Blender or game files:

```powershell
uv run --with Pillow python tests/test_satellite_tile_queue.py
uv run python tests/test_render_safety.py
uv run python tests/test_uniform_water.py
```

### Precomputed regional satellite detail

Generate a lossless close-up from an exported scene before choosing the detailed map's pixel spacing:

```text
blender --background SCENE.blend --threads 2 --python-exit-code 1 --python tools/render_quality_probe.py -- --x 12670 --z 7600 --world-width 6700 --world-depth 3500 --width 4096 --samples 128 --output PROBE
```

The comparison keeps source materials and lighting, uses north-up square pixels, and writes `raw.png` plus camera/render evidence without saving the scene. It uses CPU rendering with two threads, no denoising, a maximum 4096-pixel image edge and an exclusive render slot. The explicit comparison permits up to 256 samples; ordinary satellite baking retains its existing sample limit. Compare matched world regions at native image pixels before applying a denoiser or choosing the delivery resolution.

`prepare_quality_patch.py` packages a completed north-up raw quality probe into the next raster level without rendering or modifying the existing dataset:

```text
uv run --script tools/prepare_quality_patch.py --source INPUT/raw.png --proof INPUT/proof.json --manifest BASE/pyramid.json --manifest-url https://example.org/maps/base/pyramid.json --world-width 81920 --world-depth 81920 --output CANDIDATE
```

Use a fresh output directory and the actual URL of the base manifest. Existing overview and tile references resolve against that URL, retaining their version parameters. The candidate contains lossless WebP tiles and a manifest with regional `coverage`; only tiles whose interiors, gutters and Lanczos filter support fit inside the source are advertised. Source pixel spacing must be at least as fine as the candidate level. The proof must describe a completed raw probe, its image dimensions and north-up orientation. Optional camera rotation, location, scale and pixel-aspect evidence must agree with that footprint; optional source byte count and SHA-256 must match the supplied image. Rounded orthographic image height determines the actual vertical footprint. Private `provenance.json` records mixed base/region sampling. Serve the candidate as static files and inspect it before selecting it for a viewer; packaging does not promote imagery.

### Smaller 3D derivatives

Create a smaller 3D derivative from those heights with Blender:

```text
blender --background --python tools/export_height_mesh.py -- work/terrain/heartland-highres/height-meters.npy work/terrain/heartland-highres/terrain.json work/terrain/heartland-preview --columns 512
```

This produces a GLB, a little-endian float32 height grid and JSON with exact selected source row/column indices. glTF coordinates are X=world X, Y=elevation, Z=world Z in meters. The derivative retains missing sampled points; smaller holes or features can be bridged or lost. Caves and overhangs are lost because a heightfield retains the highest surface. Full imported terrain meshes remain available separately for greater fidelity.

The tool loads root serialized asset/scene files and their resource streams. For stripped MonoBehaviour trees it generates the schemas from the supplied `Managed/` assemblies, using pinned TypeTreeGeneratorAPI 0.0.10. Arbitrary StreamingAssets bundles require separate investigation rather than guessing a Unity version.

UnityPy API reference: [official README](https://github.com/K0lb3/UnityPy/blob/master/README.md). UnityPy 1.23.0 is pinned in the Python project and lockfile.

Contour-only updates can use the same command with `--contours-only`. This reads existing raster levels from `tiles.json` and rewrites only their contour vectors and contour metadata. The 10/20/50/100 m intervals all intersect the original transformed native ground triangles; missing ground creates no synthetic contour geometry. Export simplification is bounded to 0.5 pixel at overview levels 0-2 and 0.1 pixel at close levels 3 and above. Display smoothing rounds corners locally while retaining the measured vectors and contour elevations; its radius is a cartographic display parameter, not a reconstructed terrain height. Set smoothing to zero to inspect exported contours without further corner rounding. Tile endpoints are fixed to maintain joins.

The contour builder intersects the greatest-common-divisor interval once per tile, then selects the corresponding measured elevation paths for each requested interval and computes its index flags. `--reuse-base-contours` resumes an interrupted compatible build by validating and reusing existing base-interval JSON files; use it only with the same geometry cache and export settings. Missing or incomplete JSON is regenerated, and writes replace completed files atomically.
