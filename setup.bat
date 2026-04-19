@echo off
chcp 65001 >nul 2>&1
title 语音输入工具 - 环境准备

echo ============================================
echo   语音输入工具 - 环境准备脚本
echo ============================================
echo.

:: 检查管理员权限
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 需要管理员权限！
    echo 请右键此文件 → "以管理员身份运行"
    echo.
    pause
    exit /b 1
)
echo [OK] 管理员权限确认

:: 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python！
    echo 请安装 Python 3.11+ : https://www.python.org/downloads/
    echo 安装时勾选 "Add Python to PATH"
    echo.
    pause
    exit /b 1
)
echo [OK] Python 已安装
python --version

:: 检查 VC++ Runtime（简单提示）
echo.
echo [提示] 如果后续模型加载失败，请安装 VC++ Runtime:
echo https://aka.ms/vs/17/release/vc_redist.x64.exe
echo.

:: 升级 pip
echo [1/4] 升级 pip...
python -m pip install --upgrade pip --quiet

:: 安装基础依赖
echo [2/4] 安装基础依赖...
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [错误] 基础依赖安装失败
    pause
    exit /b 1
)

:: 安装 Windows 依赖
echo [3/4] 安装 Windows 依赖...
pip install -r requirements_windows.txt --quiet
if %errorlevel% neq 0 (
    echo [错误] Windows 依赖安装失败
    pause
    exit /b 1
)

:: 下载 STT 模型
echo [4/4] 下载 STT 模型 (small, ~500MB)...
echo 这可能需要几分钟，请耐心等待...
python -c "from faster_whisper import WhisperModel; WhisperModel('small', download_root='./models'); print('模型下载完成')"
if %errorlevel% neq 0 (
    echo [错误] 模型下载失败，请检查网络连接
    echo 可以稍后手动运行:
    echo python -c "from faster_whisper import WhisperModel; WhisperModel('small', download_root='./models')"
    pause
    exit /b 1
)

echo.
echo ============================================
echo   安装完成！
echo   请运行 run.bat 启动程序
echo ============================================
echo.
pause
