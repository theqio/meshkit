@echo off
chcp 65001 >nul
title MeshKit - 加载桥接
setlocal

echo ============================================================
echo   MeshKit  加载桥接代理
echo ============================================================
echo.

rem 命令放进剪贴板
powershell -NoProfile -Command "Set-Clipboard -Value 'execfile(''D:/ai/meshkit/app/bridge_agent.py'')" >nul 2>&1
if errorlevel 1 (
  echo [警告] 复制到剪贴板失败，请手动复制下面这一行：
  echo   execfile("D:/ai/meshkit/app/bridge_agent.py")
)

rem 控制台已在运行就不再打开新网页
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue; if ($c) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 (
  echo 控制台未运行，正在打开 http://127.0.0.1:8765 ...
  start "" http://127.0.0.1:8765
) else (
  echo 控制台已在运行，不再重复打开网页。
)

echo.
echo   命令已复制到剪贴板，接着在 Mechanical 里：
echo     1) 顶部 Automation（自动化） -^> Scripting（脚本）
echo     2) 点一下脚本框
echo     3) Ctrl+V 粘贴，再按 Enter（或点绿色 ▶）
echo.
echo   网页右上角「实例」里的状态会在 5 秒内自动变成「已连接」。
echo.
pause
endlocal
