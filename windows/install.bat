@echo off
rem Terraformer installer for Windows (WSL2) + NVIDIA, beta. Runs install.ps1 next to it: the WSL
rem setup, then the setup wizard in your browser.
rem Options are passed through, e.g.: install.bat -Distro Ubuntu-24.04 -NoDesktopShortcut
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "rc=%errorlevel%"
echo.
pause
exit /b %rc%
