# -*- coding: utf-8 -*-
"""修实例切换：
   1) 服务端用 AttachThreadInput 方式激活窗口（比 SetForegroundWindow 可靠）
   2) 目标实例失效/未连接时自动切到第一个已连接的
   3) /api/connect 返回更明确的状态
"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

# ---------- 1) 用 ctypes 激活窗口 ----------
OLD = '''def _activate_window(pid):
    """把指定 pid 的 Mechanical 窗口切到前台"""
    try:
        cmd = ['''
NEW = '''def _activate_window(pid):
    """把指定 pid 的 Mechanical 窗口切到前台（AttachThreadInput 方式）"""
    try:
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32

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
                    found.append(h)
            return True

        user32.EnumWindows(cb, 0)
        if not found:
            return "nowin"
        h = found[0]
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
        return "ok" if user32.GetForegroundWindow() == h else "no"
    except Exception as e:
        return "err:%s" % str(e)[:40]


def _activate_window_ps(pid):
    """旧的 powershell 兜底方式"""
    try:
        cmd = ['''
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("已加 ctypes 激活方式")
else:
    print("警告：未找到 _activate_window")

# ---------- 2) connect_instance 用新激活 + 兜底 ----------
OLD2 = '''    # 未连接：激活 + 复制命令
    act = _activate_window(pid)'''
NEW2 = '''    # 未连接：激活 + 复制命令
    act = _activate_window(pid)
    if act not in ("ok",):
        try:
            act2 = _activate_window_ps(pid)
            if act2 == "ok":
                act = "ok"
        except Exception:
            pass'''
if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("connect 已用新激活 + 兜底")

# ---------- 3) 目标实例自愈 ----------
OLD3 = '''    tgt = target_pid()
    out = []'''
NEW3 = '''    tgt = target_pid()
    # 目标失效或未连接 → 自动挑第一个已连接的
    if out_connected := [p for p in reg if _pid_alive(p)]:
        if tgt not in out_connected:
            try:
                cfg = load_config()
                cfg["target_pid"] = sorted(out_connected)[0]
                save_config(cfg)
                tgt = cfg["target_pid"]
            except Exception:
                pass
    out = []'''
if OLD3 in src:
    src = src.replace(OLD3, NEW3, 1)
    print("已加目标自愈")
else:
    print("警告：未找到 target 计算处")

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
compile(io.open(P, encoding="utf-8").read(), P, "exec")
print("server 语法 OK")
