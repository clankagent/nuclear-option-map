# Development

## Architecture and ownership

| Area | Responsibility |
| --- | --- |
| `crates/map-core/` | Portable coordinate and viewport math; no browser or game dependencies. |
| `crates/map-wasm/` | WASM bridge to the Rust core. |
| `src/` | Browser library, catalog validation, rendering, and input. |
| `demo/map-session.ts`, `demo/map-variants.ts` | Demo map lifecycle and local view discovery. |
| `demo/ui/` | Demo controls and UI state. |
| `demo/index.html`, `demo/style.css` | Shared page structure and styles. |
| `tools/` | Offline extraction and map preparation. |
| `tests/` | Automated checks and fixtures. |
| `demo/public/maps/`, `work/` | Ignored local assets, inputs, and processing evidence. |

Keep code changes separate from datasets and rendering jobs. Browser navigation consumes prepared files; it must not start renders. Game files, extracted assets, and proprietary source stay outside Git. A synthetic fixture may exercise the same interfaces without representing a game map.

## Local workflow

Use the pinned pnpm version. Run these commands from the repository root in PowerShell or WSL/Bash:

```sh
pnpm install --frozen-lockfile
pnpm run wasm
pnpm run dev
```

The WASM build needs the Rust `wasm32-unknown-unknown` target and `wasm-bindgen-cli` 0.2.129. `pnpm run wasm` uses a cross-platform Node script. The PowerShell wrapper remains available for Windows callers.

| Command | Result |
| --- | --- |
| `pnpm run build` | Code-only demo in `dist/app`. Local map assets are excluded. |
| `pnpm run package:demo` | Demo with local public assets in `dist/demo-package`. No remote fetch. |
| `pnpm run build:library` | Browser library and declarations in `dist/browser`, plus bindings in `dist/wasm`. |
| `pnpm run typecheck` | TypeScript checks. |
| `pnpm run test` | JavaScript/TypeScript tests. |
| `pnpm run test:smoke` | Desktop and phone browser checks with synthetic data and real WASM. |

Asset-inclusive packaging copies available local files. Manifests may still depend on external or proxied URLs. Check those dependencies before claiming that a package works offline.

## Verification

```sh
cargo test --locked --workspace
pnpm run wasm
pnpm run typecheck
pnpm run test
pnpm run build
pnpm exec playwright install chromium
pnpm run test:smoke
```

Build `dist/app` before running the browser smoke test. It needs no game files and starts its own preview server on loopback port 47839. Set `NUCLEAR_MAP_SMOKE_PORT` if that port is occupied. Set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to use an existing compatible Chromium executable instead of the Playwright download. Set environment variables with your shell's normal syntax.

CI runs the code checks and browser smoke test without local assets. These checks establish synthetic browser integration, not game-map fidelity or complete offline packaging. For map changes, also inspect real coastlines, cities, airbases, and mountains at overview and close zoom. For UI changes, inspect desktop and phone layouts and exercise the primary controls.

## Parallel work and integration

Work directly on the repository's default branch. Agree on disjoint file scopes before parallel edits. Core math, browser rendering, isolated UI components, documentation, and offline tools can be separate tasks when their interfaces are clear.

Coordinate changes to `AppContext`, `demo/index.html`, shared CSS, and catalog or variant manifests before editing them. These files connect multiple areas. Treat interface changes as shared work, even when their implementations live in separate files.

Use one integration and committing owner at a time. Each contributor reports files changed, checks run, and unresolved interface changes. Review the combined diff, then commit coherent changes. Stage only the intended files or hunks; do not stage another contributor's pending work. Preserve unrelated edits, local assets, and rendering receipts. A code integration does not authorize promoting a dataset or starting a rendering job.
