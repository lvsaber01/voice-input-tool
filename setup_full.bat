@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo ==================================================
echo   VoiceInputTool - Full Setup (FunASR + SenseVoice)
echo ==================================================
echo.

:: Step 1: Check or install uv (for isolated Python 3.11)
where uv >nul 2>&1
if errorlevel 1 (
    echo [1/5] Installing uv (Python package manager)...
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
    where uv >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] uv install failed. Please install manually: https://docs.astral.sh/uv/getting-started/installation/
        pause
        exit /b 1
    )
) else (
    echo [1/5] uv OK
)

:: Step 2: Create venv with Python 3.11 (uv handles download automatically)
if exist .venv (
    echo [2/5] venv exists, updating...
) else (
    echo [2/5] Creating Python 3.11 venv (uv will download Python if needed)...
    uv venv --python 3.11
    if errorlevel 1 (
        echo [ERROR] Failed to create venv
        pause
        exit /b 1
    )
)

:: Step 3: Install core dependencies
echo [3/5] Installing core dependencies (faster-whisper)...
uv pip install -r requirements.txt
uv pip install -r requirements_windows.txt

:: Step 4: Install FunASR + torch (CPU only, smaller)
echo [4/5] Installing FunASR + SenseVoice dependencies (this may take a few minutes)...
uv pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu
uv pip install funasr modelscope

if errorlevel 1 (
    echo [WARN] FunASR install had issues, trying fallback...
    .venv\Scripts\python -m pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu
    .venv\Scripts\python -m pip install funasr modelscope
)

:: Step 5: Verify
echo [5/5] Verifying installation...
.venv\Scripts\python -c "from faster_whisper import WhisperModel; print('  faster-whisper OK')"
.venv\Scripts\python -c "import funasr; print('  funasr OK')"
.venv\Scripts\python -c "import torch; print('  torch', torch.__version__)"

echo.
echo ==================================================
echo   Setup complete!
echo.
echo   Run:  run_full.bat   (to start with FunASR support)
echo   Or:   start.bat      (to start with faster-whisper only)
echo ==================================================
echo.
pause
