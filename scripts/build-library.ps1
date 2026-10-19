$ErrorActionPreference = 'Stop'
& node (Join-Path $PSScriptRoot 'build-library.mjs')
if ($LASTEXITCODE -ne 0) { throw 'Library build failed' }
