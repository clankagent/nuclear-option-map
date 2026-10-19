# Nuclear Option Map

A portable map core and browser map viewer for Nuclear Option. Rust provides map-coordinate and viewport operations, compiled natively or to WebAssembly. A TypeScript browser adapter draws local map images, spatial raster tiles and vector contours with pan, zoom and grid overlays.

Game files and extracted assets are not included. The offline extraction tools read a local client data directory; the viewer uses the resulting map manifest and images. No game process is required for image-based maps.

The built-in viewer supports Heartland and Ignus Archipelago when extracted from a compatible client. It provides cursor-anchored wheel zoom, pointer pan, touch pinch, keyboard navigation, original sprite/tinted modes, and the game's 10 km grid. Zoom ranges from 0.25× to 1024× the aspect-preserving fit. Closer zoom enlarges the available imagery; it does not add source detail. Grid labels can appear along the edges, within cells, or be hidden. World coordinates use centered X/Z meters: +X east and +Z north.

## Build and run

Requirements: Rust with the `wasm32-unknown-unknown` target, the matching `wasm-bindgen-cli` 0.2.129, Node.js, pnpm and uv. Vite Plus is the project web toolchain. PowerShell build scripts are included; equivalent Cargo/wasm-bindgen commands work on other platforms.

```powershell
git clone https://github.com/clankagent/nuclear-option-map.git
cd nuclear-option-map
rustup target add wasm32-unknown-unknown
cargo install wasm-bindgen-cli --version 0.2.129 --locked
pnpm install --frozen-lockfile
uv sync --locked
./scripts/build-wasm.ps1
uv run python tools/extract_map.py "PATH/TO/NuclearOption_Data"
./scripts/build-library.ps1
pnpm exec vp dev
```

The demo runs on `http://127.0.0.1:47832`. `pnpm exec vp build` writes a local demo build. Extracted images and geometry are excluded from Git. See [extraction tools](tools/README.md) for optional geometry export.

## Browser library

`scripts/build-library.ps1` writes `dist/browser/index.js` and TypeScript declarations. The ES module embeds its WASM core, so it can be served as one JavaScript file. It also writes an independent WASM module and bindings to `dist/wasm/`; keep those files together. No npm publication is required.

```typescript
import { createMapViewer, loadMapCatalog } from './dist/browser/index.js';

const catalog = await loadMapCatalog('/maps/manifest.json');
const viewer = await createMapViewer(document.querySelector('canvas')!, {
  map: catalog.maps[0],
  mode: 'builtin',
  grid: true,
  gridLabels: 'edges',
  tint: '#6f9d75',
  onPointerMove: point => console.log(point),
});

viewer.fit();
// When the host removes the canvas:
viewer.destroy();
```

Give the canvas a CSS width and height. The adapter observes size changes and pixel density. `builtin` multiplies the source sprite's RGB by the selected tint and preserves alpha; `raw` displays the exported sprite unchanged. The default green is configurable.

The factory initializes WASM before constructing the viewer. Supported operations include `setMap`, `updateRasterTiles`, `setMode`, `setGrid`, `setGridLabels`, `setTint`, `setContourInterval`, `setContourSmoothing`, `setTopoStyle`, `setMaxZoom`, `setView`, `fit`, `zoomIn`, `zoomOut`, `worldToScreen`, `screenToWorld` and `destroy`. `state` exposes the current map, zoom relative to fit, pixels per meter, viewport size and world center. `onViewChange` and `onPointerMove` let a host display its own controls and coordinates. Pointer events include `gridReference` (for example `E4`), or `null` outside the map/grid. `gridCell`, `gridReference` and `gridRowLabel` are also exported for host overlays. Supply CORS-readable images when hosting assets on another origin.

Use `viewer.setView({ centerX: 12000, centerZ: -4500, zoom: 12.5 })` to set the world-meter center and zoom relative to fit in one update. Each field is optional, so a host can move the center or change zoom alone. The viewer rejects nonfinite values, clamps zoom to its configured limit, and emits one `onViewChange` notification for the update.

Set `maxZoom` in the viewer options or call `viewer.setMaxZoom(max)` to limit all zoom input. The library default is 1024; valid limits range from 0.25 to 1024. Lowering the limit clamps the current zoom while keeping the world center. Raising it leaves the current zoom unchanged. The limit persists across map changes. Fit uses 1×, or the limit when it is below 1×.

The demo starts with a 400× limit. Settings can enable extended zoom up to 1,024×. Turning it off restores the 400× limit and reduces a closer view to 400×.

Use `viewer.setMap(otherImage, {preserveView: true})` to compare images with identical world dimensions while retaining zoom and center. The demo saves grid, label, tint, contour and topographic style preferences locally and discovers optional views from `/maps/variants.json`; the original source image is always available.

## Demo controls

Choose a map from the upper-left menu and a view from the header tabs or Layers panel. Maps start in Satellite when that dataset is available; shared links restore their selected view. Drag to pan, use the wheel or pinch to zoom, double-click to zoom in, and use the footer controls to zoom or fit. The jump field accepts grid references such as `E4` and meter coordinates such as `12000,-4500`. The readout copies the current coordinates when selected. The overview map moves the main view when clicked or dragged.

Places retains saved locations and JSON import/export. Select a location to move the map to it. Right-click, long-press, or press Shift+F10 to copy coordinates, copy the grid reference, center the map, or zoom in. Measurement and pin-placement tools and their map overlays are removed.

Copy view link shares the map, view, center, and zoom. Links to views above 400× enable extended zoom. The address updates as you move, so reloading restores the current view. Search clears after a jump; Escape clears its text first, then closes it. On phones, panels open above the toolbar. Tap outside a panel or drag its handle down to close it.

Press `/` or Ctrl/Cmd+K to search, `B` for Places, `L` for Layers, `G` for grid, `H` for the minimap, `1`/`2`/`3` for views, `,` for Settings, and `?` for all shortcuts. Escape closes the active panel. The canvas also supports arrows to pan, `+`/`-` to zoom, and `0` to fit. Saved places and display settings stay in this browser.

The schema-one catalog contains `maps` with `id`, `name`, `widthMeters`, `heightMeters`, `image`, and `grid: {columns, rows, offsetX, offsetY}`. Image URLs resolve relative to the manifest. Grid lines use `x = -offsetX + column * 10000` and `z = offsetY - row * 10000`; grid extent is independent of sprite bounds.

Detailed raster maps add `tiles: {tileSize, gutter, levels}`. Each level specifies `level`, `width`, `height`, `columns`, `rows` and `urlTemplate`, such as `tiles/{z}/{x}/{y}.webp`. Levels start at zero and double their pixel dimensions. A small `image` overview remains underneath while sharper visible tiles load and fade into place. The renderer loads visible parent coverage and detail before neighboring prefetch, limits outstanding loaders to six, and retains decoded fallback coverage during navigation. Loaded parents draw only where sharper children are missing or fading. Versioned satellite tiles use immutable browser caching in the dev server; manifests remain revalidatable. Built-in sprites can use the same delivery format without inventing extra source detail.

Precomputed regional detail can add `coverage: [{x: 39, y: 24, columns: 6, rows: 4}]` to any raster level above zero. Coordinates are tile indices from the top-left; each rectangle includes its starting tile and excludes its right/bottom edge. Omitted coverage means the entire level is available; an empty array means no tiles are available. The root level must remain complete. Level dimensions still describe the whole map and double normally, so regional tiles retain the same geographic alignment. Only advertised tiles are requested, including during prefetch; elsewhere the viewer retains available ancestors. A finer region may skip an intermediate sparse level. Supply finished static files before advertising their coverage and update versioned URLs when replacing them. Rendering belongs in offline preparation, never in a tile request handler.

A level can also define `preferred: {urlTemplate, coverage}` for completed replacement tiles. These take priority over baseline URLs and `urlOverrides`; a failed replacement keeps or loads the baseline tile. Call `viewer.updateRasterTiles(pyramid)` to refresh routes on the same tile lattice while retaining the viewport, overview and unchanged decoded tiles. Changed tiles keep their old image until the replacement loads and fades in. Advertise only completed files in preferred coverage.

Topographic maps can add `contours: {intervals: [10, 20, 50, 100], levels}`. Each level supplies `level`, `columns`, `rows` and a URL template containing `{interval}`, `{x}` and `{y}` (optionally `{z}`). Contour JSON uses schema one, world-meter `bounds: [minX, minZ, maxX, maxZ]`, and `paths: [{id, elevationMeters, isIndex, points: [[x, z], ...]}]`. Lines and index-elevation labels render at screen resolution independently of the raster backdrop. The default `contourInterval: 'auto'` selects finer measured elevation intervals at closer zoom. Use `await viewer.setContourInterval(10)` for fixed spacing, or `await viewer.setContourInterval('auto')` to restore automatic density. Replacement coarse coverage is prepared before switching, and manual preferences survive switching views. Only supplied intervals are used; no heights are invented.

`contourSmoothingMeters` defaults to 40 and rounds sharp line corners for cartographic display. `viewer.setContourSmoothing(0)` shows the exported contour lines without corner rounding; values from 0 to 160 meters set the corner-rounding radius. This affects drawing only, with displacement bounded to half the radius. Source heights, contour coordinates and 3D geometry remain unchanged. Exported lines may already have bounded simplification. The demo offers a 0-80 m smoothing slider and displays the active contour interval in its footer.

Painting is scheduled once per animation frame. Dense contour layers are built in bounded batches; the previous completed layer follows pan and zoom while its replacement is prepared. Offscreen segments are culled before drawing, and unchanged layers are reused. Subpixel vertex reduction is bounded to 0.25 screen pixels and retains chunk endpoints. Loading and label placement also yield between batches to keep input responsive.

Optional terrain styling adds `topography: {minHeightMeters, maxHeightMeters, heightStepMeters, tiles}` to the map. Lossless PNG tiles encode `height = minHeightMeters + (R * 256 + G) * heightStepMeters`, northwest hillshade in B (180 is a flat surface), and a binary alpha mask: 255 for valid land, 0 for water/unknown. Use tile sizes of at most 512 pixels and at most four root tiles. URLs resolve against the catalog as for other tile pyramids. Keep these measured elevations separate from display smoothing.

`viewer.setTopoStyle('lowlands-dark', 0.45, true)` combines darker lowlands with shaded relief. Other styles are `'plain'`, `'elevation-colors'` (green lowlands to cream highlands), and `'hillshade'`. The third argument enables relief with any fill. Strength ranges from 0 to 1 and applies once beneath contours; changing it requires no tile requests or recoloring. Defaults are elevation colors, strength 0.45, and relief on. The constructor accepts `topoStyle`, `topoStrength` and `topoHillshade`, and `state` exposes them. The optional layer bounds retained packed/display tiles and viewport scratch to a 24 MiB budget and recolors at most two visible cached tiles per paint; transient decode buffers and browser overhead are additional. Plain with relief off makes no height-tile requests.
## Native hosts and verification

The [Rust core](crates/map-core/README.md) can be used by a native host without browser APIs. The canvas adapter is a browser renderer; native applications supply their own rendering and input adapter.

```powershell
cargo test --locked --workspace
pnpm exec vp run typecheck
pnpm exec vp test run
uv run python -m unittest discover -s tests/extraction
```

Terrain extraction exports map-prefab mesh instances and their resolved placement hierarchy. The Blender importer compensates for OBJ handedness and converts the world to Blender axes. Offline tools produce terrain scenes, GLB models and elevation arrays, while the cartographic pipeline projects ground, roads and building footprints into raster tiles and extracts contour vectors from the ground triangles. Missing elevations stay explicitly unknown; roofs do not generate terrain contours.

The demo discovers locally generated views from `/maps/variants.json`. Satellite rendering requires recovered scenery and material assignments; the game's coarse terrain-color texture alone does not provide that view. See [extraction tools](tools/README.md) for local processing and optional smaller GLB previews. Mission loading and an interactive 3D viewer are planned extensions.

Own code is MIT licensed. Nuclear Option game assets are not included or covered by that license.
