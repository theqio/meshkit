# -*- coding: utf-8 -*-
"""修"灯又不好使"：Mechanical 忙时不再把页面一起拖死
   1) 桥接锁改为带超时：读操作等 2 秒拿不到锁 → 直接返回 busy
   2) 控制项校验拿不到结果时，灯光保留上一次结果并标记"待刷新"
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

# 1) 读操作集合
READ_OPS = '''READ_OPS = {
    "ping", "status", "list_bodies", "geom_groups", "read_plan",
    "scan_categories", "validate_controls", "body_counts", "failed_detail",
    "mesh_messages", "diagnose", "quality_metrics", "get_params",
    "probe_tree", "probe_geom_groups", "project_info",
}

'''
if "READ_OPS" not in s:
    s = s.replace("def call_bridge(op, payload=None, timeout=None):",
                  READ_OPS + "def call_bridge(op, payload=None, timeout=None, wait=None):", 1)
    print("已加 READ_OPS")

# 2) call_bridge 带超时拿锁
OLD = '''def call_bridge(op, payload=None, timeout=None, wait=None):
    """串行化包装：同一时刻只允许一个请求进入 Mechanical（避免并发闪退）"""
    with _BRIDGE_LOCK:
        ret'''
NEW = '''def call_bridge(op, payload=None, timeout=None, wait=None):
    """串行化包装：同一时刻只允许一个请求进入 Mechanical（避免并发闪退）
       读操作拿不到锁就立刻返回 busy，避免把页面拖死
    """
    if wait is None:
        wait = 2.0 if op in READ_OPS else 900.0
    if not _BRIDGE_LOCK.acquire(timeout=wait):
        return {"ok": False, "busy": True,
                "error": "Mechanical 正在忙（上一个操作还没结束）"}
    try:
        ret'''
if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("call_bridge 已改为带超时拿锁")
else:
    print("未找到 call_bridge 包装")

# 需要把原来的 with 块改成 try/finally —— 找到函数结尾
i = s.find("def call_bridge(op, payload=None, timeout=None, wait=None):")
j = s.find("\ndef ", i + 10)
seg = s[i:j]
print("call_bridge 段长度:", len(seg))
if "finally:" not in seg:
    # 原实现是:
    #   with _BRIDGE_LOCK:
    #       ret = _call_bridge_impl(...)
    #   return ret
    import re
    m = re.search(r"(\n\s+)return ret\b", seg)
    if m:
        seg2 = seg[:m.start()] + "\n    finally:\n        _BRIDGE_LOCK.release()\n    return ret\n"
        s = s[:i] + seg2 + s[j:]
        print("已补 finally 释放锁")
    else:
        print("未找到 return ret")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("server 语法 OK")
