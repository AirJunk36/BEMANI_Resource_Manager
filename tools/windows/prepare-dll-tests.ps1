$ErrorActionPreference='Stop'
$testRoot=Join-Path $env:LOCALAPPDATA 'IIDXResourceViewerTest'
$paths=Get-Content (Join-Path $testRoot 'paths.json') -Raw | ConvertFrom-Json
$shared=$env:IIDX_GAME_ROOT
if (!$shared) { throw 'Set IIDX_GAME_ROOT to your own game directory' }
$files=@('33_Sparkle_Shower/afp-core.dll','33_Sparkle_Shower/avs2-core.dll','33_Sparkle_Shower/afp-utils.dll','33_Sparkle_Shower/bm2dx.dll','33_Sparkle_Shower/data/graphic/0/title.ifs')
foreach($relative in $files){
    $source=Join-Path $shared $relative
    $target=Join-Path $paths.data $relative
    $before=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
    Copy-Item -LiteralPath $source -Destination $target -Force
    if((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne $before -or (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $before){throw 'DLL fixture copy/hash failed'}
}
Write-Output ('Verified DLL inspection fixture copies: '+$files.Count)
