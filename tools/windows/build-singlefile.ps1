param([switch]$ForceColdTest, [ValidateSet('ARM64','x64')][string]$Architecture='ARM64')
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$testRoot = Join-Path $env:LOCALAPPDATA 'IIDXResourceViewerTest'
$paths = Get-Content (Join-Path $testRoot 'paths.json') -Raw | ConvertFrom-Json
$portable = Get-Content (Join-Path $testRoot 'portable-bundle.json') -Raw | ConvertFrom-Json
$sharedProject = if ($env:IIDX_SHARED_PROJECT) { $env:IIDX_SHARED_PROJECT } else { Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }
Copy-Item -LiteralPath (Join-Path $sharedProject 'native\windows-launcher') -Destination (Join-Path $paths.home 'native') -Recurse -Force
Copy-Item -LiteralPath (Join-Path $sharedProject 'native\assets') -Destination (Join-Path $paths.home 'native') -Recurse -Force
$project = Join-Path $paths.home 'native\windows-launcher\Launcher.csproj'
$publish = Join-Path $testRoot 'singlefile-launcher'
$env:DOTNET_ROOT = Split-Path $paths.dotnet
$env:DOTNET_CLI_HOME = Join-Path $testRoot 'dotnet-home'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:NUGET_PACKAGES = Join-Path $testRoot 'nuget'
& $paths.dotnet publish $project -c Release -r ("win-" + $Architecture.ToLowerInvariant()) -o $publish 2>&1 | Tee-Object (Join-Path $testRoot 'singlefile-build.log')
if ($LASTEXITCODE) { throw 'Single EXE launcher build failed' }

Add-Type -AssemblyName System.IO.Compression.FileSystem
$payload = Join-Path $testRoot ('payload-' + [Guid]::NewGuid().ToString('N') + '.zip')
[IO.Compression.ZipFile]::CreateFromDirectory($portable.bundle, $payload, [IO.Compression.CompressionLevel]::Optimal, $false)
$payloadSize = (Get-Item $payload).Length
$payloadHash = (Get-FileHash -LiteralPath $payload -Algorithm SHA256).Hash.ToLowerInvariant()
$name = 'BEMANI素材管理器-Windows-' + $Architecture + '.exe'
$result = Join-Path $testRoot $name
$output = [IO.File]::Create($result)
try {
    foreach ($inputPath in @((Join-Path $publish 'BEMANI素材管理器.exe'), $payload)) {
        $inputFile = [IO.File]::OpenRead($inputPath)
        try { $inputFile.CopyTo($output) } finally { $inputFile.Dispose() }
    }
    $footer = New-Object IO.BinaryWriter($output, [Text.Encoding]::UTF8, $true)
    try {
        $footer.Write([Text.Encoding]::ASCII.GetBytes('BEMANI_BUNDLE_V1'))
        $footer.Write([long]$payloadSize)
        $hashBytes = New-Object byte[] 32
        for ($i = 0; $i -lt 32; $i++) { $hashBytes[$i] = [Convert]::ToByte($payloadHash.Substring($i * 2, 2), 16) }
        $footer.Write($hashBytes)
    } finally { $footer.Dispose() }
} finally { $output.Dispose() }
Remove-Item -LiteralPath $payload

# Move only this EXE to an otherwise empty Unicode path. No project directory,
# SDK path or Python directory is supplied to the launcher.
$isolated = Join-Path $testRoot ('单文件 独立测试-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $isolated | Out-Null
$isolatedExe = Join-Path $isolated $name
Copy-Item -LiteralPath $result -Destination $isolatedExe
Remove-Item Env:IIDX_VIEWER_HOME -ErrorAction SilentlyContinue
Remove-Item Env:DOTNET_ROOT -ErrorAction SilentlyContinue
Remove-Item Env:DOTNET_ROOT_ARM64 -ErrorAction SilentlyContinue
Remove-Item Env:DOTNET_ROOT_X64 -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$env:IIDX_GAME_ROOT = $paths.data
$reports = Join-Path $testRoot 'singlefile-tests'
New-Item -ItemType Directory -Force -Path $reports | Out-Null
$runtime = Join-Path $env:LOCALAPPDATA ('BEMANIResourceManager\runtime\' + $payloadHash)
if ($ForceColdTest -and (Test-Path -LiteralPath $runtime)) {
    $marker = Join-Path $runtime '.ready'
    if (!(Test-Path -LiteralPath $marker) -or (Get-Content -LiteralPath $marker -Raw) -ne $payloadHash) { throw 'Unrecognized runtime cache' }
    if (Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($runtime, [StringComparison]::OrdinalIgnoreCase) }) { throw 'Runtime cache is still in use' }
    Remove-Item -LiteralPath $runtime -Recurse -Force
}
$cold = !(Test-Path (Join-Path $runtime '.ready'))
$durations = @{}
foreach ($mode in @('first','cached')) {
    $reportDir = Join-Path $reports $mode
    New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
    foreach ($log in @('native-smoke.json','native-error.log','launcher.log','launcher-error.log')) {
        Remove-Item -LiteralPath (Join-Path $reportDir $log) -ErrorAction SilentlyContinue
    }
    $env:IIDX_TEST_REPORT_DIR = $reportDir
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $app = Start-Process -FilePath $isolatedExe -ArgumentList '--self-test','--exit-after-self-test' -WorkingDirectory $isolated -PassThru
    $null = $app.Handle
    if (!$app.WaitForExit(180000)) { & taskkill.exe /PID $app.Id /T /F | Out-Null; throw ('Single EXE ' + $mode + ' launch timed out') }
    $watch.Stop()
    if ($app.ExitCode -ne 0) { throw ('Single EXE ' + $mode + ' launch failed: ' + $app.ExitCode) }
    $report = Get-Content (Join-Path $reportDir 'native-smoke.json') -Raw | ConvertFrom-Json
    if (!$report.ok -or !$report.default_startup_empty -or !$report.replacements -or !$report.renderer.Hardware -or $report.default_animation_fps -ne 60 -or
        !$report.media.title_search_audio_bga_layer -or !$report.media.native_song_movie_preview -or !$report.media.native_animation_mp4_export) {
        throw ('Single EXE native test failed: ' + $report.error)
    }
    $durations[$mode] = $watch.Elapsed.TotalSeconds
    $currentMarkerTime = (Get-Item -LiteralPath (Join-Path $runtime '.ready')).LastWriteTimeUtc
    if ($mode -eq 'first') { $firstMarkerTime = $currentMarkerTime }
    elseif ($currentMarkerTime -ne $firstMarkerTime) { throw 'Cached launch extracted the runtime again' }
}
if ((Get-ChildItem -LiteralPath $isolated -Force).Count -ne 1) { throw 'Single EXE wrote dependencies beside itself' }
$markerTime = (Get-Item -LiteralPath (Join-Path $runtime '.ready')).LastWriteTimeUtc
$summary = @{
    ok = $true; single_exe_bytes = (Get-Item $result).Length; payload_sha256 = $payloadHash
    cold_first_launch = $cold; isolated_directory_files = 1; default_animation_fps = 60
    first_and_cached_native_tests = $true; seconds_including_native_tests = $durations
    clean_exit_codes = @(0, 0)
    runtime = $runtime; cache_marker_time = $markerTime.ToString('o')
}
$artifacts = Join-Path $sharedProject ('build\windows-' + $Architecture.ToLowerInvariant() + '\test-artifacts')
$summary | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 (Join-Path $artifacts 'singlefile-verification.json')
Copy-Item -LiteralPath (Join-Path $testRoot 'singlefile-build.log') -Destination $artifacts -Force
foreach ($mode in @('first','cached')) {
    Copy-Item -LiteralPath (Join-Path $reports ($mode + '\native-smoke.json')) -Destination (Join-Path $artifacts ('singlefile-' + $mode + '-native-smoke.json')) -Force
    Copy-Item -LiteralPath (Join-Path $reports ($mode + '\launcher.log')) -Destination (Join-Path $artifacts ('singlefile-' + $mode + '-launcher.log')) -Force
}
Copy-Item -LiteralPath $result -Destination (Join-Path $sharedProject $name) -Force
Write-Output ('SINGLE_EXE=' + (Join-Path $sharedProject $name))
$summary | ConvertTo-Json -Depth 5
