@echo off
rem Copies a fresh build into cpp\build\stage (where register.cmd registered it).
rem A DLL still loaded in some app is renamed out of the way first: new apps get the new one.
setlocal
set STAGE=%~dp0build\stage
set B=%~dp0build
taskkill /im DualityIMEEngine.exe /f >nul 2>&1
for %%f in (DualityIME64.dll DualityIME32.dll) do (
    if exist "%STAGE%\%%f" (
        del /q "%STAGE%\%%f.old*" >nul 2>&1
        copy /y nul "%STAGE%\%%f.probe" >nul & del /q "%STAGE%\%%f.probe"
        move /y "%STAGE%\%%f" "%STAGE%\%%f.old%RANDOM%" >nul
    )
)
copy /y "%B%\x64-Release\DualityIME64.dll" "%STAGE%\" >nul || exit /b 1
copy /y "%B%\x86-Release\DualityIME32.dll" "%STAGE%\" >nul || exit /b 1
copy /y "%B%\x64-Release\DualityIMEEngine.exe" "%STAGE%\" >nul || exit /b 1
echo deployed
