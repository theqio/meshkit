@echo off
chcp 65001 >nul
title MeshKit - 加载桥接
setlocal

echo ============================================================
echo   MeshKit  加载桥接代理
echo ============================================================
echo.
echo   已经帮你把命令复制到剪贴板，接着：
echo.
echo     1) 切到 Mechanical 窗口
echo     2) 顶部 Automation（自动化）标签 -> Scripting（脚本）
echo     3) 点一下下面的输入框，按 Ctrl+V，再按 Enter
echo.
echo   命令内容：
echo     execfile("D:/ai/meshkit/app/bridge_agent.py")
echo.
echo ============================================================
echo.

powershell -NoProfile -Command "Set-Clipboard -Value 'execfile(''D:/ai/meshkit/app/bridge_agent.py'')'"
if errorlevel 1 (
  echo [警告] 复制到剪贴板失败，请手动复制下面这一行：
  echo execfile("D:/ai/meshkit/app/bridge_agent.py")
)

start "" http://127.0.0.1:8765

echo 已尝试打开控制台网页 http://127.0.0.1:8765
echo 加载成功后网页右上角「桥接」会变绿。
echo.
pause
endlocal
