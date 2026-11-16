@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto checkdeps
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 -m venv .venv
if errorlevel 1 goto failed
goto checkdeps
:usepython
where python >nul 2>nul
if errorlevel 1 goto nopython
python -m venv .venv
if errorlevel 1 goto failed
:checkdeps
".venv\Scripts\python.exe" -c "import ifstools, PIL, PySide6.QtGui, imageio_ffmpeg, Crypto.Cipher.AES, Crypto.Cipher.Blowfish, numpy" >nul 2>nul
if not errorlevel 1 goto launch
echo 首次运行：正在安装界面和资源解码依赖…
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
:launch
if not exist "native\assets\Application.ico" ".venv\Scripts\python.exe" tools\build_icons.py
set "IIDX_VIEWER_HOME=%cd%"
set "IIDX_BUILD_PLATFORM=x64"
if /I "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "IIDX_BUILD_PLATFORM=ARM64"
if /I "%PROCESSOR_ARCHITEW6432%"=="ARM64" set "IIDX_BUILD_PLATFORM=ARM64"
if exist "build\windows\IIDXResourceViewer.exe" goto runnative
where dotnet >nul 2>nul
if errorlevel 1 goto nodotnet
dotnet build "native\windows\IIDXResourceViewer.csproj" -c Release -p:Platform=%IIDX_BUILD_PLATFORM%
if errorlevel 1 goto failed
:runnative
start "" "build\windows\IIDXResourceViewer.exe" %*
if errorlevel 1 goto failed
exit /b 0
:nodotnet
echo 首次构建应用需要 .NET 8 SDK，请安装后重新运行。
echo https://dotnet.microsoft.com/download/dotnet/8.0
goto failed
:nopython
echo 请先安装 Python 3.10 或更新版本，并勾选 Add Python to PATH。
echo https://www.python.org/downloads/
:failed
echo 启动失败，请查看上方的错误信息。
pause
exit /b 1
