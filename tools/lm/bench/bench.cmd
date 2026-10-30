@echo off
rem Builds and runs tools\lm\bench\bench.cpp (one core, /O2, x64), like cpp\build.cmd finds the compiler.
setlocal
for /f "usebackq tokens=*" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set VS=%%i
if not defined VS (echo Visual Studio Build Tools with C++ not found & exit /b 1)
call "%VS%\Common7\Tools\VsDevCmd.bat" -arch=x64 -host_arch=x64 >nul || exit /b 1
set OUT=%TEMP%\dualityime-lm-bench
if not exist "%OUT%" mkdir "%OUT%"
cl /nologo /O2 /arch:AVX2 /EHsc /std:c++17 /Fe"%OUT%\bench.exe" /Fo"%OUT%\\" "%~dp0bench.cpp" >nul || exit /b 1
"%OUT%\bench.exe"
