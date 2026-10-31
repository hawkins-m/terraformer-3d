@echo off
rem Terraformer launcher for native Windows (beta: the greybox path, no WSL).
rem Starts `ap ui` and opens the app at http://localhost:8700/ when it answers.
rem Closing this window (or Ctrl+C) stops ap ui. install.ps1 copies this file to
rem %LOCALAPPDATA%\Terraformer\Terraformer.bat with the values below filled in.
setlocal
set "TF_AP=C:\path\to\venv\Scripts\ap.exe"
set "TF_PORT=8700"
set "PYTHONUTF8=1"
title Terraformer (native Windows, beta)
if not exist "%TF_AP%" (echo Terraformer is not installed here: run windows\install.bat first. & pause & exit /b 1)
echo Terraformer - native Windows, beta (the greybox path; TRELLIS and the vision model need the WSL install)
start "" /b powershell.exe -NoProfile -WindowStyle Hidden -Command "$u='http://localhost:%TF_PORT%/'; $up=$false; for ($i=0; $i -lt 60 -and -not $up; $i++) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 ($u + 'api/version') | Out-Null; $up=$true } catch { Start-Sleep 1 } }; if ($up) { try { Start-Process msedge -ArgumentList ('--app=' + $u) -ErrorAction Stop } catch { Start-Process $u } }"
"%TF_AP%" ui --port %TF_PORT%
if errorlevel 1 (
  echo.
  echo Terraformer did not start. See the messages above, or run: ap doctor
  pause
)
endlocal
