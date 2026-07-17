# Registers a Windows Task Scheduler job that runs the Risk Monitor web app at
# logon, owned by the OS so it survives your terminal closing and reboots.
# Windowless (pythonw), auto-restarts on failure, no run-time limit. Also starts
# it immediately.
#
#   powershell -ExecutionPolicy Bypass -File scripts\register-app-task.ps1
#
# Remove later with:
#   Unregister-ScheduledTask -TaskName 'RiskMonitor App' -Confirm:$false
param([string]$TaskName = "RiskMonitor App")
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$pyw = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"
$py  = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$exe = if (Test-Path $pyw) { $pyw } else { $py }
if (-not (Test-Path $exe)) { throw "venv python not found under $ProjectDir\.venv" }

Write-Host "Risk Monitor - register app auto-start task" -ForegroundColor Cyan
Write-Host "Project: $ProjectDir"
Write-Host "Runs:    $exe run.py"

# Free port 8000 if an instance is already listening (e.g. a manual run).
try {
  Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction Stop | ForEach-Object {
    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
  }
  Write-Host "Stopped an existing listener on :8000"
} catch { Write-Host "Port 8000 is free" }
Start-Sleep -Seconds 1

$action   = New-ScheduledTaskAction -Execute $exe -Argument "run.py" -WorkingDirectory $ProjectDir
$trigger  = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
  -Description "Runs the Risk Monitor web app on http://riskmon:8000" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

$up = $false
foreach ($i in 1..20) {
  try {
    Invoke-WebRequest -Uri "http://127.0.0.1:8000/" -UseBasicParsing -MaximumRedirection 0 -TimeoutSec 2 | Out-Null
    $up = $true; break
  } catch {
    if ($_.Exception.Response) { $up = $true; break }   # a redirect counts as "up"
    Start-Sleep -Milliseconds 500
  }
}

Write-Host ""
if ($up) {
  Write-Host "App is up at http://riskmon:8000" -ForegroundColor Green
} else {
  Write-Host "Task registered but the app didn't answer on :8000 yet - check Task Scheduler." -ForegroundColor Yellow
}
Write-Host "It now auto-starts at each logon."
Write-Host "Remove: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
