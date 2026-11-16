$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$testRoot = Join-Path $env:LOCALAPPDATA 'IIDXResourceViewerTest'
$paths = Get-Content (Join-Path $testRoot 'paths.json') -Raw | ConvertFrom-Json
$sharedProject = if ($env:IIDX_SHARED_PROJECT) { $env:IIDX_SHARED_PROJECT } else { Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }
Get-Process -Name IIDXResourceViewer -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($paths.home, [StringComparison]::OrdinalIgnoreCase) } | Stop-Process
foreach ($item in @('backend.py','requirements.txt','iidxviewer','LICENSE','COPYRIGHT','THIRD_PARTY_NOTICES.md','native','tests')) {
    Copy-Item -LiteralPath (Join-Path $sharedProject $item) -Destination $paths.home -Recurse -Force
}
$env:DOTNET_ROOT = Split-Path $paths.dotnet
$env:DOTNET_CLI_HOME = Join-Path $testRoot 'dotnet-home'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:NUGET_PACKAGES = Join-Path $testRoot 'nuget'
$env:IIDX_VIEWER_HOME = $paths.home
$env:IIDX_GAME_ROOT = $paths.data
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTHONIOENCODING = 'utf-8'
Push-Location $paths.home
try {
    $pythonLog = Join-Path $testRoot 'python-tests.log'
    $testProcess = Start-Process -FilePath $paths.python -ArgumentList '-m','unittest','discover','-s','tests','-v' -WorkingDirectory $paths.home -Wait -PassThru -NoNewWindow -RedirectStandardError $pythonLog -RedirectStandardOutput (Join-Path $testRoot 'python-tests.stdout.log')
    Get-Content $pythonLog
    if ($testProcess.ExitCode) { throw 'Windows resource tests failed' }
    & $paths.dotnet build 'native\windows\IIDXResourceViewer.csproj' -c Release -p:Platform=ARM64 -p:RuntimeIdentifier=win-arm64 2>&1 | Tee-Object (Join-Path $testRoot 'build.log')
    if ($LASTEXITCODE) { throw 'WinUI ARM64 build failed' }
    Write-Output 'WINUI_BUILD_SUCCEEDED'
    $exe = Join-Path $paths.home 'build\windows\IIDXResourceViewer.exe'
    Start-Process -FilePath $exe -ArgumentList '--self-test','--exit-after-self-test' -WorkingDirectory $paths.home
    Write-Output 'NATIVE_SELF_TEST_STARTED'
} finally { Pop-Location }
