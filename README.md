# Nuclear Option Map

A portable map core and browser map viewer for Nuclear Option. Rust provides map-coordinate and viewport operations, compiled natively or to WebAssembly. A TypeScript browser adapter draws locally extracted built-in map images with pan, zoom, tint and grid overlays.

Game files and extracted assets are not included. The offline extraction tools read a local client data directory; the viewer uses the resulting map manifest and images. No game process is required for image-based maps.

The initial scope is built-in tactical maps. Terrain geometry extraction provides the foundation for additional map views. Mission loading and interactive 3D are planned extensions.
