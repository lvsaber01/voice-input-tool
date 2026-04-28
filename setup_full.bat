@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0" || (
    echo [ERROR] Failed to change directory.
    pause
    exit /b 1
)

set "LOGFILE=%~dp0setup_log.txt"
echo ================================================== > "%LOGFILE%" 2>&1
echo   VoiceInputTool - Full Setup Log >> "%LOGFILE%" 2>&1
echo   Date: %date% %time% >> "%LOGFILE%" 2>&1
echo ================================================== >> "%LOGFILE%" 2>&1
echo.

echo ==================================================
echo   VoiceInputTool - Full Setup (FunASR + SenseVoice)
echo ==================================================
echo   Log file: %LOGFILE%
echo   If the window closes, check %LOGFILE% for details.
echo.

:: Step 1: Check or install uv
echo [1/6] Checking uv...
echo [1/6] Checking uv... >> "%LOGFILE%" 2>&1
where uv >nul 2>&1
if errorlevel 1 (
    echo        Installing uv...
    echo        Installing uv... >> "%LOGFILE%" 2>&1
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" >> "%LOGFILE%" 2>&1
    set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
    where uv >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] uv install failed. Check %LOGFILE%
        echo [ERROR] uv install failed. >> "%LOGFILE%" 2>&1
        goto :failed
    )
)
echo        uv OK
echo        uv OK >> "%LOGFILE%" 2>&1

:: Step 2: Create venv with Python 3.11
echo [2/6] Setting up Python 3.11 venv...
echo [2/6] Setting up Python 3.11 venv... >> "%LOGFILE%" 2>&1
if exist .venv (
    echo        venv exists, reusing
    echo        venv exists, reusing >> "%LOGFILE%" 2>&1
) else (
    echo        Downloading Python 3.11 and creating venv...
    echo        Downloading Python 3.11 and creating venv... >> "%LOGFILE%" 2>&1
    uv venv --python 3.11 >> "%LOGFILE%" 2>&1
    if errorlevel 1 (
        echo [ERROR] Failed to create venv. Check %LOGFILE%
        echo        Common causes: no internet, proxy issues, disk full.
        echo [ERROR] Failed to create venv. >> "%LOGFILE%" 2>&1
        goto :failed
    )
)

:: Step 3: Ensure pip is available in the venv
echo [3/6] Ensuring pip in venv...
echo [3/6] Ensuring pip in venv... >> "%LOGFILE%" 2>&1
.venv\Scripts\python.exe -c "import pip" >nul 2>&1
if errorlevel 1 (
    echo        pip not found in venv, installing...
    echo        pip not found in venv, installing... >> "%LOGFILE%" 2>&1
    uv pip install pip >> "%LOGFILE%" 2>&1
    if errorlevel 1 (
        echo [WARN] Could not install pip via uv, trying ensurepip...
        echo [WARN] Could not install pip via uv... >> "%LOGFILE%" 2>&1
        .venv\Scripts\python.exe -m ensurepip --default-pip >> "%LOGFILE%" 2>&1
    )
)
.venv\Scripts\python.exe -c "import pip" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pip is not available in venv and could not be installed.
    echo        Try manually: .venv\Scripts\python.exe -m ensurepip
    echo [ERROR] pip not available. >> "%LOGFILE%" 2>&1
    goto :failed
)
echo        pip OK
echo        pip OK >> "%LOGFILE%" 2>&1

:: Step 4: Install core dependencies
echo [4/6] Installing core dependencies...
echo [4/6] Installing core dependencies... >> "%LOGFILE%" 2>&1

set "UV_CORE1_ERR=0"
set "UV_CORE2_ERR=0"

uv pip install -r requirements.txt >> "%LOGFILE%" 2>&1
if !errorlevel! neq 0 set "UV_CORE1_ERR=1"

uv pip install -r requirements_windows.txt >> "%LOGFILE%" 2>&1
if !errorlevel! neq 0 set "UV_CORE2_ERR=1"

if "!UV_CORE1_ERR!"=="1" (
    echo [WARN] Core deps (requirements.txt) failed via uv, trying pip fallback...
    echo [WARN] Core deps failed via uv, pip fallback... >> "%LOGFILE%" 2>&1
    .venv\Scripts\python.exe -m pip install -r requirements.txt >> "%LOGFILE%" 2>&1
    if !errorlevel! neq 0 (
        echo [ERROR] pip fallback also failed for requirements.txt. Check %LOGFILE%
        echo [ERROR] pip fallback failed for requirements.txt. >> "%LOGFILE%" 2>&1
        goto :failed
    )
)
if "!UV_CORE2_ERR!"=="1" (
    echo [WARN] Core deps (requirements_windows.txt) failed via uv, trying pip fallback...
    echo [WARN] Windows deps failed via uv, pip fallback... >> "%LOGFILE%" 2>&1
    .venv\Scripts\python.exe -m pip install -r requirements_windows.txt >> "%LOGFILE%" 2>&1
    if !errorlevel! neq 0 (
        echo [ERROR] pip fallback also failed for requirements_windows.txt. Check %LOGFILE%
        echo [ERROR] pip fallback failed for requirements_windows.txt. >> "%LOGFILE%" 2>&1
        goto :failed
    )
)

:: Step 5: Install FunASR + torch (CPU only)
echo [5/6] Installing torch + FunASR (may take 5-10 min)...
echo [5/6] Installing torch + FunASR... >> "%LOGFILE%" 2>&1

set "UV_TORCH_ERR=0"
set "UV_FUNASR_ERR=0"

echo        - torch (CPU, ~200MB)...
echo        - torch (CPU)... >> "%LOGFILE%" 2>&1
uv pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu >> "%LOGFILE%" 2>&1
if !errorlevel! neq 0 set "UV_TORCH_ERR=1"

echo        - funasr + modelscope...
echo        - funasr + modelscope... >> "%LOGFILE%" 2>&1
uv pip install funasr modelscope >> "%LOGFILE%" 2>&1
if !errorlevel! neq 0 set "UV_FUNASR_ERR=1"

if "!UV_TORCH_ERR!"=="1" (
    echo [WARN] torch install failed via uv, trying pip fallback...
    echo [WARN] torch failed via uv, pip fallback... >> "%LOGFILE%" 2>&1
    .venv\Scripts\python.exe -m pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu >> "%LOGFILE%" 2>&1
    if !errorlevel! neq 0 (
        echo [WARN] pip fallback also failed for torch. FunASR may not work.
        echo [WARN] pip fallback failed for torch. >> "%LOGFILE%" 2>&1
    )
)
if "!UV_FUNASR_ERR!"=="1" (
    echo [WARN] funasr install failed via uv, trying pip fallback...
    echo [WARN] funasr failed via uv, pip fallback... >> "%LOGFILE%" 2>&1
    .venv\Scripts\python.exe -m pip install funasr modelscope >> "%LOGFILE%" 2>&1
    if !errorlevel! neq 0 (
        echo [WARN] pip fallback also failed for funasr. FunASR engine will not work.
        echo [WARN] pip fallback failed for funasr. >> "%LOGFILE%" 2>&1
    )
)

:: Step 6: Verify
echo [6/6] Verifying installation...
echo [6/6] Verifying installation... >> "%LOGFILE%" 2>&1
set "VERIFY_FAIL=0"

call :verify_import "yaml" "PyYAML"
call :verify_import "faster_whisper" "faster-whisper"
call :verify_import "funasr" "funasr"
call :verify_import "torch" "torch"
call :verify_import "sounddevice" "sounddevice"

if "!VERIFY_FAIL!"=="1" (
    echo.
    echo [WARN] Some verifications failed! Check %LOGFILE%
    echo        You can still try running, but some features may not work.
    echo ==================================================
    echo   Setup completed with WARNINGS!
    echo.
    echo   Log:  %LOGFILE%
    echo ==================================================
) else (
    echo.
    echo ==================================================
    echo   Setup complete!
    echo.
    echo   Next: double-click run_full.bat to start
    echo   Log:  %LOGFILE%
    echo ==================================================
)
echo.
echo   Next: double-click run_full.bat to start
echo   Log:  %LOGFILE%
echo ==================================================
echo.
echo Setup complete! >> "%LOGFILE%" 2>&1
pause
endlocal
exit /b 0

:failed
echo.
echo ==================================================
echo   Setup FAILED!
echo   Check %LOGFILE% for error details.
echo   You can send %LOGFILE% for diagnosis.
echo ==================================================
echo.
echo Setup FAILED! >> "%LOGFILE%" 2>&1
pause
endlocal
exit /b 1

:: ============================================================
:: Subroutines
:: ============================================================

:verify_import
:: Usage: call :verify_import "module_name" "display_name"
:: Sets VERIFY_FAIL=1 on failure
.venv\Scripts\python.exe -c "import %~1; print('  [OK] %~2')" >> "%LOGFILE%" 2>&1
if !errorlevel! neq 0 (
    echo   [FAIL] %~2
    set "VERIFY_FAIL=1"
) else (
    echo   [OK] %~2
)
goto :eof
