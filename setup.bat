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
echo [1/5] 升级 pip...
python -m pip install --upgrade pip --quiet

:: 安装基础依赖
echo [2/5] 安装基础依赖...
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [错误] 基础依赖安装失败
    pause
    exit /b 1
)

:: 安装 Windows 依赖
echo [3/5] 安装 Windows 依赖...
pip install -r requirements_windows.txt --quiet
if %errorlevel% neq 0 (
    echo [错误] Windows 依赖安装失败
    pause
    exit /b 1
)

:: 可选：安装 FunASR（中文极速引擎）
echo [4/5] FunASR 引擎（可选，中文更快）...
echo.
echo   FunASR Paraformer 中文识别速度约 10x 实时（比 Whisper 快）
echo   但需要 Visual C++ Build Tools 编译 editdistance
echo.
set /p INSTALL_FUNASR="是否安装 FunASR？(y/N): "
if /i "%INSTALL_FUNASR%"=="y" (
    echo 正在安装 funasr modelscope...
    pip install funasr modelscope --quiet 2>nul
    if %errorlevel% neq 0 (
        echo [警告] FunASR 安装失败（可能缺少 C++ 编译工具）
        echo.
        echo 解决方案：
        echo   1. 安装 Visual C++ Build Tools:
        echo      https://visualstudio.microsoft.com/visual-cpp-build-tools/
        echo      （安装时勾选 "C++ 桌面开发" 工作负载）
        echo.
        echo   2. 或使用 conda 安装（有预编译包）:
        echo      conda install -c conda-forge editdistance
        echo      pip install funasr modelscope
        echo.
        echo   3. 或跳过 FunASR，使用 faster-whisper（已安装）
        echo.
        echo [提示] 不影响程序运行，只是不能用 FunASR 引擎
    ) else (
        echo [OK] FunASR 安装成功
    )
) else (
    echo [跳过] FunASR 安装
)

:: 下载 STT 模型
echo [5/5] 下载 STT 模型 (large-v3-turbo, ~800MB)...
echo 这可能需要几分钟，请耐心等待...
python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3-turbo', download_root='./models', device='cpu', compute_type='int8'); print('模型下载完成')"
if %errorlevel% neq 0 (
    echo [错误] 模型下载失败，请检查网络连接
    echo 可以稍后手动运行:
    echo python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3-turbo', download_root='./models')"
    pause
    exit /b 1
)

echo.
echo ============================================
echo   安装完成！
echo   请运行 run.bat 启动程序
echo ============================================
echo.
echo 引擎选择（Web 配置页）：
echo   - faster-whisper: 多语言，已安装
echo   - funasr:         中文极速，可选安装
echo.
pause