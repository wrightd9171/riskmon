@echo off
REM Launch the Risk Monitor app: start it (windowless) if it isn't already
REM listening on :8000, then open it in the default browser.
cd /d "%~dp0.."
netstat -ano | findstr "LISTENING" | findstr ":8000" >nul 2>&1
if not %errorlevel%==0 (
  start "" "%~dp0..\.venv\Scripts\pythonw.exe" run.py
  timeout /t 3 /nobreak >nul
)
start "" "http://riskmon:8000"
