@echo off
REM Launch mlrVp on Windows
REM Checks for venv first, creates one if it doesn't exist

if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -m mlrvp
    if errorlevel 1 pause
    exit /b %errorlevel%
) else if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe -m mlrvp
    if errorlevel 1 pause
    exit /b %errorlevel%
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Python is not installed or not in PATH.
        echo Please install Python 3.10+ from https://www.python.org/downloads/
        echo Make sure to check "Add Python to PATH" during installation.
        pause
        exit /b 1
    )
    echo No virtual environment found. Creating one...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo Installing dependencies...
    .venv\Scripts\pip.exe install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies.
        pause
        exit /b 1
    )
    echo.
    echo Starting mlrVp...
    .venv\Scripts\python.exe -m mlrvp
    if errorlevel 1 pause
)
