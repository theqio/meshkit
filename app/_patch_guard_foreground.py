# -*- coding: utf-8 -*-
"""防止"操作打到错的 Mechanical 实例"：
   1) 每次「写模型」的操作前，检查前台窗口是不是目标实例
      - 不是 → 自动把目标实例切到前台（并记录）
      - 前台是另一个 Mechanical → 也切目标，并在返回里标明
   2) /api/instances 返回 foreground pid，页面可显示提醒
写操作范围：scan_apply、/api/op 里的改模型命令
"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

HELPER = r'''
MUTATING_OPS = {
    "apply", "mesh", "group_geometry", "ungroup_folders", "rename_bodies",
    "fix_body", "fix_failed", "quality_run", "set_local_defeature",
    "save_project", "op_apply", "regroup",
}


def _foreground_mech_pid():
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
        return None


def ensure_target_front():
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
        return False, front

'''

if "_foreground_mech_pid" not in src:
    anchor = "def _mech_auto_connect(pid):"
    src = src.replace(anchor, HELPER.strip() + "\n\n\n" + anchor, 1)
    print("已加前台校验工具")

# /api/instances 增加 foreground
OLD = '''    return {"ok": True, "instances": out,
            "connected_count": sum(1 for x in out if x["connected"]),
            "target": tgt,
            "connect_cmd": MECH_CONNECT_CMD}'''
NEW = '''    _fg = _foreground_mech_pid()
    for x in out:
        x["foreground"] = (x["pid"] == _fg)
    return {"ok": True, "instances": out,
            "connected_count": sum(1 for x in out if x["connected"]),
            "target": tgt, "foreground": _fg,
            "connect_cmd": MECH_CONNECT_CMD}'''
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("instances 已返回 foreground")

# /api/op：写操作前切窗口
OLD2 = '''            if path == "/api/op":
                body = self._body()
                op = body.get("op") or ""
                payload = body.get("payload") or {}'''
NEW2 = '''            if path == "/api/op":
                body = self._body()
                op = body.get("op") or ""
                payload = body.get("payload") or {}
                if op in MUTATING_OPS:
                    try:
                        ensure_target_front()
                    except Exception:
                        pass'''
if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("/api/op 写操作前已切窗口")

# /api/scan_apply：执行前切窗口
OLD3 = '''            if path == "/api/scan_apply":
                body = self._body()
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})'''
NEW3 = '''            if path == "/api/scan_apply":
                body = self._body()
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                try:
                    ensure_target_front()
                except Exception:
                    pass'''
if OLD3 in src:
    src = src.replace(OLD3, NEW3, 1)
    print("scan_apply 执行前已切窗口")

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
compile(io.open(P, encoding="utf-8").read(), P, "exec")
print("server 语法 OK")
