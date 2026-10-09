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

For a private overhead scene with serialized materials and UVs, run `tools/render_material_prototype.py` in Blender 4+ with `geometry/scene.json`, the matching `work/extraction/material-report.json`, an ignored output folder, and a manifest map ID. It saves a packed `.blend` plus exact-bounds overview and a 3.84 km close-up when an `airbase_city` object exists. It preserves OBJ UVs and resolves complete transform hierarchies into world placements, excludes LOD1 child meshes when a primary ancestor mesh is present, ignores alpha from opaque URP textures, and omits `Shader Graphs/surf` because its `_Foam` input is an animated foam mask rather than a static base color. Unity DXT5nm normals use alpha for tangent X and green for Y; Blender reconstructs positive Z before normal mapping (`tests/extraction/test_dxt5nm.py` verifies bounded decoding). Terrain uses the actual map macro base color as a baked image; the compiled Shader Graph's splat/detail node blend is unavailable, so the scene does not claim native shader fidelity. All proprietary source textures and resulting scenes stay in ignored `work/`.

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
uv run --script tools/build_terrain_tiles.py --scene demo/public/maps/geometry/scene.json --manifest demo/public/maps/manifest.json --map heartland-46396 --cache work/terrain/tiles-heartland --output demo/public/maps/terrain/heartland-46396 --max-level 6 --intervals 20,50,100
```

Add `--land-mask work/extraction/composition-textures/heartland-46396-oceandepthmap.png` for a locally exported, visually verified OceanDepthmap. Its conservative bright-land threshold fills the neutral visual backdrop beneath missing terrain/platform coverage; it never supplies elevations. Add `--material-report work/extraction/material-report.json` to project actual asphalt, runway and road material submesh triangles as neutral pavement. The report must correspond to the same scene/assets. Ignus uses its corresponding map ID and mask, with `--max-level 5`. A single `--preview-tile 6/42/16` produces a private labeled close-up without declaring that fine level complete. `--reuse-contours` is appropriate only when the source geometry and contour settings are unchanged.

Tiles are generated coarse first in bounded regions; no complete 32768-pixel raster is allocated. Contours use native ground triangles, exclude roof heights and preserve clipped tile endpoints. Pavement uses measured ground geometry, while markings and texture-only details remain separate recovery work. Negative elevations are retained only where source geometry exists; missing coverage stays unknown. Raster backdrops and contour vectors remain independent, allowing contour intervals and screen-space line/label styles to change without regenerating the backdrop.

The output `tiles.json` supplies `image`, `tiles` and `contours` for a map definition or demo variant. Add a variant's `id`, `label`, `kind: "terrain"` and finest `widthPixels`/`heightPixels` in `variants.json`. The browser draws minor/index contours and elevation labels independently of raster resolution and offers only generated contour intervals. Use a verified complete backdrop before adding the view to the demo; geometry-export success alone does not establish visual completeness.

### Satellite scene tiles

Render a material-preserving scene into small WebP tiles. Supply your Blender executable with `--blender` when it is outside the default location:

```powershell
uv run --script tools/render_satellite_tiles.py --blend work/extraction/heartland-scene.blend --output demo/public/maps/tiles/satellite/heartland --world-width 81920 --world-depth 81920 --max-level 3 --tile-budget 85 --blender PATH/TO/blender
```

This publishes only complete levels in `pyramid.json`. Coarse levels provide immediate coverage. To render finer regions as they are requested, run the persistent local cache against the same scene and output:

```powershell
uv run --script tools/render_satellite_tiles.py --blend work/extraction/heartland-scene.blend --output demo/public/maps/tiles/satellite/heartland --world-width 81920 --world-depth 81920 --max-level 6 --serve 47833 --blender PATH/TO/blender
```

The service binds to loopback, advertises lazy levels through `/pyramid.json`, and caches requested tiles. The demo proxies Heartland through `/satellite/heartland`; Ignus uses port 47834 and `/satellite/ignus`. Ignus bounds are 163840 by 81920 m and its coarse levels 0–3 contain 170 tiles. Lazy level 6 provides 2.5 m pixels on both maps without generating a whole fine-resolution image. This is the render sampling scale; the recovered terrain macro texture remains coarser, and its native detail blend is not reconstructed.

Resolve the service descriptor's image and tile templates against the corresponding proxy path when adding a demo variant. Asset URLs include a source/settings version to prevent stale tiles after scene changes. A request canceled before rendering leaves the queue; an active render completes and is cached. The viewer keeps its overview and decoded parents visible during refinement. For a static host, generate all advertised levels beforehand or supply an equivalent tile endpoint; the Vite development proxy is not part of a static build. Use a fresh output directory after changing the scene or render settings.

Queue ownership and disconnect checks run without Blender or game files:

```powershell
uv run python tests/test_satellite_tile_queue.py
```

### Smaller 3D derivatives

Create a smaller 3D derivative from those heights with Blender:

```text
blender --background --python tools/export_height_mesh.py -- work/terrain/heartland-highres/height-meters.npy work/terrain/heartland-highres/terrain.json work/terrain/heartland-preview --columns 512
```

This produces a GLB, a little-endian float32 height grid and JSON with exact selected source row/column indices. glTF coordinates are X=world X, Y=elevation, Z=world Z in meters. The derivative retains missing sampled points; smaller holes or features can be bridged or lost. Caves and overhangs are lost because a heightfield retains the highest surface. Full imported terrain meshes remain available separately for greater fidelity.

The tool loads root serialized asset/scene files and their resource streams. For stripped MonoBehaviour trees it generates the schemas from the supplied `Managed/` assemblies, using pinned TypeTreeGeneratorAPI 0.0.10. Arbitrary StreamingAssets bundles require separate investigation rather than guessing a Unity version.

UnityPy API reference: [official README](https://github.com/K0lb3/UnityPy/blob/master/README.md). UnityPy 1.23.0 is pinned in the Python project and lockfile.
