# -*- coding: utf-8 -*-
"""连接流程改成全自动尝试：
   激活 → 最大化 → 确保 Scripting 面板 → 定位编辑区粘贴 → Ctrl+F5 运行
   → 轮询代理是否注册 → 成功返回 connected，失败返回 need_manual
"""
import io
import re
import shutil
import time

P = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

FULL = r'''
def _mech_auto_connect(pid):
    """完整自动连接尝试：激活/最大化/开面板/粘贴/Ctrl+F5，返回是否成功"""
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

    def activate(h):
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        time.sleep(0.12)
        cur = k32.GetCurrentThreadId()
        tgt = user32.GetWindowThreadProcessId(h, None)
        user32.AttachThreadInput(cur, tgt, True)
        user32.BringWindowToTop(h)
        user32.SetForegroundWindow(h)
        user32.AttachThreadInput(cur, tgt, False)
        time.sleep(0.3)

    def click(L, T, rx, ry):
        user32.SetCursorPos(int(L + rx), int(T + ry))
        time.sleep(0.25)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.1)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.4)

    def combo(vk_main, vk):
        user32.keybd_event(vk_main, 0, 0, 0)
        time.sleep(0.07)
        user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.07)
        user32.keybd_event(vk, 0, 2, 0)
        time.sleep(0.07)
        user32.keybd_event(vk_main, 0, 2, 0)
        time.sleep(0.2)

    win = find_win()
    if not win:
        return {"ok": False, "stage": "找不到窗口"}
    h, L, T, W, H = win

    # 1) 激活 + 最大化
    activate(h)
    try:
        user32.ShowWindow(h, 3)      # SW_MAXIMIZE
        time.sleep(1.0)
        win = find_win()
        if win:
            h, L, T, W, H = win
    except Exception:
        pass

    # 2) 确保 Scripting 面板
    stage = "面板"
    if not _scripting_pane_open(pid):
        _open_scripting_pane(pid)
        time.sleep(1.0)
        if not _scripting_pane_open(pid):
            _open_scripting_pane(pid)
            time.sleep(1.0)

    # 3) 点编辑区 → 全选 → 粘贴
    stage = "粘贴"
    ed_x = max(1200, int(W * 0.83))
    for ed_y in (216, 240, 260):
        click(L, T, ed_x, ed_y)
        combo(0x11, 0x41)     # Ctrl+A
        combo(0x11, 0x56)     # Ctrl+V
        time.sleep(0.6)

        # 4) Ctrl+F5 运行
        stage = "运行"
        combo(0x11, 0x74)     # Ctrl+F5
        time.sleep(3.0)

        # 5) 验证是否注册
        stage = "验证"
        for _ in range(6):
            f = os.path.join(APP_DIR, "ipc", "agents", "%d.json" % int(pid))
            if os.path.isfile(f):
                try:
                    if _pid_alive(int(pid)):
                        return {"ok": True, "stage": "已连接"}
                except Exception:
                    return {"ok": True, "stage": "已连接"}
            time.sleep(1.0)

    return {"ok": False, "stage": stage,
            "error": "自动运行未生效，请在 Mechanical 的 Scripting 面板点一下运行"
                     "（或 Ctrl+F5）"}

'''

if "_mech_auto_connect" not in src:
    anchor = "def _open_scripting_pane("
    src = src.replace(anchor, FULL.strip() + "\n\n\n" + anchor, 1)
    print("已加 _mech_auto_connect")

# 在 connect_instance 未连接分支改用新流程
OLD = '''    # 未连接：激活 + 确保 Scripting 面板打开 + 复制命令
    act = _activate_window(pid)
    pane = "unknown"
    try:
        if act == "ok":
            if _scripting_pane_open(pid):
                pane = "already"
            else:
                _open_scripting_pane(pid)
                time.sleep(0.8)
                if _scripting_pane_open(pid):
                    pane = "opened"
                else:
                    # 点一次没打开（可能原来就是开着的，被切换关了）→ 再点一次
                    _open_scripting_pane(pid)
                    time.sleep(0.8)
                    pane = "opened" if _scripting_pane_open(pid) else "failed"
    except Exception:
        pane = "err"
    if act not in ("ok",):
        try:
            act2 = _activate_window_ps(pid)
            if act2 == "ok":
                act = "ok"
        except Exception:
            pass
    clip = _set_clipboard(MECH_CONNECT_CMD)'''
NEW = '''    # 未连接：先放命令进剪贴板，再走全自动尝试
    clip = _set_clipboard(MECH_CONNECT_CMD)
    auto = {"ok": False, "stage": "未开始"}
    try:
        auto = _mech_auto_connect(pid)
    except Exception as e:
        auto = {"ok": False, "stage": "异常", "error": str(e)[:160]}
    if auto.get("ok"):
        return {"ok": True, "connected": True, "pid": pid,
                "message": "已自动连接并切换",
                "stage": auto.get("stage")}
    act = auto.get("stage") or "失败"
    pane = "auto"'''
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("connect 已改为全自动尝试")
else:
    print("警告：未找到 connect 未连接分支")

OLD2 = '''    return {"ok": True, "connected": False, "pid": pid,
            "activated": act, "pane": pane, "clipboard": clip,
            "command": MECH_CONNECT_CMD,
            "message": "已激活该 Mechanical 窗口并把命令放进剪贴板，"
                       "在 Mechanical 的 Scripting 输入框里 Ctrl+V 再回车"}'''
NEW2 = '''    return {"ok": True, "connected": False, "pid": pid,
            "activated": "ok", "pane": pane, "clipboard": clip,
            "stage": auto.get("stage"),
            "command": MECH_CONNECT_CMD,
            "message": "自动连接未成功（停在：%s）。请到 Mechanical 的 "
                       "Scripting 面板手动点运行或按 Ctrl+F5；命令已在剪贴板。"
                       % (auto.get("stage") or "未知")}'''
if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("返回值已更新")
else:
    print("警告：未找到返回段")

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
compile(io.open(P, encoding="utf-8").read(), P, "exec")
print("server 语法 OK")
