@echo off
chcp 65001 >nul 2>&1
title 语音输入工具

:: 检查管理员权限
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 需要管理员权限！
    echo 请右键此文件 → "以管理员身份运行"
    echo.
    pause
    exit /b 1
)

if not exist logs mkdir logs

:: 启动程序
python main.py 2>logs\stderr.log

set EXIT_CODE=%errorlevel%
echo [%date% %time%] 退出码: %EXIT_CODE% >> logs\run.log

if %EXIT_CODE% neq 0 (
    echo.
    echo [错误] 程序异常退出 (退出码: %EXIT_CODE%)
    echo 日志目录: logs\
    echo   - app.log    运行日志
    echo   - crash.log  崩溃日志  
    echo   - stderr.log 错误输出
    echo.
    pause
)
