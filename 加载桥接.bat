@echo off
chcp 65001 >nul
cd /d "%~dp0"
title MeshKit - Load Bridge
set "PYTHONIOENCODING=utf-8"
set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PY if exist "C:\Python312\python.exe" set "PY=C:\Python312\python.exe"
if not defined PY if exist "C:\Python311\python.exe" set "PY=C:\Python311\python.exe"
if not defined PY if exist "C:\Python310\python.exe" set "PY=C:\Python310\python.exe"
if not defined PY if exist "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" set "PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not defined PY set "PY=py -3"

echo ============================================================
echo   MeshKit AI  -  bridge helper
echo   install dir: %CD%
echo ============================================================
echo.

if not exist "app\bridge_agent.py" (
  echo [ERROR] app\bridge_agent.py not found. run this .bat inside the meshkit folder.
  pause
  exit /b 1
)
if not defined PY (
  echo [ERROR] Python not found. install Python 3.8+ and add to PATH.
  pause
  exit /b 1
)
echo Using Python: %PY%
echo.

rem 1) copy the connect command (auto contains this install dir)
%PY% "app\connect_cmd.py"
echo.

rem 2) start local web service if port 8765 is free
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue; if($c){exit 0}else{exit 1}" >nul 2>&1
if errorlevel 1 (
  echo starting local service on http://127.0.0.1:8765 ...
  start "MeshKit Server" /min "%PY%" "app\server.py"
  timeout /t 5 >nul
) else (
  echo local service already running: http://127.0.0.1:8765
)

rem 2.5) auto-start DLP two-way sync (project ^<^-^> %%TEMP%%\meshkit_work) if not running
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*dlp_sync.py*' }; if ($p) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 (
  start "MeshKit DLP Sync" /min "%PY%" "app\tools\dlp_sync.py" --watch 2
  echo DLP auto sync started: project ^<^-^> %%TEMP%%\meshkit_work
) else (
  echo DLP auto sync already running.
)

rem 3) open the page
start "" http://127.0.0.1:8765

echo.
echo done. next: Mechanical -^> Automation -^> Scripting -^> paste -^> Ctrl+F5
echo.
pause
