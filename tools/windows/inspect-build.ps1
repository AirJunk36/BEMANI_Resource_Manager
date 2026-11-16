$testRoot = Join-Path $env:LOCALAPPDATA 'IIDXResourceViewerTest'
$nuget = Join-Path $testRoot 'nuget'
Get-ChildItem $nuget -Recurse -Filter '*Pri.Tasks*' -ErrorAction SilentlyContinue | Select-Object FullName
$target = Join-Path $nuget 'microsoft.windowsappsdk\1.6.250205002\buildTransitive\MrtCore.PriGen.targets'
Select-String -Path $target -Pattern 'PriTasks','Pri.Tasks','PriTask','AppxMSBuild' -Context 3,3 | Select-Object -First 18
$versions = Invoke-RestMethod -Uri 'https://api.nuget.org/v3-flatcontainer/microsoft.windows.sdk.buildtools.msix/index.json'
$versions.versions | Select-Object -Last 8
