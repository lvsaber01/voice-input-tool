@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set LOGFILE=setup_log.txt
echo ================================================== > %LOGFILE% 2>&1
echo   VoiceInputTool - Full Setup Log >> %LOGFILE% 2>&1
echo   Date: %date% %time% >> %LOGFILE% 2>&1
echo ================================================== >> %LOGFILE% 2>&1
echo.

echo ==================================================
echo   VoiceInputTool - Full Setup (FunASR + SenseVoice)
echo ==================================================
echo   Log file: %LOGFILE%
echo   If the window closes, check %LOGFILE% for details.
echo.

:: Step 1: Check or install uv
echo [1/5] Checking uv...
echo [1/5] Checking uv... >> %LOGFILE% 2>&1
where uv >nul 2>&1
if errorlevel 1 (
    echo        Installing uv...
    echo        Installing uv... >> %LOGFILE% 2>&1
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" >> %LOGFILE% 2>&1
    set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
    where uv >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] uv install failed. Check %LOGFILE%
        echo [ERROR] uv install failed. >> %LOGFILE% 2>&1
        goto :failed
    )
)
echo        uv OK
echo        uv OK >> %LOGFILE% 2>&1

:: Step 2: Create venv with Python 3.11
echo [2/5] Setting up Python 3.11 venv...
echo [2/5] Setting up Python 3.11 venv... >> %LOGFILE% 2>&1
if exist .venv (
    echo        venv exists, reusing
    echo        venv exists, reusing >> %LOGFILE% 2>&1
) else (
    echo        Downloading Python 3.11 and creating venv...
    echo        Downloading Python 3.11 and creating venv... >> %LOGFILE% 2>&1
    uv venv --python 3.11 >> %LOGFILE% 2>&1
    if errorlevel 1 (
        echo [ERROR] Failed to create venv. Check %LOGFILE%
        echo [ERROR] Failed to create venv. >> %LOGFILE% 2>&1
        goto :failed
    )
)

:: Step 3: Install core dependencies
echo [3/5] Installing core dependencies...
echo [3/5] Installing core dependencies... >> %LOGFILE% 2>&1
uv pip install -r requirements.txt >> %LOGFILE% 2>&1
uv pip install -r requirements_windows.txt >> %LOGFILE% 2>&1
if errorlevel 1 (
    echo [WARN] Some core deps failed, trying pip fallback...
    echo [WARN] Some core deps failed, trying pip fallback... >> %LOGFILE% 2>&1
    .venv\Scripts\python -m pip install -r requirements.txt >> %LOGFILE% 2>&1
    .venv\Scripts\python -m pip install -r requirements_windows.txt >> %LOGFILE% 2>&1
)

:: Step 4: Install FunASR + torch (CPU only)
echo [4/5] Installing torch + FunASR (may take 5-10 min)...
echo [4/5] Installing torch + FunASR... >> %LOGFILE% 2>&1
echo        - torch (CPU, ~200MB)...
echo        - torch (CPU)... >> %LOGFILE% 2>&1
uv pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu >> %LOGFILE% 2>&1
echo        - funasr + modelscope...
echo        - funasr + modelscope... >> %LOGFILE% 2>&1
uv pip install funasr modelscope >> %LOGFILE% 2>&1

if errorlevel 1 (
    echo [WARN] uv install had issues, trying pip fallback...
    echo [WARN] uv install had issues, trying pip fallback... >> %LOGFILE% 2>&1
    .venv\Scripts\python -m pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu >> %LOGFILE% 2>&1
    .venv\Scripts\python -m pip install funasr modelscope >> %LOGFILE% 2>&1
)

:: Step 5: Verify
echo [5/5] Verifying installation...
echo [5/5] Verifying installation... >> %LOGFILE% 2>&1
.venv\Scripts\python -c "from faster_whisper import WhisperModel; print('  [OK] faster-whisper')" >> %LOGFILE% 2>&1
.venv\Scripts\python -c "from faster_whisper import WhisperModel; print('  [OK] faster-whisper')"

.venv\Scripts\python -c "import funasr; print('  [OK] funasr')" >> %LOGFILE% 2>&1
.venv\Scripts\python -c "import funasr; print('  [OK] funasr')"

.venv\Scripts\python -c "import torch; print('  [OK] torch', torch.__version__)" >> %LOGFILE% 2>&1
.venv\Scripts\python -c "import torch; print('  [OK] torch', torch.__version__)"

echo.
echo ==================================================
echo   Setup complete!
echo.
echo   Next: double-click run_full.bat to start
echo   Log:  %LOGFILE%
echo ==================================================
echo.
echo Setup complete! >> %LOGFILE% 2>&1
pause
exit /b 0

:failed
echo.
echo ==================================================
echo   Setup FAILED!
echo   Check %LOGFILE% for error details.
echo   You can send %LOGFILE% for diagnosis.
echo ==================================================
echo.
echo Setup FAILED! >> %LOGFILE% 2>&1
pause
exit /b 1
