$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$sharedProject = if ($env:IIDX_SHARED_PROJECT) { $env:IIDX_SHARED_PROJECT } else { Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }
$sharedGameRoot = if ($env:IIDX_GAME_ROOT) { $env:IIDX_GAME_ROOT } else { Split-Path $sharedProject }
$testRoot = Join-Path $env:LOCALAPPDATA 'IIDXResourceViewerTest'
$paths = Get-Content (Join-Path $testRoot 'paths.json') -Raw | ConvertFrom-Json
$site = Join-Path $paths.home '.venv\Lib\site-packages'
& $paths.python -m pip install --disable-pip-version-check --no-warn-script-location --target $site 'pycryptodome>=3.21,<4' 'numpy>=2.3,<3'
if ($LASTEXITCODE) { throw 'Legacy cipher dependency install failed' }
$fixtures = Get-Content (Join-Path $sharedProject 'tools\windows\fixtures.json') -Raw | ConvertFrom-Json
foreach ($relative in $fixtures) {
    $destination = Join-Path $paths.data $relative
    if (!(Test-Path -LiteralPath $destination)) {
        New-Item -ItemType Directory -Force -Path (Split-Path $destination) | Out-Null
        Copy-Item -LiteralPath (Join-Path $sharedGameRoot $relative) -Destination $destination
    }
}
Write-Output ('FIXTURES_READY=' + $fixtures.Count)
