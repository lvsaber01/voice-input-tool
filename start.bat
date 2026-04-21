@echo off
chcp 65001 >nul 2>&1
title 语音输入工具
cd /d "%~dp0"

echo ==================================================
echo   语音输入工具 - VoiceInputTool
echo ==================================================
echo.

:: 检查 uv
where uv >nul 2>&1
if errorlevel 1 (
    echo [1/4] 安装 uv...
    powershell -c "irm https://astral.sh/uv/install.ps1 | iex" 2>nul
    if errorlevel 1 (
        echo [错误] uv 安装失败，请手动安装: https://docs.astral.sh/uv/getting-started/installation/
        pause
        exit /b 1
    )
    :: 重新加载 PATH
    set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
    where uv >nul 2>&1
    if errorlevel 1 (
        echo [错误] uv 安装后未找到，请关闭终端重试
        pause
        exit /b 1
    )
) else (
    echo [1/4] uv 已就绪
)

:: 创建 venv
if not exist .venv (
    echo [2/4] 创建虚拟环境 (Python 3.11)...
    uv venv --python 3.11
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
) else (
    echo [2/4] 虚拟环境已存在
)

:: 安装基础依赖
echo [3/4] 检查依赖...
uv pip install -r requirements.txt -q
uv pip install -r requirements_windows.txt -q

:: FunASR 按需安装
if exist config.yaml (
    findstr /i "funasr" config.yaml >nul 2>&1
) else (
    findstr /i "funasr" config.example.yaml >nul 2>&1
)
if not errorlevel 1 (
    .venv\Scripts\python -c "import funasr" 2>nul || (
        echo [提示] 检测到 FunASR 引擎配置，正在安装 FunASR 依赖...
        echo       (首次安装可能需要几分钟，需要 VC++ Build Tools)
        uv pip install funasr modelscope
        if errorlevel 1 (
            echo [警告] FunASR 安装失败，将回退到 faster-whisper 引擎
            echo       如需 FunASR 请安装 VC++ Build Tools: https://visualstudio.microsoft.com/visual-cpp-build-tools/
        )
    )
)

:: 启动
echo [4/4] 启动语音输入工具...
echo.
.venv\Scripts\python main.py
if errorlevel 1 (
    echo.
    echo [程序异常退出] 请检查日志目录: logs\
)
pause
