# -*- coding: utf-8 -*-
"""解决"一次卡住、全局变慢/红灯"的根本问题：
   1) 桥接锁改成「按实例」加锁 —— 一个实例卡住不影响另一个
   2) 写操作等锁 180 秒、读操作 2 秒；等不到就返回 busy（页面显示忙，不装死）
   3) 提供 /api/bridge/reset 强制解除卡住的锁
   4) 控制项校验拿不到时，灯显示灰色"校验中"，不再误显示红灯
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

# 1) 按实例锁 + 重置
OLD = '''def call_bridge(op, payload=None, timeout=None, wait=None):
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

NEW = '''_PID_LOCKS = {}
_PID_LOCK_TS = {}


def _pid_lock(pid):
    """每个实例一把锁：一个实例卡住不影响另一个"""
    pid = int(pid or 0)
    lk = _PID_LOCKS.get(pid)
    if lk is None:
        lk = threading.RLock()
        _PID_LOCKS[pid] = lk
    return lk


def reset_bridge_locks():
    """强制丢弃所有按实例锁（卡死时用）"""
    n = len(_PID_LOCKS)
    _PID_LOCKS.clear()
    _PID_LOCK_TS.clear()
    return n


def call_bridge(op, payload=None, timeout=None, wait=None):
    """串行化包装（按实例）：读操作拿不到锁立刻返回 busy，写操作最多等 180 秒"""
    pid = target_pid()
    if pid is None:
        return {"ok": False, "error": "没有在线的 Mechanical 实例"}
    lk = _pid_lock(pid)
    if wait is None:
        wait = 2.0 if op in READ_OPS else 180.0
    if not lk.acquire(timeout=wait):
        held = round(time.time() - _PID_LOCK_TS.get(int(pid), time.time()), 1)
        return {"ok": False, "busy": True,
                "error": "该实例正在忙（上一个操作已占用 %.0f 秒），可稍候或点「解除卡住」" % max(held, 0)}
    _PID_LOCK_TS[int(pid)] = time.time()
    try:
        return _call_bridge_impl(op, payload, timeout)
    finally:
        try:
            lk.release()
        except Exception:
            pass
        _PID_LOCK_TS.pop(int(pid), None)'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("已改为按实例锁")
else:
    print("未找到 call_bridge 包装")

# 2) 重置接口
ROUTE = '''            if path == "/api/bridge/reset":
                _n = reset_bridge_locks()
                try:
                    cache_invalidate()
                except Exception:
                    pass
                return self._json({"ok": True, "released": _n,
                                   "message": "已重置桥接锁，可重新操作"})

'''
if "/api/bridge/reset" not in s:
    anchor = '            if path == "/api/agent/reload":'
    pos = s.find(anchor, s.find("def do_POST"))
    if pos > 0:
        s = s[:pos] + ROUTE + s[pos:]
        print("已加 /api/bridge/reset")

# 3) 校验不可用时灯显示灰色
OLD2 = '''    if not _ok_lights:
        for _c in cats:
            if _c.get("light") != "muted":
                _c["light_reason"] = "控制项状态读取中…"'''
NEW2 = '''    if not _ok_lights:
        for _c in cats:
            if _c.get("light") != "muted":
                _c["light"] = "muted"
                _c["light_reason"] = "控制项校验暂不可用（Mechanical 忙），稍后自动刷新"'''
if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)
    print("校验不可用时改为灰灯")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("server 语法 OK")
