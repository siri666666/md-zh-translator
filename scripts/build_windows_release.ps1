param(
    [string]$Version = "dev"
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$buildDir = Join-Path $root "build"
$distDir = Join-Path $root "dist"
$artifactsDir = Join-Path $root "artifacts"
$stagingDir = Join-Path $artifactsDir "md-zh-translator-windows-x64"
$entry = Join-Path $root "scripts\\pyinstaller_entry.py"

Write-Host "[1/4] Building standalone exe with PyInstaller..."
Push-Location $root
try {
    pyinstaller `
      --noconfirm `
      --clean `
      --onefile `
      --name md-zh-translator `
      --paths (Join-Path $root "src") `
      $entry | Out-Host
}
finally {
    Pop-Location
}

$exePath = Join-Path $distDir "md-zh-translator.exe"
if (-not (Test-Path $exePath)) {
    throw "Build failed: $exePath not found"
}

Write-Host "[2/4] Preparing release staging directory..."
if (Test-Path $stagingDir) {
    Remove-Item -Recurse -Force $stagingDir
}
New-Item -ItemType Directory -Path $stagingDir | Out-Null

Copy-Item $exePath (Join-Path $stagingDir "md-zh-translator.exe")
Copy-Item (Join-Path $root ".env.example") (Join-Path $stagingDir ".env.example")
Copy-Item (Join-Path $root "README.md") (Join-Path $stagingDir "README.md")
Copy-Item (Join-Path $root "scripts\\RELEASE_USAGE_zh.md") (Join-Path $stagingDir "USAGE_zh.md")

Write-Host "[3/4] Creating zip package..."
if (-not (Test-Path $artifactsDir)) {
    New-Item -ItemType Directory -Path $artifactsDir | Out-Null
}
$zipPath = Join-Path $artifactsDir "md-zh-translator-windows-x64-$Version.zip"
if (Test-Path $zipPath) {
    Remove-Item -Force $zipPath
}
Compress-Archive -Path (Join-Path $stagingDir "*") -DestinationPath $zipPath -CompressionLevel Optimal

Write-Host "[4/4] Done"
Write-Host "EXE: $exePath"
Write-Host "ZIP: $zipPath"
