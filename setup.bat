@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0" || (
    echo [ERROR] Failed to change directory.
    pause
    exit /b 1
)

if not exist logs mkdir logs
set "LOGFILE=%~dp0logs\setup.log"

echo ================================================== > "%LOGFILE%" 2>&1
echo   VoiceInputTool - Setup Log >> "%LOGFILE%" 2>&1
echo   Date: %date% %time% >> "%LOGFILE%" 2>&1
echo ================================================== >> "%LOGFILE%" 2>&1

echo ==================================================
echo   VoiceInputTool - Environment Setup
echo   Log: logs\setup.log
echo ==================================================
echo.

:: Step 1: Check Python version
echo [1/6] Checking Python...
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.11+
    echo https://www.python.org/downloads/
    echo [ERROR] Python not found >> "%LOGFILE%" 2>&1
    pause
    exit /b 1
)

for /f "tokens=1,2" %%a in ('python -c "import sys; print(sys.version_info.major, sys.version_info.minor)" 2^>^&1') do (
    set "PYMAJ=%%a"
    set "PYMIN=%%b"
)

if "!PYMAJ!"=="" (
    echo [ERROR] Failed to detect Python version.
    echo [ERROR] Failed to detect Python version >> "%LOGFILE%" 2>&1
    pause
    exit /b 1
)
echo        Python !PYMAJ!.!PYMIN! detected
echo        Python !PYMAJ!.!PYMIN! detected >> "%LOGFILE%" 2>&1

:: Step 2: Create venv
set "NEEDS_UV_311=0"
if !PYMAJ! LSS 3 (
    echo [ERROR] Python version too old. Need Python 3.11 - 3.13.
    pause
    exit /b 1
)
if !PYMAJ! GTR 3 (
    echo [WARN] Python !PYMAJ!.!PYMIN! not compatible. Will use uv with Python 3.11.
    set "NEEDS_UV_311=1"
)
if !PYMAJ!==3 if !PYMIN! LSS 11 (
    echo [ERROR] Python 3.!PYMIN! is too old. Need Python 3.11 - 3.13.
    pause
    exit /b 1
)
if !PYMAJ!==3 if !PYMIN! GEQ 14 (
    echo [WARN] Python 3.!PYMIN! may have issues. Will use uv with Python 3.11.
    set "NEEDS_UV_311=1"
)

echo [2/6] Creating virtual environment...
if exist .venv (
    echo        venv exists, reusing
    echo        venv exists >> "%LOGFILE%" 2>&1
) else (
    set "VENV_CREATED=0"
    if "!NEEDS_UV_311!"=="1" goto :create_with_uv

    where uv >nul 2>&1
    if !errorlevel! equ 0 (
        echo        Trying uv with Python 3.11...
        uv venv --python 3.11 >nul 2>&1
        if !errorlevel! equ 0 (
            set "VENV_CREATED=1"
            echo        venv created with uv (Python 3.11)
            echo        venv created with uv >> "%LOGFILE%" 2>&1
        )
    )

    if "!VENV_CREATED!"=="0" (
        echo        Creating venv (Python 3.!PYMIN!)...
        python -m venv .venv
        if !errorlevel! neq 0 (
            echo [ERROR] Failed to create venv
            echo [ERROR] Failed to create venv >> "%LOGFILE%" 2>&1
            pause
            exit /b 1
        )
        echo        venv created >> "%LOGFILE%" 2>&1
    )
    goto :venv_done

    :create_with_uv
    where uv >nul 2>&1
    if !errorlevel! neq 0 (
        echo        Installing uv...
        powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" 2>nul
        set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
    )
    where uv >nul 2>&1
    if !errorlevel! neq 0 (
        echo [ERROR] Cannot install uv. Please install Python 3.11 manually:
        echo        https://www.python.org/downloads/
        echo [ERROR] Cannot install uv >> "%LOGFILE%" 2>&1
        pause
        exit /b 1
    )
    uv venv --python 3.11
    if !errorlevel! neq 0 (
        echo [ERROR] Cannot create Python 3.11 venv.
        echo [ERROR] Cannot create Python 3.11 venv >> "%LOGFILE%" 2>&1
        pause
        exit /b 1
    )
    echo        venv created with uv (Python 3.11) >> "%LOGFILE%" 2>&1

    :venv_done
)

:: Step 3: Ensure pip
echo [3/6] Ensuring pip in venv...
.venv\Scripts\python.exe -c "import pip" >nul 2>&1
if !errorlevel! neq 0 (
    where uv >nul 2>&1
    if !errorlevel! equ 0 (
        uv pip install pip >nul 2>&1
    )
    .venv\Scripts\python.exe -c "import pip" >nul 2>&1
    if !errorlevel! neq 0 (
        .venv\Scripts\python.exe -m ensurepip --default-pip >nul 2>&1
    )
    .venv\Scripts\python.exe -c "import pip" >nul 2>&1
    if !errorlevel! neq 0 (
        echo [ERROR] Cannot install pip. Try: .venv\Scripts\python.exe -m ensurepip
        echo [ERROR] Cannot install pip >> "%LOGFILE%" 2>&1
        pause
        exit /b 1
    )
    echo        pip installed OK
)
echo        pip OK
echo        pip OK >> "%LOGFILE%" 2>&1

:: Step 4: Install base + platform dependencies
echo [4/6] Installing dependencies...
where uv >nul 2>&1
if !errorlevel! neq 0 (
    .venv\Scripts\python.exe -m pip install -r "%~dp0requirements\base.txt" -q 2>>"%LOGFILE%"
    if !errorlevel! neq 0 echo [WARN] Base deps failed via pip, trying fallback...
    .venv\Scripts\python.exe -m pip install -r "%~dp0requirements\windows.txt" -q 2>>"%LOGFILE%"
) else (
    uv pip install -r "%~dp0requirements\base.txt" -q 2>>"%LOGFILE%"
    if !errorlevel! neq 0 (
        echo [WARN] Base deps failed via uv, pip fallback...
        .venv\Scripts\python.exe -m pip install -r "%~dp0requirements\base.txt" -q 2>>"%LOGFILE%"
    )
    uv pip install -r "%~dp0requirements\windows.txt" -q 2>>"%LOGFILE%"
    if !errorlevel! neq 0 (
        .venv\Scripts\python.exe -m pip install -r "%~dp0requirements\windows.txt" -q 2>>"%LOGFILE%"
    )
)
echo        Dependencies installed
echo        Dependencies installed >> "%LOGFILE%" 2>&1

:: Step 5: Optional engine deps (FunASR / Qwen3-ASR)
echo [5/6] Checking optional engines...
echo [5/6] Checking optional engines... >> "%LOGFILE%" 2>&1

.venv\Scripts\python.exe -c "import funasr" >nul 2>&1
if !errorlevel! neq 0 (
    if exist "%~dp0config.yaml" (
        findstr /i "funasr" "%~dp0config.yaml" >nul 2>&1
        if !errorlevel! equ 0 (
            echo        Installing FunASR (may take a few minutes)...
            where uv >nul 2>&1
            if !errorlevel! neq 0 (
                .venv\Scripts\python.exe -m pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu -q 2>>"%LOGFILE%"
                .venv\Scripts\python.exe -m pip install funasr modelscope -q 2>>"%LOGFILE%"
            ) else (
                uv pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu -q 2>>"%LOGFILE%"
                uv pip install funasr modelscope -q 2>>"%LOGFILE%"
            )
            if !errorlevel! neq 0 echo [WARN] FunASR install failed, will fallback to faster-whisper
        )
    )
) else (
    echo        FunASR OK
)

.venv\Scripts\python.exe -c "import qwen_asr" >nul 2>&1
if !errorlevel! equ 0 (
    echo        Qwen3-ASR OK
)

:: Step 6: Verify core imports
echo [6/6] Verifying core dependencies...
set "VERIFY_FAIL=0"

for %%M in (yaml faster_whisper sounddevice scipy pypinyin) do (
    .venv\Scripts\python.exe -c "import %%M" >nul 2>&1
    if !errorlevel! equ 0 (
        echo   [OK] %%M
    ) else (
        echo   [FAIL] %%M
        set "VERIFY_FAIL=1"
    )
)

echo. >> "%LOGFILE%" 2>&1
echo Setup complete at %date% %time% >> "%LOGFILE%" 2>&1

if "!VERIFY_FAIL!"=="1" (
    echo.
    echo [WARN] Some verifications failed! Check logs\setup.log
    echo        You can still try running, but some features may not work.
    pause
    exit /b 1
)

echo.
echo ==================================================
echo   Setup complete!
echo   Next: run.bat to start
echo   Log:  logs\setup.log
echo ==================================================
pause
endlocal
exit /b 0
