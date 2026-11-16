$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$sharedProject = if ($env:IIDX_SHARED_PROJECT) { $env:IIDX_SHARED_PROJECT } else { Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }
$sharedGameRoot = if ($env:IIDX_GAME_ROOT) { $env:IIDX_GAME_ROOT } else { Split-Path $sharedProject }
$testRoot = Join-Path $env:LOCALAPPDATA 'IIDXResourceViewerTest'
$appRoot = Join-Path $testRoot 'app'
$sdkRoot = Join-Path $testRoot 'dotnet'
$pythonRoot = Join-Path $appRoot '.venv\Scripts'
$sitePackages = Join-Path $appRoot '.venv\Lib\site-packages'
$dataRoot = Join-Path $testRoot 'TestData'
New-Item -ItemType Directory -Force -Path $testRoot,$appRoot,$pythonRoot,$sitePackages,$dataRoot | Out-Null
foreach ($item in @('backend.py','main.py','requirements.txt','LICENSE','COPYRIGHT','THIRD_PARTY_NOTICES.md','VERSION','iidxviewer','native','tests','tools','start_windows.bat','README.md')) {
    Copy-Item -LiteralPath (Join-Path $sharedProject $item) -Destination $appRoot -Recurse -Force
}
Write-Output ('COPIED_SOURCE=' + $appRoot)

if (!(Test-Path (Join-Path $sdkRoot 'dotnet.exe'))) {
    $metadata = Invoke-RestMethod -Uri 'https://dotnetcli.blob.core.windows.net/dotnet/release-metadata/8.0/releases.json'
    $sdk = $metadata.releases | ForEach-Object { $_.sdks } | Where-Object { $_.version -eq $metadata.'latest-sdk' } | Select-Object -First 1
    $archive = $sdk.files | Where-Object { $_.rid -eq 'win-arm64' -and $_.name.EndsWith('.zip') } | Select-Object -First 1
    if (!$archive) { throw 'Official ARM64 .NET SDK archive was not found' }
    Write-Output ('DOWNLOADING_DOTNET=' + $sdk.version)
    $download = Join-Path $testRoot 'dotnet-sdk.zip'
    Invoke-WebRequest -UseBasicParsing -Uri $archive.url -OutFile $download
    if ((Get-FileHash $download -Algorithm SHA512).Hash -ne $archive.hash) { throw 'SDK hash mismatch' }
    Expand-Archive -Path $download -DestinationPath $sdkRoot -Force
}
Write-Output ('DOTNET=' + (& (Join-Path $sdkRoot 'dotnet.exe') --version))

# x64 Python runs under Windows ARM's x64 emulation, which also makes the
# native IFS extension wheels available. The WinUI process itself is ARM64.
$python = Join-Path $pythonRoot 'python.exe'
if (!(Test-Path $python)) {
    Write-Output 'DOWNLOADING_PYTHON=3.13.9-x64'
    $download = Join-Path $testRoot 'python-embed.zip'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.13.9/python-3.13.9-embed-amd64.zip' -OutFile $download
    Expand-Archive -Path $download -DestinationPath $pythonRoot -Force
}
$pth = Join-Path $pythonRoot 'python313._pth'
@('python313.zip','.', '..\Lib\site-packages','..\..','import site') | Set-Content -Path $pth -Encoding ASCII
if (!(Test-Path (Join-Path $sitePackages 'pip'))) {
    $bootstrap = Join-Path $testRoot 'get-pip.py'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $bootstrap
    & $python $bootstrap --target $sitePackages --no-warn-script-location
    if ($LASTEXITCODE) { throw 'pip bootstrap failed' }
}
& $python -m pip install --disable-pip-version-check --no-warn-script-location --target $sitePackages -r (Join-Path $appRoot 'requirements.txt')
if ($LASTEXITCODE) { throw 'Python dependency install failed' }

$fixtures = Get-Content (Join-Path $sharedProject 'tools\windows\fixtures.json') -Raw | ConvertFrom-Json
foreach ($relative in $fixtures) {
    $destination = Join-Path $dataRoot $relative
    New-Item -ItemType Directory -Force -Path (Split-Path $destination) | Out-Null
    Copy-Item -LiteralPath (Join-Path $sharedGameRoot $relative) -Destination $destination -Force
}
Write-Output ('FIXTURES=' + $fixtures.Count)
@{home=$appRoot; data=$dataRoot; dotnet=(Join-Path $sdkRoot 'dotnet.exe'); python=$python} | ConvertTo-Json | Set-Content (Join-Path $testRoot 'paths.json') -Encoding UTF8
Write-Output 'TEST_ENVIRONMENT_READY'
