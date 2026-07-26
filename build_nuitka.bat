@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================
echo   AI 办公助手 — Nuitka 打包脚本
echo ============================================
echo.

REM -------------------------------------------------
REM 1. 检查 Python
REM -------------------------------------------------
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请确认 Python 已安装并加入 PATH。
    pause
    exit /b 1
)
echo [OK] Python 已就绪

REM -------------------------------------------------
REM 2. 检查 / 安装 Nuitka
REM -------------------------------------------------
python -c "import nuitka" >nul 2>&1
if %errorlevel% neq 0 (
    echo [信息] 未检测到 Nuitka，正在安装...
    pip install nuitka>=2.0
    if %errorlevel% neq 0 (
        echo [错误] Nuitka 安装失败，请检查 pip 配置。
        pause
        exit /b 1
    )
)
echo [OK] Nuitka 已就绪

REM -------------------------------------------------
REM 3. 检查关键依赖是否已安装
REM -------------------------------------------------
echo [信息] 检查 PySide6...
python -c "import PySide6" >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] PySide6 未安装，请先执行: pip install -r requirements.txt
    pause
    exit /b 1
)
echo [OK] PySide6 已就绪

REM -------------------------------------------------
REM 4. 执行 Nuitka 打包
REM -------------------------------------------------
echo.
echo ============================================
echo   开始打包 main.py
echo ============================================
echo.

python -m nuitka ^
    --standalone ^
    --windows-console-mode=disable ^
    --enable-plugin=pyside6 ^
    --output-dir=./dist ^
    main.py

if %errorlevel% neq 0 (
    echo.
    echo [错误] Nuitka 打包失败，请查看上方日志。
    pause
    exit /b 1
)

REM -------------------------------------------------
REM 5. 完成
REM -------------------------------------------------
echo.
echo ============================================
echo   打包完成！
echo   输出目录: .\dist\main.dist\
echo   可执行文件: .\dist\main.dist\main.exe
echo ============================================
pause
