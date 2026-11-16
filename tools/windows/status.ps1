$testRoot = Join-Path $env:LOCALAPPDATA 'IIDXResourceViewerTest'
Get-ChildItem $testRoot -ErrorAction SilentlyContinue | Select-Object Name,Length,LastWriteTime
foreach ($name in @('build.log','python-tests.log','native-smoke.json','native.stderr.log')) {
    $path = Join-Path $testRoot $name
    if (Test-Path $path) { Write-Output ('--- ' + $name); Get-Content $path -Tail 28 }
}
