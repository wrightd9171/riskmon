# Registers a Windows Task Scheduler job that sends the Risk Monitor portfolio
# digest via Pushover on a weekly cadence, independent of the app.
#
#   powershell -ExecutionPolicy Bypass -File scripts\register-notify-task.ps1
#
# The task must decrypt your Pushover keys unattended, so it needs your master
# password on disk. This writes it to a file under %LOCALAPPDATA%\riskmon
# (NOT OneDrive, NOT the repo), locked to your user account. That is the
# security tradeoff of scheduling outside the unlocked app.
param(
  [string]$TaskName = "RiskMonitor Portfolio Digest"
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$Sender = Join-Path $ProjectDir "send_digest.py"
if (-not (Test-Path $Python)) { throw "venv python not found at $Python" }
if (-not (Test-Path $Sender)) { throw "send_digest.py not found at $Sender" }

Write-Host "Risk Monitor - register weekly Pushover digest task" -ForegroundColor Cyan
Write-Host "Project: $ProjectDir"

$validDays = "Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"
$day = Read-Host "Day of week to send (default Sunday)"
if ([string]::IsNullOrWhiteSpace($day)) { $day = "Sunday" }
if ($validDays -notcontains $day) { throw "Invalid day: $day" }
$time = Read-Host "Time HH:mm 24h (default 08:00)"
if ([string]::IsNullOrWhiteSpace($time)) { $time = "08:00" }

Write-Host ""
Write-Host "Your master password will be stored on disk so the task can decrypt" -ForegroundColor Yellow
Write-Host "your Pushover keys unattended (LOCALAPPDATA, user-only ACL)." -ForegroundColor Yellow
$secure = Read-Host "Master password" -AsSecureString
$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
  [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))

$cfgDir = Join-Path $env:LOCALAPPDATA "riskmon"
New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
$pwFile = Join-Path $cfgDir "notify-password.txt"
Set-Content -Path $pwFile -Value $plain -NoNewline -Encoding UTF8
icacls $pwFile /inheritance:r /grant:r "$($env:USERNAME):(R)" | Out-Null

$wrapper = Join-Path $cfgDir "run-notify.cmd"
@"
@echo off
set "RISKMON_MASTER_PASSWORD_FILE=$pwFile"
cd /d "$ProjectDir"
"$Python" "$Sender" >> "$cfgDir\notify.log" 2>&1
"@ | Set-Content -Path $wrapper -Encoding ASCII

$action   = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$wrapper`""
$trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $day -At $time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
  -Settings $settings -Description "Sends the Risk Monitor portfolio digest via Pushover." -Force | Out-Null

Write-Host ""
Write-Host "Registered '$TaskName' - $day at $time weekly." -ForegroundColor Green
Write-Host "Password file: $pwFile"
Write-Host "Log file:      $cfgDir\notify.log"
Write-Host ""
Write-Host "Send once now to test:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Remove the task:        Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
