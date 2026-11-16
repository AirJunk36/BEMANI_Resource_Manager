param([ValidateSet('ARM64','x64')][string]$Architecture='ARM64')
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$testRoot = if ($env:IIDX_TEST_ROOT) { $env:IIDX_TEST_ROOT } else { Join-Path $env:LOCALAPPDATA 'IIDXResourceViewerTest' }
$user = (Get-CimInstance Win32_ComputerSystem).UserName
if (!$user) { throw 'Windows has no logged-in desktop user' }
$result = Join-Path $testRoot 'interactive-singlefile-result.json'
Remove-Item -LiteralPath $result -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $testRoot 'interactive-singlefile.log') -ErrorAction SilentlyContinue
$body = @'
$ErrorActionPreference = 'Stop'
$testRoot = if ($env:IIDX_TEST_ROOT) { $env:IIDX_TEST_ROOT } else { Join-Path $env:LOCALAPPDATA 'IIDXResourceViewerTest' }
$result = Join-Path $testRoot 'interactive-singlefile-result.json'
try {
    $script = '__BEMANI_SOURCE_ROOT__\tools\windows\build-singlefile.ps1'
    & $script -ForceColdTest -Architecture $architecture *> (Join-Path $testRoot 'interactive-singlefile.log')
    @{ok=$true;user=[Security.Principal.WindowsIdentity]::GetCurrent().Name} | ConvertTo-Json | Set-Content -Encoding UTF8 $result
} catch {
    @{ok=$false;error=$_.ToString()} | ConvertTo-Json | Set-Content -Encoding UTF8 $result
}
'@
$body = '$architecture=''' + $Architecture + '''; ' + $body
$sourceRoot = if ($env:IIDX_SHARED_PROJECT) { $env:IIDX_SHARED_PROJECT } else { Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }
$body = $body.Replace('__BEMANI_SOURCE_ROOT__', $sourceRoot.Replace("'", "''"))
$body = '$env:IIDX_TEST_ROOT=''' + $testRoot.Replace("'", "''") + '''; ' + $body
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($body))
$name = 'Codex BEMANI Single EXE Test ' + [Guid]::NewGuid().ToString('N')
$action = New-ScheduledTaskAction -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -EncodedCommand ' + $encoded)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
try {
    Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings | Out-Null
    Start-ScheduledTask -TaskName $name
    $deadline = (Get-Date).AddMinutes(9)
    while (!(Test-Path -LiteralPath $result)) {
        if ((Get-Date) -gt $deadline) { throw 'Interactive single EXE verification timed out' }
        Start-Sleep -Milliseconds 250
    }
    if (Test-Path -LiteralPath (Join-Path $testRoot 'interactive-singlefile.log')) { Get-Content -LiteralPath (Join-Path $testRoot 'interactive-singlefile.log') -Tail 60 }
    $report = Get-Content -LiteralPath $result -Raw | ConvertFrom-Json
    $report | ConvertTo-Json
    if (!$report.ok) { throw $report.error }
} finally {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
}
