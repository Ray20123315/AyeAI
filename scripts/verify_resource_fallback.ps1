$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$SourceFile = Join-Path $ProjectRoot "runtime\recovery input\long_stream.mp4"
$JobDir = Join-Path $ProjectRoot "runtime\resource_job"
$Stdout = Join-Path $ProjectRoot "runtime\resource-run.out.log"
$Stderr = Join-Path $ProjectRoot "runtime\resource-run.err.log"
$BusyFlag = Join-Path $JobDir "RESOURCE_BUSY"

$ArgumentList = @(
    "main.py", "--no-auto-tune", "--chunk-seconds", "30", "--overlap-seconds", "4",
    "--max-retries", "0", "--output-dir", $JobDir, "--json", ('"' + $SourceFile + '"')
)
$Runner = Start-Process -FilePath $Python -ArgumentList $ArgumentList -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr
for ($index = 0; $index -lt 120 -and -not (Test-Path (Join-Path $JobDir "state.db")) -and -not $Runner.HasExited; $index++) {
    Start-Sleep -Milliseconds 250
}
if ($Runner.HasExited) {
    throw "runner exited before creating state.db"
}
$cudaSeen = $false
for ($index = 0; $index -lt 240 -and -not $Runner.HasExited; $index++) {
    if (Test-Path $Stdout) {
        $text = Get-Content $Stdout -Raw
        if ($text -match "backend=cuda") {
            $cudaSeen = $true
            break
        }
    }
    Start-Sleep -Milliseconds 250
}
if (-not $cudaSeen) {
    throw "did not observe the first CUDA chunk"
}
New-Item -ItemType File -Force -Path $BusyFlag | Out-Null
Wait-Process -Id $Runner.Id -Timeout 300
Remove-Item -LiteralPath $BusyFlag -Force -ErrorAction SilentlyContinue
$finalStatus = & $Python main.py --status $JobDir --json

Write-Output ("CUDA_SEEN=" + $cudaSeen)
Write-Output ("NPU_SEEN=" + ((Get-Content $Stdout -Raw) -match "backend=npu"))
Write-Output "FINAL_STATUS"
$finalStatus
Write-Output "RUN_STDOUT_TAIL"
Get-Content $Stdout -Tail 60
Write-Output "RUN_STDERR_TAIL"
Get-Content $Stderr -Tail 30
