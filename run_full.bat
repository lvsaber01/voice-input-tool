@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0" || (
    echo [ERROR] Failed to change directory.
    pause
    exit /b 1
)

set "LOGFILE=%~dp0run_log.txt"

if not exist .venv (
    echo [ERROR] .venv not found.
    echo        Please run setup_full.bat first to install dependencies.
    echo.
    pause
    exit /b 1
)

if not exist .venv\Scripts\python.exe (
    echo [ERROR] .venv\Scripts\python.exe not found.
    echo        The virtual environment may be corrupted.
    echo        Delete .venv folder and re-run setup_full.bat.
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

echo ================================================== > "%LOGFILE%" 2>&1
echo   VoiceInputTool Run Log >> "%LOGFILE%" 2>&1
echo   Date: %date% %time% >> "%LOGFILE%" 2>&1
echo ================================================== >> "%LOGFILE%" 2>&1

echo Starting VoiceInputTool (with FunASR/SenseVoice)...
echo Log file: %LOGFILE%
echo.
.venv\Scripts\python.exe "%~dp0main.py" >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Program exited with error.
    echo         Check %LOGFILE% for details.
    echo         You can send %LOGFILE% for diagnosis.
)
echo.
echo Program exited. >> "%LOGFILE%" 2>&1
pause
endlocal
exit /b 0
