@echo off
chcp 65001 >nul
rem ============================================================
rem  AAA端口大王 - 一键启动
rem  双击本文件即可（无黑窗口）
rem ============================================================
setlocal
cd /d "%~dp0"

if not exist "dist\AAA端口大王.exe" (
    echo.
    echo   [错误] 找不到 dist\AAA端口大王.exe
    echo.
    echo   请任选一种:
    echo     1. 用源码跑:  python portking.py
    echo     2. 自己打包:  双击 打包exe.bat
    echo.
    pause
    exit /b 1
)

start "" "dist\AAA端口大王.exe"
exit /b 0
