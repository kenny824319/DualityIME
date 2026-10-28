@echo off
rem Builds the C++ version into cpp\build\<config> (default Release).
rem   cpp\build.cmd [Release|Debug] [x64|x86|arm64]
setlocal
set CONFIG=%~1
if "%CONFIG%"=="" set CONFIG=Release
set ARCH=%~2
if "%ARCH%"=="" set ARCH=x64
for /f "usebackq tokens=*" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set VS=%%i
if not defined VS (echo Visual Studio Build Tools with C++ not found & exit /b 1)
call "%VS%\Common7\Tools\VsDevCmd.bat" -arch=%ARCH% -host_arch=x64 >nul || exit /b 1
set OUT=%~dp0build\%ARCH%-%CONFIG%
cmake -S "%~dp0." -B "%OUT%" -G Ninja -DCMAKE_BUILD_TYPE=%CONFIG% >nul || exit /b 1
cmake --build "%OUT%" || exit /b 1
