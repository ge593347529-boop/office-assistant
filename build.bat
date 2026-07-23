@echo off
chcp 65001 >nul
title AI 办公助手 - 构建脚本

echo ========================================
echo   AI 办公助手 - 打包构建
echo ========================================
echo.

:: 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)
echo [✓] Python 已安装

:: 检查依赖
echo [ ] 检查依赖...
pip show PySide6 >nul 2>&1 || (
    echo [ ] 正在安装依赖...
    pip install -r requirements.txt
)
echo [✓] 依赖已就绪

:: 检查 Playwright 浏览器
echo [ ] 检查 Playwright 浏览器...
python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); p.stop()" >nul 2>&1
if %errorlevel% neq 0 (
    echo [ ] 正在安装 Chromium...
    playwright install chromium
)
echo [✓] Playwright 浏览器已就绪

:: 检查 Ollama
echo [ ] 检查 Ollama...
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] 警告: Ollama 服务未运行
    echo     请先启动 Ollama 并确保模型已下载
) else (
    echo [✓] Ollama 服务运行中
)

:: 打包
echo.
echo ========================================
echo   开始打包...
echo ========================================

pyinstaller office-assistant.spec --noconfirm

if %errorlevel% neq 0 (
    echo.
    echo [错误] 打包失败！
    pause
    exit /b 1
)

echo.
echo ========================================
echo   打包完成！
echo   输出: dist\AI办公助手.exe
echo ========================================
echo.
echo 分发说明:
echo   1. 将 dist\AI办公助手.exe 拷贝到目标电脑
echo   2. 目标电脑需安装: Google Chrome 浏览器
echo   3. 目标电脑需安装并运行: Ollama
echo   4. 首次运行会在 exe 同目录创建 data\ 文件夹
echo.

pause
