@echo off
rem Terraformer launcher for Windows (WSL2) + NVIDIA, beta.
rem Starts `ap ui` inside WSL and opens your default browser at http://localhost:8700/.
rem Closing this window (or Ctrl+C) stops ap ui. install.ps1 copies this file to
rem %LOCALAPPDATA%\Terraformer\ with the values below filled in, and points the shortcuts at it.
setlocal
set "TF_DISTRO=Ubuntu-24.04"
set "TF_REPO=$HOME/terraformer-3d"
set "TF_PORT=8700"
title Terraformer (WSL2 + NVIDIA, beta)
where wsl.exe >nul 2>&1 || (echo WSL is not installed: run install.bat first. & pause & exit /b 1)
echo Terraformer - Windows (WSL2) + NVIDIA, beta
wsl.exe -d %TF_DISTRO% -- bash -lc "exec \"%TF_REPO%/scripts/wsl_ui.sh\" %TF_PORT%"
if errorlevel 1 (
  echo.
  echo Terraformer did not start. See the messages above, or run "ap doctor" in WSL.
  pause
)
endlocal
