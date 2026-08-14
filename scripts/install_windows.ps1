param(
    [switch]$SkipNpu
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Push-Location $ProjectRoot
try {
    if (-not (Test-Path $VenvPython)) {
        py -3.12 -m venv .venv
    }
    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install -e ".[dev]"
    & $VenvPython -m pip install -r requirements-cuda.txt
    if (-not $SkipNpu) {
        & $VenvPython -m pip install -r requirements-npu.txt
    }
    Write-Host "AyeAI 安裝完成。下一步："
    Write-Host "  .\.venv\Scripts\python.exe main.py --doctor"
    Write-Host "  .\.venv\Scripts\python.exe main.py --help"
} finally {
    Pop-Location
}
