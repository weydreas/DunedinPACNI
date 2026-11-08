@echo off
setlocal
chcp 65001 >nul 2>&1
set "PACNI_ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PACNI_ROOT%start.ps1"
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" echo Launcher error %CODE%.
pause
exit /b %CODE%
