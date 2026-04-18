@echo off
chcp 65001 >nul 2>&1
echo ===================================
echo   语音输入工具 - 环境准备
echo ===================================
echo.

:: 检测管理员权限
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [错误] 请以管理员权限运行此脚本！
    echo 右键点击此文件 → "以管理员身份运行"
    pause
    exit /b 1
)

cd /d "%~dp0"

:: ---- 1. 检查 VC++ Runtime ----
echo [1/4] 检查 VC++ Runtime...
reg query "HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" /v Version >nul 2>&1
if %errorLevel% neq 0 (
    echo [警告] 未检测到 VC++ 2015-2022 x64 Runtime
    echo CTranslate2 依赖此运行时，请下载安装：
    echo https://aka.ms/vs/17/release/vc_redist.x64.exe
    echo.
    echo 安装完成后请重新运行此脚本。
    start https://aka.ms/vs/17/release/vc_redist.x64.exe
    pause
    exit /b 1
)
echo       VC++ Runtime 已安装 ✓

:: ---- 2. 配置 embeddable Python ----
echo [2/4] 配置 Python 环境...
if not exist "python\python.exe" (
    echo [错误] 未找到 python\python.exe
    echo 请确保 embeddable Python 已放置在 python\ 目录中
    pause
    exit /b 1
)

:: 确保 python311.zip 已解压到 python\Lib（如需要）
if not exist "python\Lib" (
    if exist "python\python311.zip" (
        echo       正在解压 python311.zip...
        powershell -Command "Expand-Archive -Path 'python\python311.zip' -DestinationPath 'python\Lib' -Force"
    )
)
echo       Python 环境就绪 ✓

:: 确保 pip 可用
if not exist "python\Scripts\pip.exe" (
    echo       正在安装 pip...
    python\python.exe -m ensurepip --default-pip 2>nul
    if %errorLevel% neq 0 (
        echo       正在通过 get-pip.py 安装...
        powershell -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile 'get-pip.py'"
        python\python.exe get-pip.py
        del get-pip.py 2>nul
    )
)

:: ---- 3. 安装依赖 ----
echo [3/4] 检查 Python 依赖...
python\python.exe -c "import faster_whisper" 2>nul
if %errorLevel% neq 0 (
    echo       正在安装依赖（首次安装可能需要几分钟）...
    python\python.exe -m pip install -r requirements.txt
    if %errorLevel% neq 0 (
        echo [错误] 依赖安装失败，请检查网络连接
        pause
        exit /b 1
    )
) else (
    echo       依赖已安装 ✓
)

:: ---- 4. 检查模型文件 ----
echo [4/4] 检查模型文件...
if exist "models\small\model.bin" (
    echo       模型文件就绪 ✓
) else (
    echo [警告] 未找到 STT 模型文件
    echo.
    echo 正在下载 Whisper small 模型（约 500MB）...
    echo 如果下载失败，请手动运行: python\python.exe scripts\download_model.py
    echo.
    python\python.exe scripts\download_model.py
    if %errorLevel% neq 0 (
        echo [警告] 模型下载失败，请稍后手动运行 download_model.py
    )
)

echo.
echo ===================================
echo   环境准备完成！
echo   请以管理员权限运行 run.bat 启动工具
echo ===================================
pause
