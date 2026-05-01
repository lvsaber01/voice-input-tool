@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0" || (
    echo [ERROR] Failed to change directory.
    pause
    exit /b 1
)

if not exist .venv (
    echo [ERROR] .venv not found.
    echo        Please run setup.bat first to install dependencies.
    echo.
    pause
    exit /b 1
)

if not exist .venv\Scripts\python.exe (
    echo [ERROR] .venv\Scripts\python.exe not found.
    echo        The virtual environment may be corrupted.
    echo        Delete .venv folder and re-run setup.bat.
    echo.
    pause
    exit /b 1
)

if not exist "%~dp0main.py" (
    echo [ERROR] main.py not found in %~dp0
    echo        Please check the installation directory.
    echo.
    pause
    exit /b 1
)

if not exist logs mkdir logs
set "LOGFILE=%~dp0logs\run.log"

echo ================================================== > "%LOGFILE%" 2>&1
echo   VoiceInputTool - Run Log >> "%LOGFILE%" 2>&1
echo   Date: %date% %time% >> "%LOGFILE%" 2>&1
echo ================================================== >> "%LOGFILE%" 2>&1

echo Starting VoiceInputTool...
echo Log: logs\run.log
echo.

.venv\Scripts\python.exe "%~dp0main.py" >> "%LOGFILE%" 2>&1

if errorlevel 1 (
    echo.
    echo [ERROR] Program exited abnormally.
    echo        Check logs\run.log for details.
)

echo.
echo Program exited. >> "%LOGFILE%" 2>&1
pause
endlocal
exit /b 0
