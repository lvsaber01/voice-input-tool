@echo off
chcp 65001 >nul 2>&1
title 语音输入工具

:: 检测管理员权限
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo 正在请求管理员权限...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

:: 切换到脚本所在目录
cd /d "%~dp0"

:: 启动主程序
echo 正在启动语音输入工具...
python\python.exe main.py
if %errorLevel% neq 0 (
    echo.
    echo 程序异常退出，错误码: %errorLevel%
    pause
)
