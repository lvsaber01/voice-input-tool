@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0" || (
    echo [ERROR] Failed to change directory.
    pause
    exit /b 1
)

echo ==================================================
echo   VoiceInputTool
echo ==================================================
echo.

:: Step 1: Check Python
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.11+
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Check Python version using sys.version_info for robust parsing
for /f "tokens=1,2" %%a in ('python -c "import sys; print(sys.version_info.major, sys.version_info.minor)" 2^>^&1') do (
    set "PYMAJ=%%a"
    set "PYMIN=%%b"
)

if "!PYMAJ!"=="" (
    echo [ERROR] Failed to detect Python version.
    echo        Please ensure Python 3.11+ is installed correctly.
    pause
    exit /b 1
)

echo        Python !PYMAJ!.!PYMIN! detected

:: Version check: need 3.11 - 3.13
set "NEEDS_UV_311=0"
if !PYMAJ! LSS 3 (
    echo [ERROR] Python version too old. Need Python 3.11 - 3.13.
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)
if !PYMAJ! GTR 3 (
    echo [WARN] Python !PYMAJ!.!PYMIN! is not compatible. Need Python 3.11 - 3.13.
    set "NEEDS_UV_311=1"
)
if !PYMAJ!==3 if !PYMIN! LSS 11 (
    echo [ERROR] Python 3.!PYMIN! is too old. Need Python 3.11 - 3.13.
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)
if !PYMAJ!==3 if !PYMIN! GEQ 14 (
    echo [WARN] Python 3.!PYMIN! may have compatibility issues, will use uv with Python 3.11.
    set "NEEDS_UV_311=1"
)

:: Step 2: Create venv
if exist .venv (
    echo [2/4] venv exists
) else (
    set "VENV_CREATED=0"

    :: If Python 3.14+ or incompatible, must use uv with Python 3.11
    if "!NEEDS_UV_311!"=="1" goto :create_with_uv

    :: Try uv first for any version
    where uv >nul 2>&1
    if !errorlevel! equ 0 (
        echo        Trying uv to create Python 3.11 venv...
        uv venv --python 3.11 >nul 2>&1
        if !errorlevel! equ 0 (
            set "VENV_CREATED=1"
            echo        venv created with uv (Python 3.11)
        )
    )

    :: Fallback to system python -m venv
    if "!VENV_CREATED!"=="0" (
        echo [2/4] Creating venv (Python 3.!PYMIN!)...
        python -m venv .venv
        if !errorlevel! neq 0 (
            echo [ERROR] Failed to create venv
            pause
            exit /b 1
        )
        set "VENV_CREATED=1"
    )
    goto :venv_done

    :create_with_uv
    echo        Python 3.11+ required, using uv...
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
        pause
        exit /b 1
    )
    uv venv --python 3.11
    if !errorlevel! neq 0 (
        echo [ERROR] Cannot create Python 3.11 venv with uv.
        echo        Please install Python 3.11 manually: https://www.python.org/downloads/
        pause
        exit /b 1
    )
    set "VENV_CREATED=1"

    :venv_done
)

:: Ensure pip is available in venv (uv venv does not include pip)
:: This runs regardless of how the venv was created
.venv\Scripts\python.exe -c "import pip" >nul 2>&1
if !errorlevel! neq 0 (
    echo        Installing pip into venv...
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
        echo [ERROR] Cannot install pip. Try manually:
        echo        .venv\Scripts\python.exe -m ensurepip
        pause
        exit /b 1
    )
    echo        pip installed OK
)

:: Step 3: Install deps
echo [3/4] Installing dependencies...
where uv >nul 2>&1
if !errorlevel! neq 0 (
    .venv\Scripts\python.exe -m pip install -r "%~dp0requirements.txt" -q
    if !errorlevel! neq 0 (
        echo [WARN] Some dependencies failed to install. Check above for errors.
    )
    .venv\Scripts\python.exe -m pip install -r "%~dp0requirements_windows.txt" -q
    if !errorlevel! neq 0 (
        echo [WARN] Some Windows dependencies failed to install.
    )
) else (
    uv pip install -r "%~dp0requirements.txt" -q
    if !errorlevel! neq 0 (
        echo [WARN] Some deps failed via uv, trying pip fallback...
        .venv\Scripts\python.exe -m pip install -r "%~dp0requirements.txt" -q
    )
    uv pip install -r "%~dp0requirements_windows.txt" -q
    if !errorlevel! neq 0 (
        .venv\Scripts\python.exe -m pip install -r "%~dp0requirements_windows.txt" -q
    )
)

:: FunASR if needed
.venv\Scripts\python.exe -c "import funasr" 2>nul
if !errorlevel! neq 0 (
    if exist "%~dp0config.yaml" (
        findstr /i "funasr" "%~dp0config.yaml" >nul 2>&1
        if !errorlevel! equ 0 (
            echo Installing FunASR (may take a few minutes)...
            where uv >nul 2>&1
            if !errorlevel! neq 0 (
                .venv\Scripts\python.exe -m pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu -q
                .venv\Scripts\python.exe -m pip install funasr modelscope -q
            ) else (
                uv pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu -q
                uv pip install funasr modelscope -q
            )
            if !errorlevel! neq 0 (
                echo [WARN] FunASR install failed, will fallback to faster-whisper
            )
        )
    )
)

:: Step 4: Run
echo [4/4] Starting VoiceInputTool...
echo.
.venv\Scripts\python.exe "%~dp0main.py"
if !errorlevel! neq 0 (
    echo.
    echo [ERROR] Program exited abnormally, check logs/
)
pause
endlocal
exit /b 0
