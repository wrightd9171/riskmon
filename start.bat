@echo off
setlocal
cd /d "%~dp0"

set PY_CMD=
where py >nul 2>&1
if not errorlevel 1 (
    set PY_CMD=py -3
) else (
    where python >nul 2>&1
    if not errorlevel 1 (
        set PY_CMD=python
    )
)

if "%PY_CMD%"=="" (
    echo.
    echo Python was not found on PATH.
    echo Install Python 3.11+ from https://www.python.org/downloads/ (check "Add Python to PATH" during install)
    echo and then re-run this script.
    echo.
    pause
    exit /b 1
)

set VENV_DIR=.venv

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo.
    echo First-time setup: creating virtual environment in %VENV_DIR% ...
    %PY_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo Installing dependencies (this takes a minute) ...
    "%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
    "%VENV_DIR%\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Failed to install dependencies.
        pause
        exit /b 1
    )
    echo Setup complete.
    echo.
)

echo Starting Risk Monitor at http://127.0.0.1:8000/
echo Press Ctrl+C to stop.
echo.
"%VENV_DIR%\Scripts\python.exe" run.py

endlocal
