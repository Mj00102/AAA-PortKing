@echo off
chcp 65001 >nul
rem ============================================================
rem  AAA端口大王 - 重新打包 exe
rem  改了 portking.py 后双击本文件即可重新生成
rem ============================================================
setlocal
cd /d "%~dp0"

echo [1/4] 检查 Python ...
where python >nul 2>nul || (echo   没有找到 Python, 请先装 Python 3.8+ ^(勾选 Add to PATH^) & pause & exit /b 1)
python --version

echo.
echo [2/4] 检查 PyInstaller ...
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo   未安装, 正在自动安装 ...
    python -m pip install pyinstaller
    if errorlevel 1 (echo   安装失败, 请检查网络 & pause & exit /b 1)
) else (
    echo   已安装
)

echo.
echo [3/4] 编译文件夹版 (约 1 分钟) ...
python -m PyInstaller --onedir --noconsole --name PortKing ^
  --icon "..\assets\app.ico" --add-data "..\assets\app.ico;." ^
  --distpath "dist" --workpath "tools\pyi-work" --specpath "tools" portking.py
if errorlevel 1 (echo   编译失败 & pause & exit /b 1)

echo.
echo [4/4] 生成单文件版 ...
python "tools\make_onefile.py"
if errorlevel 1 (echo   生成失败 & pause & exit /b 1)

echo.
echo ============================================================
echo  完成! 产物:
echo    dist\AAA端口大王.exe        (单文件版, 双击即用)
echo    dist\PortKing\PortKing.exe  (文件夹版)
echo ============================================================
pause
