@echo off
chcp 65001 >nul
title 房车比价通
cd /d "%~dp0"
echo ============================================
echo   房车比价通 - 二手房车聚合比价
echo ============================================
echo.
echo [1/2] 正在启动比价应用...
echo.
start http://127.0.0.1:5000
python app.py
pause
