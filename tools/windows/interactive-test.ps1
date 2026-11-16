$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$testRoot = if ($env:IIDX_TEST_ROOT) { $env:IIDX_TEST_ROOT } else { Join-Path $env:LOCALAPPDATA 'IIDXResourceViewerTest' }
$user=(Get-CimInstance Win32_ComputerSystem).UserName
if(!$user){throw 'Windows has no logged-in desktop user'}
$result=Join-Path $testRoot 'interactive-test-result.json'
Remove-Item -LiteralPath $result -ErrorAction SilentlyContinue
$body=@'
$ErrorActionPreference='Stop'
$testRoot = if ($env:IIDX_TEST_ROOT) { $env:IIDX_TEST_ROOT } else { Join-Path $env:LOCALAPPDATA 'IIDXResourceViewerTest' }
$result=Join-Path $testRoot 'interactive-test-result.json'
try {
    [IO.File]::WriteAllText((Join-Path $testRoot 'interactive-test-started.txt'),[Security.Principal.WindowsIdentity]::GetCurrent().Name)
    $paths=Get-Content (Join-Path $testRoot 'paths.json') -Raw | ConvertFrom-Json
    $env:IIDX_VIEWER_HOME=$paths.home
    $env:IIDX_GAME_ROOT=$paths.data
    $env:IIDX_TEST_REPORT_DIR=$testRoot
    $exe=Join-Path $paths.home 'build\windows\IIDXResourceViewer.exe'
    $process=Start-Process -FilePath $exe -ArgumentList '--self-test','--exit-after-self-test' -WorkingDirectory $paths.home -PassThru
    $null=$process.Handle
    if(!$process.WaitForExit(120000)){throw 'Native test timed out'}
    if($process.ExitCode -ne 0){throw ('Native application exit failed: '+$process.ExitCode)}
    $report=Get-Content (Join-Path $testRoot 'native-smoke.json') -Raw | ConvertFrom-Json
    if(!$report.ok){throw ('Native test failed: '+$report.error)}
    @{exit_code=$process.ExitCode;user=[Security.Principal.WindowsIdentity]::GetCurrent().Name}|ConvertTo-Json|Set-Content -Encoding UTF8 $result
}catch{@{error=$_.ToString()}|ConvertTo-Json|Set-Content -Encoding UTF8 $result}
'@
$encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($body))
$name='Codex BEMANI Test '+[Guid]::NewGuid().ToString('N')
$action=New-ScheduledTaskAction -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -Argument ('-NoProfile -WindowStyle Hidden -EncodedCommand '+$encoded)
$principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
try {
    Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings | Out-Null
    Start-ScheduledTask -TaskName $name
    $deadline=(Get-Date).AddSeconds(150)
    while(!(Test-Path -LiteralPath $result)) {
        if((Get-Date)-gt $deadline){Get-ScheduledTaskInfo -TaskName $name | Format-List;throw 'Interactive Windows test did not respond'}
        Start-Sleep -Milliseconds 250
    }
    Get-Content -LiteralPath $result
    $outcome=Get-Content -LiteralPath $result -Raw | ConvertFrom-Json
    if($outcome.error -or $outcome.exit_code -ne 0){throw ('Interactive test failed: '+($outcome | ConvertTo-Json -Compress))}
    Get-Content (Join-Path $testRoot 'native-smoke.json') -Raw
}finally{Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue}
