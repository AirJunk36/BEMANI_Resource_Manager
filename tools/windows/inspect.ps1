$ErrorActionPreference = 'Stop'
Write-Output ('USERPROFILE=' + $env:USERPROFILE)
Write-Output ('ARCH=' + $env:PROCESSOR_ARCHITECTURE)
foreach ($name in @('dotnet', 'python', 'py', 'winget')) {
    $command = Get-Command $name -ErrorAction SilentlyContinue
    if ($command) { Write-Output ($name + '=' + $command.Source) }
}
foreach ($path in @((Join-Path $env:ProgramFiles 'dotnet\dotnet.exe'), (Join-Path $env:LOCALAPPDATA 'Programs\Python'), (Join-Path $env:LOCALAPPDATA 'Microsoft\dotnet\dotnet.exe'))) {
    if (Test-Path $path) { Get-Item $path | Select-Object FullName }
}
$project = if ($env:IIDX_SHARED_PROJECT) { $env:IIDX_SHARED_PROJECT } else { Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }
Write-Output ('PROJECT_ACCESS=' + (Test-Path (Join-Path $project 'backend.py')))
Invoke-WebRequest -UseBasicParsing -Uri 'https://dotnetcli.blob.core.windows.net/dotnet/release-metadata/8.0/releases.json' -OutFile (Join-Path $env:TEMP 'iidx-dotnet-releases.json')
$metadata = Get-Content (Join-Path $env:TEMP 'iidx-dotnet-releases.json') -Raw | ConvertFrom-Json
Write-Output ('DOTNET_SDK=' + $metadata.'latest-sdk')
