[CmdletBinding(SupportsShouldProcess)]
param([string]$TestRoot = $(if ($env:IIDX_TEST_ROOT) { $env:IIDX_TEST_ROOT } else { Join-Path $env:LOCALAPPDATA 'IIDXResourceViewerTest' }))
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($TestRoot).TrimEnd('\')
$paths = Get-Content -LiteralPath (Join-Path $root 'paths.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ([IO.Path]::GetFullPath($paths.home).TrimEnd('\') -ine (Join-Path $root 'app')) { throw 'Not a recognized BEMANI test directory' }
$keep = @()
$portable = Join-Path $root 'portable-bundle.json'
if (Test-Path -LiteralPath $portable) {
    $bundle = Get-Content -LiteralPath $portable -Raw -Encoding UTF8 | ConvertFrom-Json
    $keep = @($bundle.bundle,$bundle.archive) | ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\') }
}
$active = @(Get-Process | ForEach-Object { try { if ($_.Path) { $_.Path } } catch {} })
$removed = @(); [long]$bytes = 0
foreach ($item in Get-ChildItem -LiteralPath $root -Force) {
    $candidate = if ($item.PSIsContainer) { $item.Name -match '^p-\d{6}$' } else {
        $item.Name -match '^IIDXResourceViewer-Windows-(x64|ARM64)-\d{14}\.zip$' -or
        $item.Name -match '^(song|s3p|manual|animation)-[0-9a-f-]+\.(mp4|wav|gif|ifs)$' -or
        ($item.Name -eq 'dotnet-sdk.zip' -and (Test-Path -LiteralPath $paths.dotnet))
    }
    if (!$candidate -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { continue }
    $prefix = $item.FullName.TrimEnd('\') + '\'
    if ($keep | Where-Object { $_ -ieq $item.FullName -or $_.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase) }) { continue }
    if ($active | Where-Object { $_ -ieq $item.FullName -or $_.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase) }) { continue }
    if ($PSCmdlet.ShouldProcess($item.FullName,'Remove obsolete test files')) {
        $size = if ($item.PSIsContainer) { (Get-ChildItem -LiteralPath $item.FullName -File -Recurse -Force | Measure-Object Length -Sum).Sum } else { $item.Length }
        Remove-Item -LiteralPath $item.FullName -Recurse -Force
        $bytes += [long]$size; $removed += $item.Name
    }
}
$report = [ordered]@{ removed = $removed; bytes_freed = $bytes; kept_latest_bundle = $keep }
$report | ConvertTo-Json -Depth 4
if (!$WhatIfPreference) { $report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $root 'cleanup-report.json') -Encoding UTF8 }
