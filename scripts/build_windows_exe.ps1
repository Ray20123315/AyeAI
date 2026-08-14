param(
    [switch]$Clean,
    [switch]$NoNpuModel
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$BuildVendor = Join-Path $ProjectRoot "build\vendor\ffmpeg"
$DistExe = Join-Path $ProjectRoot "dist\AyeAI.exe"

Push-Location $ProjectRoot
try {
    if (-not (Test-Path $VenvPython)) {
        throw "找不到 .venv；請先執行 scripts\install_windows.ps1"
    }
    & $VenvPython -m pip install -r requirements-build.txt

    if ($Clean) {
        foreach ($Target in @("build", "dist")) {
            $Resolved = Join-Path $ProjectRoot $Target
            if (Test-Path $Resolved) {
                Remove-Item -LiteralPath $Resolved -Recurse -Force
            }
        }
    }
    New-Item -ItemType Directory -Force -Path $BuildVendor | Out-Null

    $Ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
    $Ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
    if ($Ffmpeg -and $Ffprobe) {
        Copy-Item -LiteralPath $Ffmpeg.Source -Destination (Join-Path $BuildVendor "ffmpeg.exe") -Force
        Copy-Item -LiteralPath $Ffprobe.Source -Destination (Join-Path $BuildVendor "ffprobe.exe") -Force
    } else {
        Write-Host "PATH 找不到 FFmpeg，執行 AyeAI runtime auto-download..."
        & $VenvPython -c "from ayeai.bootstrap import ensure_ffmpeg; r=ensure_ffmpeg(True); print(r); raise SystemExit(0 if r.get('ready') else 2)"
        $UserTools = Join-Path $env:LOCALAPPDATA "AyeAI\tools\ffmpeg"
        Copy-Item -LiteralPath (Join-Path $UserTools "ffmpeg.exe") -Destination (Join-Path $BuildVendor "ffmpeg.exe") -Force
        Copy-Item -LiteralPath (Join-Path $UserTools "ffprobe.exe") -Destination (Join-Path $BuildVendor "ffprobe.exe") -Force
    }

    $env:AYEAI_EXE_INCLUDE_NPU_MODEL = if ($NoNpuModel) { "0" } else { "1" }
    & $VenvPython -m PyInstaller --clean --noconfirm --log-level WARN (Join-Path $ProjectRoot "ayeai.spec")
    if (-not (Test-Path $DistExe)) {
        throw "PyInstaller 沒有產生 dist\AyeAI.exe"
    }
    Write-Host "EXE：$DistExe"
    Get-FileHash -Algorithm SHA256 -LiteralPath $DistExe
    & $DistExe --license --lang en
    if ($LASTEXITCODE -ne 0) {
        throw "AyeAI.exe license smoke test failed ($LASTEXITCODE)"
    }
    $HelpLog = Join-Path $ProjectRoot "runtime\AyeAI.exe.help.txt"
    & $DistExe --help *> $HelpLog
    if ($LASTEXITCODE -ne 0) {
        throw "AyeAI.exe help smoke test failed ($LASTEXITCODE)"
    }
    Get-Content -LiteralPath $HelpLog -TotalCount 8
} finally {
    Remove-Item Env:AYEAI_EXE_INCLUDE_NPU_MODEL -ErrorAction SilentlyContinue
    Pop-Location
}
