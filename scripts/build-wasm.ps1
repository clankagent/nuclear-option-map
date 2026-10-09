# Prerequisites: rustup target add wasm32-unknown-unknown
# Install the matching CLI with: cargo install wasm-bindgen-cli --version 0.2.129 --locked
# The script also accepts a project-local official Windows release under tools/bin/.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$version = '0.2.129'
$localTool = Join-Path $projectRoot "tools/bin/wasm-bindgen-$version-x86_64-pc-windows-msvc/wasm-bindgen.exe"
$tool = if (Test-Path $localTool) { $localTool } else { (Get-Command wasm-bindgen -ErrorAction Stop).Source }
$actualVersion = & $tool --version
if ($LASTEXITCODE -ne 0 -or $actualVersion -ne "wasm-bindgen $version") {
    throw "Install wasm-bindgen-cli $version to match Cargo.lock. Found: $actualVersion"
}
Push-Location $projectRoot
try {
    & cargo build --locked --release -p nuclear-option-map-wasm --target wasm32-unknown-unknown
    if ($LASTEXITCODE -ne 0) { throw 'Rust WASM build failed' }
    & $tool 'target/wasm32-unknown-unknown/release/nuclear_option_map_wasm.wasm' --target web --out-dir pkg --out-name nuclear_option_map
    if ($LASTEXITCODE -ne 0) { throw 'wasm-bindgen generation failed' }
} finally { Pop-Location }
