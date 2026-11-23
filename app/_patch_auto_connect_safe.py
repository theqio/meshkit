# -*- coding: utf-8 -*-
"""安全版自动连接：不盲粘贴
   激活 → 最大化 → 确保 Scripting 面板 → Ctrl+F5（最多 3 次）→ 验证注册
   成功则切换目标；失败返回 need_manual，附手动步骤
"""
import io
import re
import shutil
import time

P = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

i = src.find("def _mech_auto_connect(pid):")
if i < 0:
    raise SystemExit("找不到 _mech_auto_connect")
j = src.find("\ndef ", i + 10)
print("替换区间:", i, j)

NEW = r'''def _mech_auto_connect(pid):
    """安全版：只激活/最大化/开面板/Ctrl+F5，不盲粘贴"""
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32

    class RECT(ctypes.Structure):
        _fields_ = [("L", ctypes.c_long), ("T", ctypes.c_long),
                    ("R", ctypes.c_long), ("B", ctypes.c_long)]

    def find_win():
        res = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb(h, l):
            p = wt.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value == int(pid) and user32.IsWindowVisible(h):
                r = RECT()
                user32.GetWindowRect(h, ctypes.byref(r))
                if r.R - r.L > 800 and r.B - r.T > 600:
                    res.append((h, r.L, r.T, r.R - r.L, r.B - r.T))
            return True

        user32.EnumWindows(cb, 0)
        return max(res, key=lambda x: x[3] * x[4]) if res else None

    def agent_file():
        return os.path.join(APP_DIR, "ipc", "agents", "%d.json" % int(pid))

    def registered():
        f = agent_file()
        if not os.path.isfile(f):
            return False
        try:
            return _pid_alive(int(pid))
        except Exception:
            return True

    win = find_win()
    if not win:
        return {"ok": False, "stage": "找不到窗口"}
    h, L, T, W, H = win

    # 1) 激活 + 最大化
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    time.sleep(0.12)
    cur = k32.GetCurrentThreadId()
    tgt = user32.GetWindowThreadProcessId(h, None)
    user32.AttachThreadInput(cur, tgt, True)
    user32.BringWindowToTop(h)
    user32.SetForegroundWindow(h)
    user32.AttachThreadInput(cur, tgt, False)
    time.sleep(0.35)
    try:
        user32.ShowWindow(h, 3)      # 最大化
        time.sleep(0.8)
    except Exception:
        pass

    # 2) 确保 Scripting 面板
    if not _scripting_pane_open(pid):
        _open_scripting_pane(pid)
        time.sleep(1.0)
        if not _scripting_pane_open(pid):
            _open_scripting_pane(pid)
            time.sleep(1.0)
    if not _scripting_pane_open(pid):
        return {"ok": False, "stage": "面板未打开",
                "error": "无法自动打开 Scripting 面板，请手动打开后按 Ctrl+F5"}

    # 3) Ctrl+F5 运行（编辑器里有上次的命令，Ctrl+F5 直接跑）
    for attempt in range(3):
        user32.keybd_event(0x11, 0, 0, 0)
        time.sleep(0.07)
        user32.keybd_event(0x74, 0, 0, 0)
        time.sleep(0.08)
        user32.keybd_event(0x74, 0, 2, 0)
        time.sleep(0.07)
        user32.keybd_event(0x11, 0, 2, 0)
        time.sleep(0.6)
        for _ in range(5):
            if registered():
                return {"ok": True, "stage": "已连接", "attempt": attempt + 1}
            time.sleep(1.0)

    return {"ok": False, "stage": "运行后仍未连接",
            "error": "已自动激活并发送 Ctrl+F5，但代理仍未注册。"
                     "请在 Scripting 面板里点一下代码区，再按 Ctrl+F5"}
'''

src = src[:i] + NEW + src[j:]
io.open(P, "w", encoding="utf-8", newline="\n").write(src)
compile(io.open(P, encoding="utf-8").read(), P, "exec")
print("已替换为安全版 auto connect，语法 OK")
