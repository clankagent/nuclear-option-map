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

The tool loads root serialized asset/scene files and their resource streams. For stripped MonoBehaviour trees it generates the schemas from the supplied `Managed/` assemblies, using pinned TypeTreeGeneratorAPI 0.0.10. Arbitrary StreamingAssets bundles require separate investigation rather than guessing a Unity version.

UnityPy API reference: [official README](https://github.com/K0lb3/UnityPy/blob/master/README.md). UnityPy 1.23.0 is pinned in the Python project and lockfile.
