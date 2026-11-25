@echo off
chcp 65001 >nul
title MeshKit - Load Bridge
setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

echo ============================================================
echo   MeshKit AI  -  bridge helper
echo   install dir: %ROOT%
echo ============================================================
echo.

if not exist "%ROOT%\app\bridge_agent.py" (
  echo [ERROR] app\bridge_agent.py not found under %ROOT%
  echo         please run this .bat from the meshkit folder.
  pause
  exit /b 1
)

rem 1) copy the connect command (auto contains this install dir)
python "%ROOT%\app\connect_cmd.py"
echo.

rem 2) start the local web service if port 8765 is free
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue; if($c){exit 0}else{exit 1}" >nul 2>&1
if errorlevel 1 (
  echo starting local service on http://127.0.0.1:8765 ...
  start "" /min cmd /c "cd /d "%ROOT%\app" && python server.py"
  timeout /t 3 >nul
) else (
  echo local service already running: http://127.0.0.1:8765
)

rem 3) open the page
start "" http://127.0.0.1:8765

echo.
echo done. next: in Mechanical -> Automation -> Scripting -> paste -> Ctrl+F5
echo.
pause