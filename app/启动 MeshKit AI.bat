@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title MeshKit AI

set "APPDIR=%~dp0"
set "APPDIR=%APPDIR:~0,-1%"

echo ============================================================
echo   MeshKit AI  -  ANSYS Mechanical 网格智能助手
echo ============================================================
echo.

rem ---- 1) 先找 DSH 自带 Python（本机可用）
set "PY="
for /d %%D in ("%USERPROFILE%\.dsh\dsh-runtimes\*") do (
  if exist "%%~fD\dependencies\python\python.exe" (
    set "PY=%%~fD\dependencies\python\python.exe"
    goto :found
  )
)

rem ---- 2) 再找系统里的真实 Python（排除 WindowsApps 占位符）
for %%P in (python.exe) do (
  for /f "delims=" %%F in ('where %%P 2^>nul') do (
    echo %%F | findstr /i "WindowsApps" >nul
    if errorlevel 1 (
      set "PY=%%F"
      goto :found
    )
  )
)
for %%D in ("C:\Python312" "C:\Python311" "C:\Python310" "C:\Python39" "%LOCALAPPDATA%\Programs\Python\Python312" "%LOCALAPPDATA%\Programs\Python\Python311") do (
  if exist "%%~D\python.exe" (
    set "PY=%%~fD\python.exe"
    goto :found
  )
)

:found
if "%PY%"=="" (
  echo [错误] 没找到可用的 Python 3。
  echo        请安装 Python 3.9+ 后重试，或把 python.exe 路径写入本 bat 的 PY 变量。
  pause
  exit /b 1
)

echo [1/2] 使用 Python: %PY%
"%PY%" -c "import sys;print('      版本:',sys.version.split()[0])"

echo.
echo [2/2] 启动服务并打开浏览器 ...
start "" http://127.0.0.1:8765
echo.
echo ------------------------------------------------------------
echo  在 Mechanical 里运行（每次重启 Mechanical 后都要跑一次）：
echo.
echo     execfile("D:/ai/meshkit/app/bridge_agent.py")
echo.
echo  关闭本窗口即停止服务。
echo ------------------------------------------------------------
echo.
"%PY%" "%APPDIR%\server.py"

endlocal
pause
