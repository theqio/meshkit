# -*- coding: utf-8 -*-
"""控制项清单改用 read_plan（0.8 秒），代替慢的 scan_categories
   - 名字去掉 SIZE_/METHOD_ 前缀，映射回类别名
   - 缓存 5 分钟，后台刷新
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

OLD = '''            try:
                sc = call_bridge("scan_categories", timeout=240)
                if sc.get("ok"):
                    _CTRL_CACHE[pid] = (time.time(),
                                        [c.get("name") for c in
                                         (sc.get("categories") or [])
                                         if c.get("name")])
            except Exception:
                pass'''

NEW = '''            try:
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
            except Exception:
                pass'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("控制项缓存改用 read_plan")
else:
    print("未找到 scan_categories 片段")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
