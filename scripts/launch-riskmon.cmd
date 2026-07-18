@echo off
setlocal enabledelayedexpansion
REM Launch the Risk Monitor app: start it (minimized console) if it isn't
REM already on :8000, wait until it's actually listening, then open the browser.
cd /d "%~dp0.."

netstat -ano | findstr ":8000" | findstr "LISTENING" >nul 2>&1
if errorlevel 1 start "Risk Monitor" /min ".venv\Scripts\python.exe" run.py

set /a n=0
:wait
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto ready
set /a n+=1
if !n! geq 40 goto ready
ping -n 2 127.0.0.1 >nul
goto wait

:ready
start "" "http://riskmon:8000"
