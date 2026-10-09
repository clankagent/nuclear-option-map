# Nuclear Option Map

Standalone map library, separate from the sibling BepInEx mods and server projects. User requested public GitHub source, no npm publishing, and a local-router demo. Work on builtin image maps first, then actual terrain extraction, then satellite/topographic/3D views. Mission overlays are later scope.

Rust owns portable map math and processing; it builds natively and to WASM. TypeScript owns the browser facade and rendering/input adapter. Python/UnityPy is offline extraction tooling, not a browser/runtime dependency. Keep host boundaries explicit; do not pretend a browser renderer runs natively.

Use pnpm (vp where applicable), uv for Python and the installed GNU Rust toolchain. Commit coherent reviewed changes. Preserve other agents' edits; agents have separate file scopes. Public README/API docs contain consumer guidance only. Private investigation evidence belongs in ignored work/.

Never commit game assets, extracted images/meshes, proprietary assemblies, decompiled source or incoming archives. Keep local map outputs in ignored demo/public/maps/ and raw inputs in ignored work/. Do not fake the Nuclear Option map while waiting for assets. Extraction must follow serialized map references and scene transforms; a texture-name guess is not verified mapping. Record exact provenance privately and report partial exports accurately. Do not install mods, launch the game, access servers, publish npm, or change shared infrastructure within this task.

Validate Rust invariants with native tests and execute the WASM build in the browser. Review responsive demo at desktop and phone sizes; passing compilation does not establish visual quality. Use Claude Sonnet 5.5 as a scoped UI collaborator when available per global guidance.
