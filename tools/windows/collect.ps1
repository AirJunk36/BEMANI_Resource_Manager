param([switch]$SkipCoreTests, [ValidateSet('ARM64','x64')][string]$Architecture='ARM64')
$ErrorActionPreference = 'Stop'
$testRoot = Join-Path $env:LOCALAPPDATA 'IIDXResourceViewerTest'
$paths = Get-Content (Join-Path $testRoot 'paths.json') -Raw | ConvertFrom-Json
$sharedProject = if ($env:IIDX_SHARED_PROJECT) { $env:IIDX_SHARED_PROJECT } else { Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }
$report = Get-Content (Join-Path $testRoot 'native-smoke.json') -Raw | ConvertFrom-Json
if ($report.architecture -ne $Architecture) { throw ('Native test architecture does not match ' + $Architecture) }
if (!$report.ok -or !$report.default_startup_empty -or !$report.legacy_bin_native_previews -or !$report.replacements -or !$report.renderer.Hardware -or $report.default_animation_fps -ne 60) { throw 'Native GPU, 60 FPS and replacement UI tests did not pass' }
if (!$report.media.title_search_audio_bga_layer -or !$report.media.native_song_movie_preview -or !$report.media.native_animation_mp4_export) { throw 'Native song and MP4 verification did not pass' }
foreach ($name in @('backend.py','requirements.txt')) {
    Copy-Item -LiteralPath (Join-Path $sharedProject $name) -Destination $paths.home -Force
}
foreach ($name in @('iidxviewer','tests')) {
    & robocopy.exe (Join-Path $sharedProject $name) (Join-Path $paths.home $name) /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XD __pycache__ /XF *.pyc | Out-Null
    if ($LASTEXITCODE -ge 8) { throw ('Source copy failed: ' + $name) }
}
$env:IIDX_GAME_ROOT = $paths.data
$env:IIDX_LIVE2D_ROOT=Join-Path $paths.data 'live2d'
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTHONIOENCODING = 'utf-8'
# UI/package-only iterations can reuse the passed tests for unchanged Python sources.
if (!$SkipCoreTests) {
    $tests = Start-Process -FilePath $paths.python -ArgumentList '-m','unittest','discover','-s','tests','-v' -WorkingDirectory $paths.home -Wait -PassThru -NoNewWindow -RedirectStandardError (Join-Path $testRoot 'python-tests.log') -RedirectStandardOutput (Join-Path $testRoot 'python-tests.stdout.log')
    if ($tests.ExitCode) { throw 'Final resource core tests failed' }
}
$artifacts = Join-Path $sharedProject ('build\windows-' + $Architecture.ToLowerInvariant() + '\test-artifacts')
New-Item -ItemType Directory -Force -Path $artifacts | Out-Null
foreach ($name in @('native-smoke.json','native-smoke.png','legacy-bin.png','replacement-ui.png','song-studio.png','live2d-simple.png','live2d-professional.png','build.log','python-tests.log')) {
    Copy-Item -LiteralPath (Join-Path $testRoot $name) -Destination $artifacts -Force
}
$bundleName = 'IIDXResourceViewer-Windows-' + $Architecture
$bundle = Join-Path $testRoot ('p-' + [DateTime]::UtcNow.ToString('HHmmss') + '\IIDXResourceViewer')
New-Item -ItemType Directory -Force -Path $bundle | Out-Null
Get-ChildItem (Join-Path $paths.home 'build\windows') | Copy-Item -Destination $bundle -Recurse -Force
foreach ($name in @('backend.py','requirements.txt','iidxviewer','LICENSE','COPYRIGHT','THIRD_PARTY_NOTICES.md')) {
    Copy-Item -LiteralPath (Join-Path $paths.home $name) -Destination $bundle -Recurse -Force
}
$venv = Join-Path $paths.home '.venv'
$site = Join-Path $venv 'Lib\site-packages'
$exclude = @('PySide6\qml','PySide6\include','PySide6\typesystems','PySide6\lib','shiboken6\include','lxml\includes') | ForEach-Object { Join-Path $site $_ }
& robocopy.exe $venv (Join-Path $bundle '.venv') /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XD $exclude | Out-Null
if ($LASTEXITCODE -ge 8) { throw 'Portable Python copy failed' }
Copy-Item -LiteralPath (Join-Path $sharedProject 'README-Windows.txt') -Destination $bundle -Force
$stream = [IO.File]::OpenRead((Join-Path $bundle 'IIDXResourceViewer.exe'))
try {
    $reader = New-Object IO.BinaryReader($stream)
    $stream.Position = 0x3c
    $header = $reader.ReadUInt32()
    $stream.Position = $header + 4
    $machine = $reader.ReadUInt16()
    $expectedMachine = if ($Architecture -eq 'x64') { 0x8664 } else { 0xaa64 }
    if ($machine -ne $expectedMachine) { throw ('Output executable is not ' + $Architecture) }
} finally { $stream.Dispose() }
$env:IIDX_VIEWER_HOME = $bundle
$env:IIDX_GAME_ROOT = $paths.data
$portableReport = Join-Path $testRoot 'portable-test'
New-Item -ItemType Directory -Force -Path $portableReport | Out-Null
$env:IIDX_TEST_REPORT_DIR = $portableReport
$process = Start-Process -FilePath (Join-Path $bundle 'IIDXResourceViewer.exe') -ArgumentList '--self-test','--exit-after-self-test' -WorkingDirectory $bundle -PassThru
$null = $process.Handle
if (!$process.WaitForExit(120000)) { throw 'Portable native test timed out' }
if ($process.ExitCode -ne 0) { throw ('Portable application exit failed: ' + $process.ExitCode) }
$portableResult = Get-Content (Join-Path $portableReport 'native-smoke.json') -Raw | ConvertFrom-Json
if ($portableResult.architecture -ne $Architecture) { throw 'Portable test architecture mismatch' }
if (!$portableResult.ok -or !$portableResult.default_startup_empty -or !$portableResult.legacy_bin_native_previews -or !$portableResult.replacements -or !$portableResult.renderer.Hardware -or $portableResult.default_animation_fps -ne 60) { throw ('Portable native test failed: ' + $portableResult.error) }
Copy-Item -LiteralPath (Join-Path $portableReport 'native-smoke.json') -Destination (Join-Path $artifacts 'portable-native-smoke.json') -Force
Copy-Item -LiteralPath (Join-Path $portableReport 'legacy-bin.png') -Destination (Join-Path $artifacts 'portable-legacy-bin.png') -Force
Copy-Item -LiteralPath (Join-Path $portableReport 'native-smoke.png') -Destination (Join-Path $artifacts 'portable-native-smoke.png') -Force
Copy-Item -LiteralPath (Join-Path $portableReport 'replacement-ui.png') -Destination (Join-Path $artifacts 'portable-replacement-ui.png') -Force
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = Join-Path $testRoot ($bundleName + '-' + [DateTime]::UtcNow.ToString('yyyyMMddHHmmss') + '.zip')
[IO.Compression.ZipFile]::CreateFromDirectory($bundle, $zip, [IO.Compression.CompressionLevel]::Optimal, $true)
Copy-Item -LiteralPath $zip -Destination (Join-Path $sharedProject ($bundleName + '.zip')) -Force
Write-Output ('PORTABLE_ARCHIVE=' + (Join-Path $sharedProject ($bundleName + '.zip')))
Write-Output ('ARCHIVE_BYTES=' + (Get-Item $zip).Length)
@{ bundle = $bundle; archive = $zip } | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $testRoot 'portable-bundle.json')
