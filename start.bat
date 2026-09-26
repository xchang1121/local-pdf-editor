@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 run.py %*
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python not found. Install Python 3.10-3.13 and enable Add Python to PATH.
        pause
        exit /b 1
    )
    python run.py %*
)
if errorlevel 1 (
    echo.
    echo Startup failed. See README.md for manual setup.
    pause
    exit /b 1
)
endlocal
