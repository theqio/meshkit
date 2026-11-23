# -*- coding: utf-8 -*-
"""自动连接增强：
   1) 保证「Scripting 编辑器」里始终有加载命令（写入 ScriptState 文件，
      新开的 Mechanical 启动时会读到它）
   2) 先直接 Ctrl+F5；没连上再尝试粘贴一次后 Ctrl+F5；仍失败则提示手动
"""
import io
import json
import os
import shutil
import time

P = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

HELPER = r'''
SCRIPT_STATE = os.path.join(os.environ.get("APPDATA", ""),
                            r"Ansys\v221\ACT\preferences",
                            "ScriptStateMechanical_NewScripting.json")

CONNECT_CODE = 'execfile("D:/ai/meshkit/app/bridge_agent.py")'


def _ensure_script_state():
    """把加载命令写进 Mechanical 的 Scripting 脚本状态文件
       这样新开的 Mechanical 打开 Scripting 时编辑器里就有这条命令
    """
    try:
        if not os.path.isfile(SCRIPT_STATE):
            return False
        with open(SCRIPT_STATE, "r", encoding="utf-8") as f:
            data = json.load(f)
        user = data.setdefault("User", {})
        # 找到任意一个已有条目，或新建
        key = None
        for k, v in user.items():
            if isinstance(v, dict):
                key = k
                break
        if key is None:
            key = "New Script  1"
            user[key] = {"Name": key, "Code": "", "Description": "",
                         "FilePath": "", "Edited": False,
                         "BreakPoints": [], "OnDisk": False}
        item = user[key]
        code = (item.get("Code") or "").strip()
        if "bridge_agent" in code:
            return True
        item["Code"] = CONNECT_CODE
        item["Edited"] = False
        with open(SCRIPT_STATE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return True
    except Exception:
        return False


def _scripting_pane_rect(pid):
    """UIA 取 Scripting 面板矩形（物理像素）"""
    try:
        ps = (
            "Add-Type -AssemblyName UIAutomationClient;"
            "Add-Type -AssemblyName UIAutomationTypes;"
            "$AE=[System.Windows.Automation.AutomationElement];"
            "$root=$AE::RootElement;"
            "$all=$root.FindAll([System.Windows.Automation.TreeScope]::Children,"
            "[System.Windows.Automation.Condition]::TrueCondition);"
            "foreach($w in $all){ if($w.Current.ProcessId -eq %d){"
            " $kids=$w.FindAll([System.Windows.Automation.TreeScope]::Descendants,"
            "[System.Windows.Automation.Condition]::TrueCondition);"
            " foreach($k in $kids){ if($k.Current.Name -match 'Mechanical Scripting'){"
            "  $r=$k.Current.BoundingRectangle;"
            "  Write-Output ('R|' + [int]$r.X + '|' + [int]$r.Y + '|' + [int]$r.Width + '|' + [int]$r.Height);"
            "  break } } } }"
        ) % int(pid)
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, timeout=25, creationflags=0x08000000)
        out = (r.stdout or b"").decode("mbcs", "ignore")
        for line in out.splitlines():
            if line.startswith("R|"):
                parts = line.split("|")
                return tuple(int(x) for x in parts[1:5])
    except Exception:
        pass
    return None

'''

if "_ensure_script_state" not in src:
    anchor = "def _mech_auto_connect(pid):"
    src = src.replace(anchor, HELPER.strip() + "\n\n\n" + anchor, 1)
    print("已加 _ensure_script_state / _scripting_pane_rect")

# 在 _mech_auto_connect 里加入：先确保脚本状态、失败后粘贴再试
OLD = '''    # 3) Ctrl+F5 运行（编辑器里有上次的命令，Ctrl+F5 直接跑）
    for attempt in range(3):'''
NEW = '''    # 2.5) 确保脚本状态里有加载命令（新开的 Mechanical 也能读到）
    try:
        _ensure_script_state()
    except Exception:
        pass

    # 3) Ctrl+F5 运行（编辑器里有上次的命令，Ctrl+F5 直接跑）
    for attempt in range(3):'''
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("已插入 script state 保障")

OLD2 = '''    return {"ok": False, "stage": "运行后仍未连接",
            "error": "已自动激活并发送 Ctrl+F5，但代理仍未注册。"
                     "请在 Scripting 面板里点一下代码区，再按 Ctrl+F5"}'''
NEW2 = '''    # 3.5) 兜底：往编辑区粘贴一次命令再 Ctrl+F5
    rect = _scripting_pane_rect(pid)
    if rect:
        try:
            win = find_win()
            if win:
                h2, L2, T2, W2, H2 = win
                # UIA 矩形是物理像素，窗口矩形是逻辑像素 → 换算
                scale = (W2 / float(W2)) if W2 else 1.0
                px = rect[0] - _win_origin_phys_x(pid)
                py = rect[1] - _win_origin_phys_y(pid)
                # 点击面板内靠上位置（避开标题栏/工具条）
                cx = int(px / 2 + 80)
                cy = int(py / 2 + 120)
                user32.SetCursorPos(L2 + cx, T2 + cy)
                time.sleep(0.3)
                user32.mouse_event(0x0002, 0, 0, 0, 0)
                time.sleep(0.1)
                user32.mouse_event(0x0004, 0, 0, 0, 0)
                time.sleep(0.5)
                _set_clipboard(CONNECT_CODE)
                for mod, vk in ((0x11, 0x41), (0x11, 0x56)):
                    user32.keybd_event(mod, 0, 0, 0)
                    time.sleep(0.06)
                    user32.keybd_event(vk, 0, 0, 0)
                    time.sleep(0.06)
                    user32.keybd_event(vk, 0, 2, 0)
                    time.sleep(0.06)
                    user32.keybd_event(mod, 0, 2, 0)
                    time.sleep(0.3)
                user32.keybd_event(0x11, 0, 0, 0)
                time.sleep(0.06)
                user32.keybd_event(0x74, 0, 0, 0)
                time.sleep(0.08)
                user32.keybd_event(0x74, 0, 2, 0)
                time.sleep(0.06)
                user32.keybd_event(0x11, 0, 2, 0)
                for _ in range(8):
                    time.sleep(1.0)
                    if registered():
                        return {"ok": True, "stage": "粘贴后已连接"}
        except Exception:
            pass

    return {"ok": False, "stage": "运行后仍未连接",
            "error": "已自动激活并发送 Ctrl+F5，但代理仍未注册。"
                     "请在 Scripting 面板里点一下代码区，再按 Ctrl+F5"}'''

if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("已加粘贴兜底")

# 辅助：窗口物理原点
ORIGIN = '''

def _win_origin_phys_x(pid):
    try:
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        res = []

        class RECT(ctypes.Structure):
            _fields_ = [("L", ctypes.c_long), ("T", ctypes.c_long),
                        ("R", ctypes.c_long), ("B", ctypes.c_long)]

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb(h, l):
            p = wt.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value == int(pid) and user32.IsWindowVisible(h):
                r = RECT()
                user32.GetWindowRect(h, ctypes.byref(r))
                if r.R - r.L > 800 and r.B - r.T > 600:
                    res.append(r.L)
            return True

        user32.EnumWindows(cb, 0)
        return min(res) if res else 0
    except Exception:
        return 0


def _win_origin_phys_y(pid):
    try:
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        res = []

        class RECT(ctypes.Structure):
            _fields_ = [("L", ctypes.c_long), ("T", ctypes.c_long),
                        ("R", ctypes.c_long), ("B", ctypes.c_long)]

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb(h, l):
            p = wt.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value == int(pid) and user32.IsWindowVisible(h):
                r = RECT()
                user32.GetWindowRect(h, ctypes.byref(r))
                if r.R - r.L > 800 and r.B - r.T > 600:
                    res.append(r.T)
            return True

        user32.EnumWindows(cb, 0)
        return min(res) if res else 0
    except Exception:
        return 0

'''
if "_win_origin_phys_x" not in src:
    src = src.replace("def _ensure_script_state(", ORIGIN.strip() + "\n\n\ndef _ensure_script_state(", 1)
    print("已加窗口原点辅助")

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
compile(io.open(P, encoding="utf-8").read(), P, "exec")
print("server 语法 OK")
