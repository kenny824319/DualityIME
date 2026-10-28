@echo off
rem Registers (or with /u removes) the C++ input method built into cpp\build\stage.
rem Run as administrator:  cpp\register.cmd        cpp\register.cmd /u
setlocal
set STAGE=%~dp0build\stage
net session >nul 2>&1 || (echo Please run this as administrator. & exit /b 1)
if /i "%~1"=="/u" (
    taskkill /im DualityIMEEngine.exe /f >nul 2>&1
    "%SystemRoot%\SysWOW64\regsvr32.exe" /s /u "%STAGE%\DualityIME32.dll"
    "%SystemRoot%\System32\regsvr32.exe" /s /u "%STAGE%\DualityIME64.dll"
    echo removed
    exit /b 0
)
"%SystemRoot%\System32\regsvr32.exe" /s "%STAGE%\DualityIME64.dll" || (echo 64-bit registration failed & exit /b 1)
"%SystemRoot%\SysWOW64\regsvr32.exe" /s "%STAGE%\DualityIME32.dll" || (echo 32-bit registration failed & exit /b 1)
echo registered. Add it in Settings - Time and language - Language and region - Chinese (Taiwan) - Keyboards.
