@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set LOGFILE=run_log.txt

if not exist .venv (
    echo [ERROR] .venv not found.
    echo        Please run setup_full.bat first to install dependencies.
    echo.
    pause
    exit /b 1
)

echo ================================================== > %LOGFILE% 2>&1
echo   VoiceInputTool Run Log >> %LOGFILE% 2>&1
echo   Date: %date% %time% >> %LOGFILE% 2>&1
echo ================================================== >> %LOGFILE% 2>&1

echo Starting VoiceInputTool (with FunASR/SenseVoice)...
echo Log file: %LOGFILE%
echo.
.venv\Scripts\python main.py >> %LOGFILE% 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Program exited with error.
    echo         Check %LOGFILE% for details.
    echo         You can send %LOGFILE% for diagnosis.
)
echo.
echo Program exited. >> %LOGFILE% 2>&1
pause
