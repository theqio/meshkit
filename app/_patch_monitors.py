# -*- coding: utf-8 -*-
"""识别 Mechanical 窗口所在显示器：
   - 枚举所有显示器（含副屏的负坐标）
   - 找到目标窗口所在屏幕，返回序号/矩形/是否主屏
   - 若窗口在屏幕外（拔掉副屏等），自动挪回主屏可见区
"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

HELPER = r'''
def _monitors():
    """所有显示器矩形：[{index,x,y,w,h,primary}]（物理像素）"""
    out = []
    try:
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                        ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

        proc = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HMONITOR, wt.HDC,
                                  ctypes.POINTER(wt.RECT), wt.LPARAM)

        def cb(hmon, hdc, lprc, lp):
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                r = mi.rcMonitor
                out.append({"x": r.left, "y": r.top,
                            "w": r.right - r.left, "h": r.bottom - r.top,
                            "primary": bool(mi.dwFlags & 1)})
            return 1

        user32.EnumDisplayMonitors(0, 0, proc(cb), 0)
    except Exception:
        pass
    out.sort(key=lambda m: m["x"])
    for i, m in enumerate(out):
        m["index"] = i + 1
    return out


def _window_rect(pid):
    try:
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
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
                if r.R - r.L > 600 and r.B - r.T > 400:
                    res.append((r.L, r.T, r.R - r.L, r.B - r.T))
            return True

        user32.EnumWindows(cb, 0)
        if not res:
            return None
        return max(res, key=lambda x: x[2] * x[3])
    except Exception:
        return None


def window_screen(pid):
    """目标窗口在哪块屏幕：{monitor:{...}, rect:[x,y,w,h], offscreen:bool}"""
    rect = _window_rect(pid)
    mons = _monitors()
    if not rect:
        return {"monitor": None, "rect": None, "offscreen": False,
                "monitors": mons}
    x, y, w, h = rect
    cx, cy = x + w // 2, y + h // 2
    hit = None
    for m in mons:
        if m["x"] <= cx < m["x"] + m["w"] and m["y"] <= cy < m["y"] + m["h"]:
            hit = m
            break
    return {"monitor": hit, "rect": list(rect),
            "offscreen": hit is None, "monitors": mons}


def _ensure_window_visible(pid):
    """窗口整块在屏幕外（例如副屏被拔掉）时，挪回主屏"""
    info = window_screen(pid)
    if not info.get("offscreen"):
        return False
    mons = info.get("monitors") or []
    prim = next((m for m in mons if m.get("primary")), mons[0] if mons else None)
    rect = info.get("rect")
    if not prim or not rect:
        return False
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
                if r.R - r.L > 600 and r.B - r.T > 400:
                    res.append(h)
            return True

        user32.EnumWindows(cb, 0)
        if not res:
            return False
        user32.SetWindowPos(res[0], 0, prim["x"] + 40, prim["y"] + 40,
                            0, 0, 0x0001 | 0x0004 | 0x0010)
        time.sleep(0.3)
        return True
    except Exception:
        return False

'''

if "_monitors(" not in src:
    anchor = "def _foreground_mech_pid():"
    src = src.replace(anchor, HELPER.strip() + "\n\n\n" + anchor, 1)
    print("已加显示器识别")

# instances 里带上屏幕信息
OLD = '''    _fg = _foreground_mech_pid()
    for x in out:
        x["foreground"] = (x["pid"] == _fg)'''
NEW = '''    _fg = _foreground_mech_pid()
    for x in out:
        x["foreground"] = (x["pid"] == _fg)
        try:
            x["screen"] = window_screen(x["pid"]).get("monitor")
        except Exception:
            x["screen"] = None'''
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("instances 已返回屏幕信息")

# 激活前先确保窗口可见
OLD2 = '''def _activate_window(pid):
    """把指定 pid 的 Mechanical 窗口切到前台（AttachThreadInput 方式）"""
    try:'''
NEW2 = '''def _activate_window(pid):
    """把指定 pid 的 Mechanical 窗口切到前台（AttachThreadInput 方式）"""
    try:
        _ensure_window_visible(pid)
    except Exception:
        pass
    try:'''
if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("激活前已检查窗口是否在屏幕外")

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
compile(io.open(P, encoding="utf-8").read(), P, "exec")
print("server 语法 OK")
