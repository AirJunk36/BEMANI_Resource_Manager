$testRoot = Join-Path $env:LOCALAPPDATA 'IIDXResourceViewerTest'
foreach ($name in @('native-error.log','singlefile-build.log','singlefile-tests\first\launcher.log','singlefile-tests\first\launcher-error.log','singlefile-tests\first\native-error.log','singlefile-tests\first\native-smoke.json','singlefile-tests\cached\launcher.log','singlefile-tests\cached\launcher-error.log','singlefile-tests\cached\native-error.log','singlefile-tests\cached\native-smoke.json')) {
    $path = Join-Path $testRoot $name
    if (Test-Path -LiteralPath $path) { Write-Output $name; Get-Content -LiteralPath $path -Tail 30 }
}
Get-ChildItem -LiteralPath (Join-Path $testRoot 'singlefile-tests\first') | Select-Object Name,LastWriteTime
Get-ChildItem -LiteralPath $env:LOCALAPPDATA -Directory | Where-Object { $_.Name -like 'BEMANI*' } | ForEach-Object {
    Get-ChildItem -LiteralPath (Join-Path $_.FullName 'runtime') -Directory | Select-Object Name,LastWriteTime
}
Get-WinEvent -FilterHashtable @{LogName='Application';StartTime=(Get-Date).AddMinutes(-8)} -ErrorAction SilentlyContinue |
    Where-Object { $_.ProviderName -in @('Application Error','.NET Runtime','Windows Error Reporting') } |
    Select-Object -First 8 TimeCreated,ProviderName,Message | Format-List
