# -*- coding: utf-8 -*-
"""把"防止打错实例"做成硬约束：
   1) 前台判定改为看进程（任何 AnsysWBU），不再只认已注册代理
   2) 写模型前：目标不在前台 → 尝试激活；激活后仍不在前台 → 直接拒绝执行
   3) 返回里带上 front_pid / blocked 信息，页面可显示
"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

OLD1 = '''def _foreground_mech_pid():
    """当前前台窗口如果是 Mechanical，返回其 pid，否则 None"""
    try:
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        h = user32.GetForegroundWindow()
        if not h:
            return None
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        p = int(pid.value)
        for a in _agents_raw():
            if int(a.get("pid") or 0) == p:
                return p
        return None
    except Exception:
        return None'''

NEW1 = '''def _foreground_mech_pid():
    """当前前台窗口如果是 Mechanical 进程，返回其 pid，否则 None
       （按进程判定，未注册代理的 Mechanical 也算）
    """
    try:
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        h = user32.GetForegroundWindow()
        if not h:
            return None
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        p = int(pid.value)
        # 先看已注册代理
        for a in _agents_raw():
            if int(a.get("pid") or 0) == p:
                return p
        # 再看所有 Mechanical 进程（覆盖未注册代理的窗口）
        try:
            for item in _list_mech_procs():
                if int(item.get("pid") or 0) == p:
                    return p
        except Exception:
            pass
        return None
    except Exception:
        return None'''

if OLD1 in src:
    src = src.replace(OLD1, NEW1, 1)
    print("前台判定已扩展到所有 Mechanical 进程")
else:
    print("未找到 _foreground_mech_pid")

OLD2 = '''def ensure_target_front():
    """把目标实例切到前台；返回 (是否切换过, 切换前的前台 pid)"""
    tgt = target_pid()
    if not tgt:
        return False, None
    front = _foreground_mech_pid()
    if front == tgt:
        return False, front
    try:
        act = _activate_window(int(tgt))
        return (act == "ok"), front
    except Exception:
        return False, front'''

NEW2 = '''def ensure_target_front(strict=True):
    """确保目标实例在前台
       返回 dict: {ok, switched, front_before, front_after}
       strict=True 且最终不在前台 → ok=False（调用方应拒绝执行）
    """
    tgt = target_pid()
    if not tgt:
        return {"ok": False, "switched": False, "front_before": None,
                "front_after": None, "error": "没有可用的目标实例"}
    front = _foreground_mech_pid()
    if front == tgt:
        return {"ok": True, "switched": False, "front_before": front,
                "front_after": front}
    switched = False
    try:
        if _activate_window(int(tgt)) == "ok":
            switched = True
    except Exception:
        pass
    time.sleep(0.35)
    after = _foreground_mech_pid()
    if after == tgt:
        return {"ok": True, "switched": switched, "front_before": front,
                "front_after": after}
    if not strict:
        return {"ok": True, "switched": switched, "front_before": front,
                "front_after": after}
    return {"ok": False, "switched": switched, "front_before": front,
            "front_after": after,
            "error": "目标实例不在前台（前台是 %s），已阻止本次写操作"
                     % (after if after else "其它窗口")}'''

if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("ensure_target_front 已改为硬校验")
else:
    print("未找到 ensure_target_front")

# /api/op 使用硬校验
OLD3 = '''                if op in MUTATING_OPS:
                    try:
                        ensure_target_front()
                    except Exception:
                        pass'''
NEW3 = '''                if op in MUTATING_OPS:
                    try:
                        _guard = ensure_target_front(strict=True)
                    except Exception:
                        _guard = {"ok": True}
                    if not _guard.get("ok"):
                        return self._json({
                            "ok": False, "blocked": True,
                            "error": _guard.get("error") or "目标实例不在前台",
                            "front_pid": _guard.get("front_after"),
                            "target_pid": target_pid()})'''
if OLD3 in src:
    src = src.replace(OLD3, NEW3, 1)
    print("/api/op 已改为拒绝执行")

# scan_apply 同样
OLD4 = '''                try:
                    ensure_target_front()
                except Exception:
                    pass'''
NEW4 = '''                try:
                    _guard = ensure_target_front(strict=True)
                except Exception:
                    _guard = {"ok": True}
                if not _guard.get("ok"):
                    return self._json({
                        "ok": False, "blocked": True,
                        "error": _guard.get("error") or "目标实例不在前台",
                        "front_pid": _guard.get("front_after"),
                        "target_pid": target_pid()})'''
if OLD4 in src:
    src = src.replace(OLD4, NEW4, 1)
    print("scan_apply 已改为拒绝执行")

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
compile(io.open(P, encoding="utf-8").read(), P, "exec")
print("server 语法 OK")
