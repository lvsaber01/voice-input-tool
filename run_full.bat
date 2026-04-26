@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

if not exist .venv (
    echo [ERROR] .venv not found. Please run setup_full.bat first.
    pause
    exit /b 1
)

echo Starting VoiceInputTool (with FunASR/SenseVoice support)...
echo.
.venv\Scripts\python main.py
if errorlevel 1 (
    echo.
    echo [ERROR] Program exited abnormally, check logs/ directory
)
pause
