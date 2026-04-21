@echo off
cd /d "%~dp0"

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

:: Check Python version (need >=3.11, <3.14)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do (
    set PYMAJ=%%a
    set PYMIN=%%b
)

:: Step 2: Create venv (try uv first, fallback to python -m venv)
if exist .venv (
    echo [2/4] venv exists
) else (
    :: Try uv
    set USE_UV=0
    where uv >nul 2>&1
    if not errorlevel 1 (
        uv venv --python 3.11 >nul 2>&1
        if not errorlevel 1 set USE_UV=1
    )

    if "%USE_UV%"=="0" (
        :: Check system Python version
        if %PYMAJ% LSS 3 (
            echo [ERROR] Python %PYVER% too old. Need Python 3.11+
            echo Please install Python 3.11: https://www.python.org/downloads/
            pause
            exit /b 1
        )
        if %PYMAJ%==3 if %PYMIN% GEQ 14 (
            echo [WARN] Python %PYVER% may have compatibility issues.
            echo Trying to use uv with Python 3.11...
            where uv >nul 2>&1
            if errorlevel 1 (
                powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" 2>nul
                set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
            )
            where uv >nul 2>&1
            if not errorlevel 1 (
                uv venv --python 3.11
                if errorlevel 1 (
                    echo [ERROR] Cannot create Python 3.11 venv.
                    echo Please install Python 3.11: https://www.python.org/downloads/
                    pause
                    exit /b 1
                )
                set USE_UV=1
            ) else (
                echo [ERROR] Python %PYVER% is not compatible. Need Python 3.11-3.13.
                echo Cannot install uv. Please install Python 3.11 manually.
                pause
                exit /b 1
            )
        )
        if "%USE_UV%"=="0" (
            echo [2/4] Creating venv (Python %PYVER%)...
            python -m venv .venv
            if errorlevel 1 (
                echo [ERROR] Failed to create venv
                pause
                exit /b 1
            )
        )
    )
)

:: Step 3: Install deps
echo [3/4] Installing dependencies...
where uv >nul 2>&1
if errorlevel 1 (
    .venv\Scripts\python -m pip install -r requirements.txt -q
    .venv\Scripts\python -m pip install -r requirements_windows.txt -q
) else (
    uv pip install -r requirements.txt -q
    uv pip install -r requirements_windows.txt -q
)

:: FunASR if needed
.venv\Scripts\python -c "import funasr" 2>nul
if errorlevel 1 (
    findstr /i "funasr" config.yaml >nul 2>&1
    if not errorlevel 1 (
        echo Installing FunASR (may take a few minutes)...
        where uv >nul 2>&1
        if errorlevel 1 (
            .venv/scripts\python -m pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu -q
            .venv\Scripts\python -m pip install funasr modelscope -q
        ) else (
            uv pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu -q
            uv pip install funasr modelscope -q
        )
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
