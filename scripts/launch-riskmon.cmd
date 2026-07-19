@echo off
REM Thin wrapper — the real logic lives in launch-riskmon.ps1 (robust than batch).
powershell -ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File "%~dp0launch-riskmon.ps1"
