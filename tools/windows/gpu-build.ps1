param([ValidateSet('ARM64','x64')][string]$Architecture='ARM64', [switch]$SkipCoreTests)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$testRoot = if ($env:IIDX_TEST_ROOT) { $env:IIDX_TEST_ROOT } else { Join-Path $env:LOCALAPPDATA 'IIDXResourceViewerTest' }
$paths=Get-Content (Join-Path $testRoot 'paths.json') -Raw | ConvertFrom-Json
$sharedProject = if ($env:IIDX_SHARED_PROJECT) { $env:IIDX_SHARED_PROJECT } else { Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }
foreach($name in @('backend.py','requirements.txt')) {
    Copy-Item -LiteralPath (Join-Path $sharedProject $name) -Destination $paths.home -Force
}
foreach($name in @('iidxviewer','native','tests')) {
    & robocopy.exe (Join-Path $sharedProject $name) (Join-Path $paths.home $name) /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XD __pycache__ obj bin /XF *.pyc | Out-Null
    if($LASTEXITCODE -ge 8){throw ('Source copy failed: '+$name)}
}
$env:DOTNET_ROOT=Split-Path $paths.dotnet
$env:DOTNET_CLI_HOME=Join-Path $testRoot 'dotnet-home'
$env:DOTNET_CLI_TELEMETRY_OPTOUT='1'
$env:NUGET_PACKAGES=Join-Path $testRoot 'nuget'
$env:IIDX_GAME_ROOT=$paths.data
$env:IIDX_LIVE2D_ROOT=Join-Path $paths.data 'live2d'
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONIOENCODING='utf-8'
Push-Location $paths.home
try {
    & $paths.dotnet build 'native\windows\IIDXResourceViewer.csproj' -t:Rebuild -c Release "-p:Platform=$Architecture" "-p:RuntimeIdentifier=win-$($Architecture.ToLowerInvariant())" 2>&1 | Tee-Object (Join-Path $testRoot 'build.log')
    if($LASTEXITCODE){throw 'Windows UI build failed'}
    $probe="from PySide6.QtGui import QGuiApplication; from iidxviewer.gpu import GpuDevice; app=QGuiApplication([]); d=GpuDevice(); print(d.info); d.close()"
    & $paths.python -c $probe
    if($LASTEXITCODE){throw 'Windows GPU probe failed'}
    if (!$SkipCoreTests) {
        $tests=Start-Process -FilePath $paths.python -ArgumentList '-m','unittest','discover','-s','tests','-v' -WorkingDirectory $paths.home -Wait -PassThru -NoNewWindow -RedirectStandardError (Join-Path $testRoot 'python-tests.log') -RedirectStandardOutput (Join-Path $testRoot 'python-tests.stdout.log')
        Get-Content (Join-Path $testRoot 'python-tests.log') -Tail 12
        if($tests.ExitCode){throw 'Windows GPU and resource tests failed'}
    }
} finally {Pop-Location}
