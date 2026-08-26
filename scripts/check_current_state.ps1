$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectRoot "docs/current-state.json"
$state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
$pyproject = Get-Content -LiteralPath (Join-Path $projectRoot "pyproject.toml") -Raw -Encoding UTF8
$index = Get-Content -LiteralPath (Join-Path $projectRoot "static/index.html") -Raw -Encoding UTF8
$main = Get-Content -LiteralPath (Join-Path $projectRoot "app/main.py") -Raw -Encoding UTF8
$appJs = Get-Content -LiteralPath (Join-Path $projectRoot "static/app.js") -Raw -Encoding UTF8

$release = [Regex]::Escape([string]$state.release)
$checks = [ordered]@{
    "pyproject version" = $pyproject -match "version\s*=\s*`"$release`""
    "page version" = $index -match "Novel Atlas[^<]*$release"
    "API version" = $main -match "version=`"$release`""
    "single chronology source" = $appJs -match "function storyMapSteps\(\)"
    "2D map entry" = $appJs -match 'data-mode="2d"'
    "3D map entry" = $appJs -match 'data-mode="3d"'
    "3D reuses directional coordinates" = $appJs -match "function createMapGraph3D\(" -and $appJs -match "mapContainmentDepths\("
    "view switch avoids model calls" = $appJs -match "novel-atlas-map-mode"
}

$failed = @($checks.GetEnumerator() | Where-Object { -not $_.Value })
$checks.GetEnumerator() | ForEach-Object {
    $mark = if ($_.Value) { "PASS" } else { "FAIL" }
    Write-Output "$mark`t$($_.Key)"
}
if ($failed.Count -gt 0) {
    throw "Current-state check failed: $($failed.Key -join ', ')"
}
Write-Output "Current state matches the 2.6.0 contract."
