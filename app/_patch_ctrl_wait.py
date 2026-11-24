# -*- coding: utf-8 -*-
"""控制项清单：首次同步读取（read_plan 0.8s），之后走缓存
   解决"缓存还没热，就把 muted 结果缓存下来"的问题
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

OLD = '''def ctrl_names_cached(pid=None, ttl=300):
    """Mesh 控制项名字缓存；过期后台刷新。返回 (set 或 None, age)"""
    pid = pid or target_pid()
    now = time.time()
    hit = _CTRL_CACHE.get(pid)
    if hit and now - hit[0] < ttl:
        return set(hit[1]), round(now - hit[0], 1)'''

NEW = '''def ctrl_names_cached(pid=None, ttl=300, wait=False):
    """Mesh 控制项名字缓存；wait=True 时同步读一次（约 0.8 秒）"""
    pid = pid or target_pid()
    now = time.time()
    hit = _CTRL_CACHE.get(pid)
    if hit and now - hit[0] < ttl:
        return set(hit[1]), round(now - hit[0], 1)

    if wait:
        try:
            rp = call_bridge("read_plan", timeout=120)
            names = []
            for c in (rp.get("controls") or []):
                nm = (c.get("name") or "").strip()
                if not nm:
                    continue
                for pre in ("SIZE_FIX_", "SIZE_", "METHOD_", "SWEEP_", "SHELL_"):
                    if nm.startswith(pre):
                        nm = nm[len(pre):]
                        break
                names.append(nm)
            if names:
                _CTRL_CACHE[pid] = (time.time(), names)
                _CTRL_BUSY[pid] = False
                return set(names), 0.0
        except Exception:
            pass'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("ctrl_names_cached 已支持 wait")
else:
    print("未找到 ctrl_names_cached")

# fast 分支改为同步等待
OLD2 = '''        if fast:
            _cn, _cage = ctrl_names_cached()'''
NEW2 = '''        if fast:
            _cn, _cage = ctrl_names_cached(wait=True)'''
if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)
    print("fast 分支已改为同步读取控制项")
else:
    print("未找到 fast 分支")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
