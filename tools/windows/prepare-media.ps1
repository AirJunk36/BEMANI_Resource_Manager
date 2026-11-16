$ErrorActionPreference='Stop'
$testRoot=Join-Path $env:LOCALAPPDATA 'IIDXResourceViewerTest'
$paths=Get-Content (Join-Path $testRoot 'paths.json') -Raw | ConvertFrom-Json
$sharedProject = if ($env:IIDX_SHARED_PROJECT) { $env:IIDX_SHARED_PROJECT } else { Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }
Push-Location $paths.home
try {
    & $paths.python (Join-Path $sharedProject 'tools\windows\create-media-fixtures.py') $paths.data
    if ($LASTEXITCODE) { throw 'Media fixture generation failed' }
} finally { Pop-Location }
