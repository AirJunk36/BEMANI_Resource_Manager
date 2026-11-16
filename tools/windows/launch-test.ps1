$ErrorActionPreference = 'Stop'
$testRoot = Join-Path $env:LOCALAPPDATA 'IIDXResourceViewerTest'
$paths = Get-Content (Join-Path $testRoot 'paths.json') -Raw | ConvertFrom-Json
$env:IIDX_VIEWER_HOME = $paths.home
$env:IIDX_GAME_ROOT = $paths.data
$env:IIDX_TEST_REPORT_DIR = $testRoot
$report = Join-Path $testRoot 'native-smoke.json'
'{"running":true}' | Set-Content $report -Encoding UTF8
$exe = Join-Path $paths.home 'build\windows\IIDXResourceViewer.exe'
$process = Start-Process -FilePath $exe -ArgumentList '--self-test','--exit-after-self-test' -WorkingDirectory $paths.home -PassThru -RedirectStandardError (Join-Path $testRoot 'native.stderr.log') -RedirectStandardOutput (Join-Path $testRoot 'native.stdout.log')
$null = $process.Handle
Write-Output ('NATIVE_TEST_PID=' + $process.Id)
if (!$process.WaitForExit(120000)) { throw 'Native test timed out' }
Write-Output ('NATIVE_EXIT_CODE=' + $process.ExitCode)
if ($process.ExitCode -ne 0) { throw 'Native application did not exit cleanly' }
