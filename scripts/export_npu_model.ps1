param(
    [string]$Model = "openai/whisper-small",
    [string]$Output = "models/whisper-small-int8-ov"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Optimum = Join-Path $ProjectRoot ".venv\Scripts\optimum-cli.exe"
if (-not (Test-Path $Optimum)) {
    throw "找不到 .venv\Scripts\optimum-cli.exe，請先執行 scripts/install_windows.ps1"
}
Push-Location $ProjectRoot
try {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Output) | Out-Null
    if (Test-Path (Join-Path $Output "openvino_encoder_model.xml")) {
        Write-Host "已找到 NPU model，跳過 export：$Output"
        exit 0
    }
    & $Optimum export openvino --trust-remote-code --model $Model --weight-format int8 $Output
    Write-Host "NPU Whisper model 已輸出：$Output"
} finally {
    Pop-Location
}
