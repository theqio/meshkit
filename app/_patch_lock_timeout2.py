# -*- coding: utf-8 -*-
"""桥接锁超时：读操作拿不到锁立刻返回 busy，避免 Mechanical 忙时把页面拖死"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

OLD = '''def call_bridge(op, payload=None, timeout=None):
    """串行化包装：同一时刻只允许一个请求进入 Mechanical（避免并发闪退）"""
    with _BRIDGE_LOCK:
        return _call_bridge_impl(op, payload, timeout)'''

NEW = '''READ_OPS = frozenset((
    "ping", "status", "list_bodies", "geom_groups", "read_plan",
    "scan_categories", "validate_controls", "body_counts", "failed_detail",
    "mesh_messages", "diagnose", "quality_metrics", "get_params",
    "probe_tree", "probe_geom_groups", "project_info", "probe_ctrl_scope",
))


def call_bridge(op, payload=None, timeout=None, wait=None):
    """串行化包装：同一时刻只允许一个请求进入 Mechanical（避免并发闪退）
       读操作拿不到锁就立刻返回 busy，避免 Mechanical 忙时把页面一起拖死
    """
    if wait is None:
        wait = 2.0 if op in READ_OPS else 900.0
    if not _BRIDGE_LOCK.acquire(timeout=wait):
        return {"ok": False, "busy": True,
                "error": "Mechanical 正在忙（上一个操作还没结束）"}
    try:
        return _call_bridge_impl(op, payload, timeout)
    finally:
        try:
            _BRIDGE_LOCK.release()
        except Exception:
            pass'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("call_bridge 已加锁超时")
else:
    print("未找到 call_bridge 原始定义")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
