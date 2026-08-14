$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$SourceFile = Join-Path $ProjectRoot "runtime\recovery input\long_stream.mp4"
$source = Get-ChildItem (Join-Path $ProjectRoot "runtime\recovery input") -Filter "*.mp4" | Select-Object -First 1
if (-not (Test-Path $SourceFile) -and $source) {
    Copy-Item -LiteralPath $source.FullName -Destination $SourceFile
}
$JobDir = Join-Path $ProjectRoot "runtime\recovery_job_v4"
$Stdout = Join-Path $ProjectRoot "runtime\recovery-run-v4.out.log"
$Stderr = Join-Path $ProjectRoot "runtime\recovery-run-v4.err.log"

$ArgumentList = @(
    "main.py", "--backend", "cpu", "--no-auto-tune", "--no-startup-probe",
    "--chunk-seconds", "30", "--overlap-seconds", "4", "--max-retries", "0",
    "--cpu-threads", "1", "--output-dir", $JobDir, "--json", ('"' + $SourceFile + '"')
)
$Runner = Start-Process -FilePath $Python -ArgumentList $ArgumentList -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr

for ($index = 0; $index -lt 60 -and -not (Test-Path (Join-Path $JobDir "state.db")) -and -not $Runner.HasExited; $index++) {
    Start-Sleep -Milliseconds 250
}
if ($Runner.HasExited) {
    throw "runner exited before creating state.db"
}
Start-Sleep -Seconds 2
$pauseResult = & $Python main.py --pause $JobDir --json
Start-Sleep -Seconds 2
$pausedStatus = & $Python main.py --status $JobDir --json
$resumeResult = & $Python main.py --resume $JobDir --json
Start-Sleep -Seconds 2
$wasKilled = $false
Start-Sleep -Seconds 5
$runningSeen = -not $Runner.HasExited
if ($runningSeen) {
    Stop-Process -Id $Runner.Id -Force
    $wasKilled = $true
}
$recoveryResult = & $Python main.py --backend cpu --no-auto-tune --no-startup-probe --chunk-seconds 30 --overlap-seconds 4 --max-retries 0 --cpu-threads 1 --output-dir $JobDir --json $SourceFile
$finalStatus = & $Python main.py --status $JobDir --json

Write-Output ("RUNNING_CHUNK_SEEN=" + $runningSeen)
Write-Output ("FORCED_PROCESS_KILL=" + $wasKilled)
Write-Output "PAUSE_COMMAND_RESULT"
$pauseResult
Write-Output "STATUS_WHILE_PAUSED"
$pausedStatus
Write-Output "RESUME_COMMAND_RESULT"
$resumeResult
Write-Output "RECOVERY_RUN_RESULT"
$recoveryResult
Write-Output "FINAL_STATUS"
$finalStatus
Write-Output "RUN_STDOUT_TAIL"
Get-Content $Stdout -Tail 40
Write-Output "RUN_STDERR_TAIL"
Get-Content $Stderr -Tail 40
