[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$scriptPath = $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($scriptPath)) {
    throw "Cannot resolve the build script path."
}
$scriptDirectory = Split-Path -Parent $scriptPath
$projectRoot = Split-Path -Parent $scriptDirectory
Set-Location -LiteralPath $projectRoot

# Keep packaging dependencies separate from the development environment.
$buildPython = Join-Path $projectRoot ".build-venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $buildPython)) {
    python -m venv (Join-Path $projectRoot ".build-venv")
}

& $buildPython -m pip install -e "."
& $buildPython -m pip install "pyinstaller>=6.10"

$projectMetadata = Get-Content -LiteralPath (Join-Path $projectRoot "pyproject.toml") -Raw -Encoding UTF8
$versionMatch = [Regex]::Match($projectMetadata, '(?m)^version\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) {
    throw "Cannot read the project version from pyproject.toml."
}
$release = $versionMatch.Groups[1].Value
$distRoot = Join-Path $projectRoot "dist-v$release"
$workRoot = Join-Path $projectRoot "build-v$release"
& $buildPython -m PyInstaller --noconfirm --clean --onedir --windowed --name "NovelAtlasWindows" --distpath $distRoot --workpath $workRoot --add-data "static;static" --add-data "evals/quality_corpus_manifest.json;evals" --hidden-import "app.main" --exclude-module "PyQt5" --exclude-module "PyQt6" --exclude-module "PySide2" --exclude-module "PySide6" launcher.py

$zipPath = Join-Path $distRoot "NovelAtlasWindows-$release.zip"
Compress-Archive -Path (Join-Path $distRoot "NovelAtlasWindows") -DestinationPath $zipPath -Force

Write-Output "Build complete: $(Join-Path $distRoot 'NovelAtlasWindows\NovelAtlasWindows.exe')"
Write-Output "Archive complete: $zipPath"
