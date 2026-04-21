@echo off
cd /d "%~dp0"

echo ==================================================
echo   VoiceInputTool
echo ==================================================
echo.

:: Step 1: Check uv
where uv >nul 2>&1
if errorlevel 1 (
    echo [1/4] Installing uv...
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" 2>nul
    if errorlevel 1 (
        echo [ERROR] Failed to install uv
        echo Please install manually: https://docs.astral.sh/uv/getting-started/installation/
        pause
        exit /b 1
    )
    set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
    where uv >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] uv not found after install, please restart terminal
        pause
        exit /b 1
    )
) else (
    echo [1/4] uv ready
)

:: Step 2: Create venv
if not exist .venv (
    echo [2/4] Creating venv (Python 3.11)...
    uv venv --python 3.11
    if errorlevel 1 (
        echo [ERROR] Failed to create venv
        pause
        exit /b 1
    )
) else (
    echo [2/4] venv exists
)

:: Step 3: Install deps
echo [3/4] Installing dependencies...
uv pip install -r requirements.txt -q
uv pip install -r requirements_windows.txt -q

:: FunASR if needed
.venv\Scripts\python -c "import funasr" 2>nul
if errorlevel 1 (
    findstr /i "funasr" config.yaml >nul 2>&1
    if not errorlevel 1 (
        echo Installing FunASR...
        uv pip install funasr modelscope
        if errorlevel 1 (
            echo [WARN] FunASR install failed, will fallback to faster-whisper
        )
    )
)

:: Step 4: Run
echo [4/4] Starting VoiceInputTool...
echo.
.venv\Scripts\python main.py
if errorlevel 1 (
    echo.
    echo [ERROR] Program exited abnormally, check logs/
)
pause
