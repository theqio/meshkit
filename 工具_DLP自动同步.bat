@echo off
chcp 65001 >nul
cd /d "%~dp0"
title MeshKit - DLP auto sync
set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PY if exist "C:\Python312\python.exe" set "PY=C:\Python312\python.exe"
if not defined PY if exist "C:\Python311\python.exe" set "PY=C:\Python311\python.exe"
if not defined PY if exist "C:\Python310\python.exe" set "PY=C:\Python310\python.exe"
if not defined PY if exist "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" set "PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not defined PY set "PY=py -3"
set PYTHONIOENCODING=utf-8

echo Using Python: %PY%
echo.

powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*dlp_sync.py*' }; if ($p) { exit 0 } else { exit 1 }" >nul 2>&1
if not errorlevel 1 (
  echo Auto sync is already running.
  echo Work folder: %TEMP%\meshkit_work
  echo.
  %PY% "app\tools\dlp_sync.py" --status
  echo.
  pause
  exit /b 0
)

echo Starting auto sync in background (project ^<-> %%TEMP%%\meshkit_work, every 2s)...
start "MeshKit DLP Sync" /min "%PY%" "app\tools\dlp_sync.py" --watch 2
timeout /t 3 >nul
echo.
echo Auto sync started. Do NOT close the minimized window "MeshKit DLP Sync".
echo   - edits in project  -^> work folder within ~2s
echo   - edits in work folder -^> project within ~2s
echo.
%PY% "app\tools\dlp_sync.py" --status
echo.
pause
