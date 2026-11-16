$ErrorActionPreference = 'Stop'
$testRoot = Join-Path $env:LOCALAPPDATA 'IIDXResourceViewerTest'
$appRoot = Join-Path $testRoot 'app'
$dotnet = Join-Path $testRoot 'dotnet\dotnet.exe'
$sharedProject = if ($env:IIDX_SHARED_PROJECT) { $env:IIDX_SHARED_PROJECT } else { Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }
Copy-Item -LiteralPath (Join-Path $sharedProject 'native') -Destination $appRoot -Recurse -Force
Get-Process IIDXResourceViewer -ErrorAction SilentlyContinue | Where-Object { $_.Path -like ($appRoot + '\*') } | Stop-Process
$env:DOTNET_ROOT = Split-Path $dotnet
$env:DOTNET_CLI_HOME = Join-Path $testRoot 'dotnet-home'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:DOTNET_GENERATE_ASPNET_CERTIFICATE = 'false'
$env:NUGET_PACKAGES = Join-Path $testRoot 'nuget'
Push-Location $appRoot
try {
    & $dotnet build 'native\windows\IIDXResourceViewer.csproj' -c Release -p:Platform=ARM64 -p:RuntimeIdentifier=win-arm64 2>&1 | Tee-Object (Join-Path $testRoot 'build.log')
    if ($LASTEXITCODE) { throw 'WinUI ARM64 build failed' }
    Write-Output 'WINUI_BUILD_SUCCEEDED'
} finally { Pop-Location }
