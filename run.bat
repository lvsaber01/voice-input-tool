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

:: 启动程序
python main.py

if %errorlevel% neq 0 (
    echo.
    echo [错误] 程序异常退出
    pause
)
