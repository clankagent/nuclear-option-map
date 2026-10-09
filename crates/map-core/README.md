# Native viewport math

`nuclear-option-map-core` is independent of browser APIs, Unity and game assets. Add a Cargo path dependency on this crate to use `MapViewport` from a native Rust host. The WASM bridge calls this same implementation.

```rust
use nuclear_option_map_core::MapViewport;

let mut viewport = MapViewport::new(24_000.0, 32_000.0, 900.0, 600.0).unwrap();
let pixels = viewport.world_to_screen(120.0, 450.0).unwrap();
viewport.zoom_at(2.0, pixels[0], pixels[1]).unwrap();
```

The map's world bounds are centered at `(0, 0)` with X east/right and Z north/up. World dimensions and positions use meters. Screen coordinates have their origin at the top left and use pixels. The viewport does not constrain navigation to map bounds or interpret terrain origins; transform source data to the centered domain in its adapter.

`scale()` returns pixels per meter. `zoom()` is relative to the aspect-preserving fit scale, clamped to 0.25–64. `pan(dx, dy)` moves map content by the requested pixel delta. `zoom_at(factor, x, y)` keeps the world point under that screen coordinate anchored. `resize(width, height)` retains world center and relative zoom. `fit()` resets center and zoom. Coordinate conversions return `[x, y]` screen coordinates or `[x, z]` world coordinates.

Dimensions and zoom factors must be finite and positive; positions and pan deltas must be finite. Computations that cannot produce finite results return `InvalidInput`. Failed mutations preserve the previous viewport state. Run native verification with `cargo test --workspace --locked` from the repository root.
