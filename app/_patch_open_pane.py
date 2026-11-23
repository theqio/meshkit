# -*- coding: utf-8 -*-
"""连接时自动准备粘贴环境：
   1) 激活目标 Mechanical 窗口
   2) 若 Scripting 面板没打开，自动点 Automation 里的 Scripting 按钮打开
      （通过 UIA 检测 ACTConsole 是否存在，避免重复点开/误关）
   3) 命令放入剪贴板
之后用户只需在面板里点一次绿色 ▶（或 Ctrl+Enter）
"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

HELPER = r'''
def _scripting_pane_open(pid):
    """UIA 检查该窗口是否已有 Scripting 面板（ACTConsole）"""
    try:
        ps = (
            "Add-Type -AssemblyName UIAutomationClient;"
            "Add-Type -AssemblyName UIAutomationTypes;"
            "$AE=[System.Windows.Automation.AutomationElement];"
            "$root=$AE::RootElement;"
            "$all=$root.FindAll([System.Windows.Automation.TreeScope]::Children,"
            "[System.Windows.Automation.Condition]::TrueCondition);"
            "foreach($w in $all){ if($w.Current.ProcessId -eq %d){"
            " $c=New-Object System.Windows.Automation.PropertyCondition("
            "[System.Windows.Automation.AutomationElement]::NameProperty,'ACTConsole');"
            " $p=$w.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$c);"
            " if($p -ne $null){'yes'}else{'no'}; break } }"
        ) % int(pid)
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, timeout=25, creationflags=0x08000000)
        out = (r.stdout or b"").decode("mbcs", "ignore").strip().lower()
        return "yes" in out
    except Exception:
        return False


def _open_scripting_pane(pid):
    """点 Automation 标签下的 Scripting 按钮（窗口相对坐标 155,76）"""
    try:
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32

        class RECT(ctypes.Structure):
            _fields_ = [("L", ctypes.c_long), ("T", ctypes.c_long),
                        ("R", ctypes.c_long), ("B", ctypes.c_long)]

        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb(h, l):
            p = wt.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value == int(pid) and user32.IsWindowVisible(h):
                r = RECT()
                user32.GetWindowRect(h, ctypes.byref(r))
                if r.R - r.L > 800 and r.B - r.T > 600:
                    found.append((h, r.L, r.T))
            return True

        user32.EnumWindows(cb, 0)
        if not found:
            return "nowin"
        h, L, T = found[0]
        user32.SetForegroundWindow(h)
        time.sleep(0.3)
        user32.SetCursorPos(L + 155, T + 76)
        time.sleep(0.25)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.1)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(1.2)
        return "ok"
    except Exception as e:
        return "err:%s" % str(e)[:40]

'''

if "_open_scripting_pane" not in src:
    anchor = "def _activate_window("
    src = src.replace(anchor, HELPER.strip() + "\n\n\n" + anchor, 1)
    print("已加 Scripting 面板辅助函数")

# 在 connect_instance 的未连接分支里调用
OLD = '''    # 未连接：激活 + 复制命令
    act = _activate_window(pid)'''
NEW = '''    # 未连接：激活 + 确保 Scripting 面板打开 + 复制命令
    act = _activate_window(pid)
    pane = "unknown"
    try:
        if act == "ok" and not _scripting_pane_open(pid):
            pane = _open_scripting_pane(pid)
        elif act == "ok":
            pane = "already"
    except Exception:
        pane = "err"'''
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("connect 已加入自动打开面板")

OLD2 = '''    return {"ok": True, "connected": False, "pid": pid,
            "activated": act, "clipboard": clip,'''
NEW2 = '''    return {"ok": True, "connected": False, "pid": pid,
            "activated": act, "pane": pane, "clipboard": clip,'''
if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
compile(io.open(P, encoding="utf-8").read(), P, "exec")
print("server 语法 OK")
