# -*- coding: utf-8 -*-
"""修目标实例在长任务（如 mesh）期间被抢走的问题：
   - 代理文件在忙时不刷新 → 之前会误判"离线" → 回退到另一个实例
   - 现在无论是否有代理文件，都按进程存活判定目标
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

OLD = '''    alive = [a["pid"] for a in _agents_raw()]
    procs = set()
    if not alive:
        try:
            procs = set(p["pid"] for p in _list_mech_procs())
        except Exception:
            procs = set()'''

NEW = '''    alive = [a["pid"] for a in _agents_raw()]
    # 进程集合必须一直算（带缓存，毫秒级）：
    # 长任务期间代理文件不刷新，若只看代理文件会把目标误判为离线
    procs = set()
    try:
        procs = set(p["pid"] for p in _list_mech_procs())
    except Exception:
        procs = set()'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("target_pid 已改为始终按进程判定")
else:
    print("未找到 target_pid 片段")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
