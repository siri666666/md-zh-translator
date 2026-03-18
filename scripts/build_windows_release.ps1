param(
    [string]$Version = "dev"
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $root
try {
    python .\scripts\build_release.py --version $Version --platform windows-x64
}
finally {
    Pop-Location
}
