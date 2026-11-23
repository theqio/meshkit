# -*- coding: utf-8 -*-
"""让 /api/instances 立刻返回：体数/分组数改成后台刷新 + 缓存
   - 有缓存就直接用（哪怕略旧）
   - 缓存过期则在后台线程刷新，不阻塞页面
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

OLD = '''def instance_summary(pid, ttl=20):
    """某实例的 体数/分组数（带缓存，避免频繁打扰 Mechanical）"""
    now = time.time()
    c = _INST_CACHE.get(int(pid))
    if c and now - c[0] < ttl:
        return c[1]
    info = {"bodies": None, "groups": None, "ungrouped": None}
    try:
        lb = call_bridge_pid(pid, "list_bodies", {}, timeout=25)
        if lb.get("ok"):
            info["bodies"] = len(lb.get("bodies") or [])
        gg = call_bridge_pid(pid, "geom_groups", {}, timeout=25)
        if gg.get("ok"):
            info["groups"] = len(gg.get("groups") or [])
            info["ungrouped"] = len(gg.get("ungrouped") or [])
    except Exception:
        pass
    _INST_CACHE[int(pid)] = (now, info)
    return info'''

NEW = '''_INST_BUSY = {}


def _refresh_summary(pid):
    """后台线程：真正去问 Mechanical"""
    pid = int(pid)
    try:
        info = {"bodies": None, "groups": None, "ungrouped": None}
        lb = call_bridge_pid(pid, "list_bodies", {}, timeout=20)
        if lb.get("ok"):
            info["bodies"] = len(lb.get("bodies") or [])
        gg = call_bridge_pid(pid, "geom_groups", {}, timeout=20)
        if gg.get("ok"):
            info["groups"] = len(gg.get("groups") or [])
            info["ungrouped"] = len(gg.get("ungrouped") or [])
        if info["bodies"] is not None or info["groups"] is not None:
            _INST_CACHE[pid] = (time.time(), info)
    except Exception:
        pass
    finally:
        _INST_BUSY[pid] = False


def instance_summary(pid, ttl=30):
    """立即返回缓存；缓存过期则后台刷新（不阻塞页面）"""
    pid = int(pid)
    now = time.time()
    c = _INST_CACHE.get(pid)
    if c and now - c[0] < ttl:
        return c[1]
    if not _INST_BUSY.get(pid):
        _INST_BUSY[pid] = True
        try:
            import threading
            threading.Thread(target=_refresh_summary, args=(pid,),
                             daemon=True).start()
        except Exception:
            _INST_BUSY[pid] = False
    if c:
        return c[1]
    return {"bodies": None, "groups": None, "ungrouped": None}'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    io.open(S, "w", encoding="utf-8", newline="\n").write(s)
    print("instance_summary 已改为后台刷新")
else:
    print("未找到 instance_summary 旧实现")

compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
