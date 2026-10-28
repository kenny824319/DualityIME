@echo off
rem Builds the release versions and copies them into bin\ (ignored by git), which
rem installer\build.ps1 -Release packs into the installer.
setlocal
call "%~dp0build.cmd" Release x64 || exit /b 1
call "%~dp0build.cmd" Release x86 || exit /b 1
set BIN=%~dp0..\bin
if not exist "%BIN%" mkdir "%BIN%"
copy /y "%~dp0build\x64-Release\DualityIME64.dll" "%BIN%\" >nul || exit /b 1
copy /y "%~dp0build\x86-Release\DualityIME32.dll" "%BIN%\" >nul || exit /b 1
copy /y "%~dp0build\x64-Release\DualityIMEEngine.exe" "%BIN%\" >nul || exit /b 1
echo published to bin\
