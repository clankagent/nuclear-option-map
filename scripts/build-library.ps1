$ErrorActionPreference = 'Stop'
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    & vp build --config vite.lib.config.ts
    if ($LASTEXITCODE -ne 0) { throw 'Browser library build failed' }
    & pnpm exec tsc -p tsconfig.lib.json
    if ($LASTEXITCODE -ne 0) { throw 'Library declaration generation failed' }
    New-Item -ItemType Directory -Force dist/wasm | Out-Null
    Copy-Item -LiteralPath pkg/nuclear_option_map.js,pkg/nuclear_option_map.d.ts,pkg/nuclear_option_map_bg.wasm,pkg/nuclear_option_map_bg.wasm.d.ts -Destination dist/wasm
} finally { Pop-Location }
