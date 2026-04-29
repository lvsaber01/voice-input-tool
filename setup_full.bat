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
echo   VoiceInputTool - Full Setup
echo   (faster-whisper + FunASR + Qwen3-ASR)
echo ==================================================
echo   Log file: %LOGFILE%
echo   If the window closes, check %LOGFILE% for details.
echo.

:: Step 1: Check or install uv
echo [1/8] Checking uv...
echo [1/8] Checking uv... >> "%LOGFILE%" 2>&1
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
echo [2/8] Setting up Python 3.11 venv...
echo [2/8] Setting up Python 3.11 venv... >> "%LOGFILE%" 2>&1
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
echo [3/8] Ensuring pip in venv...
echo [3/8] Ensuring pip in venv... >> "%LOGFILE%" 2>&1
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
echo [4/8] Installing core dependencies...
echo [4/8] Installing core dependencies... >> "%LOGFILE%" 2>&1

call :safe_install_req "requirements.txt" "core (requirements.txt)"
call :safe_install_req "requirements_windows.txt" "core (requirements_windows.txt)"

:: Step 5: Install engine dependencies (non-fatal)
echo [5/8] Installing engine dependencies...
echo [5/8] Installing engine dependencies... >> "%LOGFILE%" 2>&1

call :safe_install "--index-url https://download.pytorch.org/whl/cpu torch torchaudio torchvision" "torch (CPU)"
call :safe_install "funasr modelscope" "funasr + modelscope"
call :safe_install "qwen-asr" "qwen-asr (Qwen3-ASR)"
call :safe_install "tiktoken" "tiktoken (Fun-ASR-Nano fix)"

:: Step 6: Verify core dependencies
echo [6/8] Verifying core dependencies...
echo [6/8] Verifying core dependencies... >> "%LOGFILE%" 2>&1
set "VERIFY_FAIL=0"

call :verify_import "yaml" "PyYAML"
call :verify_import "faster_whisper" "faster-whisper"
call :verify_import "sounddevice" "sounddevice"
call :verify_import "scipy" "scipy"

:: Step 7: Verify engine dependencies (non-fatal)
echo [7/8] Verifying engine dependencies (non-fatal)...
echo [7/8] Verifying engine dependencies... >> "%LOGFILE%" 2>&1

call :verify_import "funasr" "funasr"
call :verify_import "torch" "torch"
call :verify_import "qwen_asr" "qwen-asr"
call :verify_import "tiktoken" "tiktoken"

:: Step 8: Done
echo [8/8] Done!
echo [8/8] Done! >> "%LOGFILE%" 2>&1

if "%VERIFY_FAIL%"=="1" (
    echo.
    echo [WARN] Some verifications failed! Check %LOGFILE%
    echo        You can still try running, but some features may not work.
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

:safe_install_req
:: Usage: call :safe_install_req "filename.txt" "display_name"
:: Installs from a requirements file with -r flag.
set "_RNAME=%~2"
echo        Installing %_RNAME%...
echo        Installing %_RNAME%... >> "%LOGFILE%" 2>&1
uv pip install -r "%~1" >> "%LOGFILE%" 2>&1
if errorlevel 1 goto :safe_install_req_pip

echo        [OK] %_RNAME%
echo        [OK] %_RNAME% >> "%LOGFILE%" 2>&1
goto :eof

:safe_install_req_pip
echo        [WARN] uv failed, pip fallback...
echo        [WARN] uv failed, pip fallback... >> "%LOGFILE%" 2>&1
.venv\Scripts\python.exe -m pip install -r "%~1" >> "%LOGFILE%" 2>&1
if errorlevel 1 goto :safe_install_req_fail

echo        [OK] %_RNAME% (pip fallback)
echo        [OK] %_RNAME% (pip fallback) >> "%LOGFILE%" 2>&1
goto :eof

:safe_install_req_fail
echo        [FAIL] %_RNAME%
echo        [FAIL] %_RNAME% >> "%LOGFILE%" 2>&1
goto :eof

:safe_install
:: Usage: call :safe_install "package args" "display_name"
:: Installs packages directly (not from a file).
set "_INSTALL_NAME=%~2"
echo        Installing %_INSTALL_NAME%...
echo        Installing %_INSTALL_NAME%... >> "%LOGFILE%" 2>&1
uv pip install %~1 >> "%LOGFILE%" 2>&1
if errorlevel 1 goto :safe_install_pip_fallback
echo        [OK] %_INSTALL_NAME%
echo        [OK] %_INSTALL_NAME% >> "%LOGFILE%" 2>&1
goto :eof

:safe_install_pip_fallback
echo        [WARN] uv failed, pip fallback...
echo        [WARN] uv failed, pip fallback... >> "%LOGFILE%" 2>&1
.venv\Scripts\python.exe -m pip install %~1 >> "%LOGFILE%" 2>&1
if errorlevel 1 goto :safe_install_fail
echo        [OK] %_INSTALL_NAME% (pip fallback)
echo        [OK] %_INSTALL_NAME% (pip fallback) >> "%LOGFILE%" 2>&1
goto :eof

:safe_install_fail
echo        [FAIL] %_INSTALL_NAME%
echo        [FAIL] %_INSTALL_NAME% >> "%LOGFILE%" 2>&1
goto :eof

:verify_import
:: Usage: call :verify_import "module_name" "display_name"
.venv\Scripts\python.exe -c "import %~1" >nul 2>&1
if errorlevel 1 goto :verify_fail
echo   [OK] %~2
echo   [OK] %~2 >> "%LOGFILE%" 2>&1
goto :eof
:verify_fail
echo   [FAIL] %~2
echo   [FAIL] %~2 >> "%LOGFILE%" 2>&1
set "VERIFY_FAIL=1"
goto :eof
