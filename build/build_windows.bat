@echo off
chcp 65001 >nul 2>&1
title 语音输入工具 - Windows 打包脚本

echo ============================================
echo   语音输入工具 - Windows 打包脚本
echo ============================================
echo.

:: 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python 未安装
    echo 请安装 Python 3.11+ : https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [OK] Python:
python --version

:: 安装 PyInstaller
echo.
echo [1/5] 安装 PyInstaller...
pip install pyinstaller --quiet
if %errorlevel% neq 0 (
    echo [ERROR] PyInstaller 安装失败
    pause
    exit /b 1
)
echo [OK] PyInstaller 已安装

:: 安装项目依赖
echo.
echo [2/5] 安装项目依赖...
pip install -r requirements\base.txt --quiet
if %errorlevel% neq 0 (
    echo [ERROR] 基础依赖安装失败
    pause
    exit /b 1
)

pip install -r requirements\windows.txt --quiet
if %errorlevel% neq 0 (
    echo [ERROR] Windows 依赖安装失败
    pause
    exit /b 1
)
echo [OK] 项目依赖已安装

:: 检查 FunASR（可选）
echo.
echo [3/5] 检查 FunASR 引擎（可选）...
python -c "import funasr; print('FunASR available')" 2>nul
if %errorlevel% equ 0 (
    echo [OK] FunASR 可用，将打包进 exe
) else (
    echo [INFO] FunASR 不可用，只打包 faster-whisper
    echo        如需 FunASR，请先安装: pip install funasr modelscope
)

:: 创建占位图标（如果没有）
echo.
echo [4/5] 检查图标文件...
if not exist "build\icon.ico" (
    echo [INFO] 创建占位图标...
    python -c "from PIL import Image; img = Image.new('RGB', (256, 256), 'blue'); img.save('build/icon.ico', 'ICO')"
    echo [OK] 占位图标已创建
) else (
    echo [OK] 图标文件已存在
)

:: 执行打包
echo.
echo [5/5] 执行 PyInstaller 打包...
echo 这可能需要几分钟，请耐心等待...
pyinstaller voice-input-tool.spec --noconfirm --clean
if %errorlevel% neq 0 (
    echo [ERROR] 打包失败
    echo 请检查 voice-input-tool.spec 配置
    pause
    exit /b 1
)

:: 检查输出
echo.
echo ============================================
echo   打包完成！
echo ============================================
echo.

if exist "dist\VoiceInputTool\VoiceInputTool.exe" (
    echo [OK] 输出目录: dist\VoiceInputTool\
    echo [OK] 可执行文件: VoiceInputTool.exe
    
    :: 显示大小
    for %%F in ("dist\VoiceInputTool\VoiceInputTool.exe") do echo [INFO] exe 大小: %%~zF bytes
    
    :: 创建发布包
    echo.
    echo [INFO] 创建发布包...
    cd dist
    7z a VoiceInputTool-Windows.zip VoiceInputTool -mx=5 >nul 2>&1
    if %errorlevel% equ 0 (
        echo [OK] 发布包: dist\VoiceInputTool-Windows.zip
    ) else (
        echo [WARN] 7z 不可用，跳过压缩
    )
    cd ..
    
    echo.
    echo ============================================
    echo   测试运行
    echo ============================================
    echo.
    echo 运行以下命令测试:
    echo   cd dist\VoiceInputTool
    echo   VoiceInputTool.exe
    echo.
) else (
    echo [ERROR] 打包输出未找到
)

pause