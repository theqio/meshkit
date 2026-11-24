# -*- coding: utf-8 -*-
"""让"自载"安全：给代理入口加 __main__ 守卫，重载时不再停旧定时器
   - 旧定时器继续跑，新代码通过替换模块全局变量立即生效
   - 不会再出现"重载后代理不再响应"的情况
"""
import io
import shutil
import time

A = r"D:\ai\meshkit\app\bridge_agent.py"
shutil.copyfile(A, A + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(A, encoding="utf-8-sig").read()

# 1) 入口加守卫
if "\nstart()\n" in s and 'if __name__ == "__main__":' not in s[-400:]:
    s = s[:s.rfind("\nstart()\n")] + '\nif __name__ == "__main__":\n    start()\n'
    print("入口已加 __main__ 守卫")
else:
    print("入口守卫已存在或格式不同")

s = s.replace('u"2026-12-02.8"', 'u"2026-12-02.9"', 1)
io.open(A, "w", encoding="utf-8-sig").write(s)
compile(io.open(A, encoding="utf-8-sig").read(), A, "exec")
print("版本 2026-12-02.9，语法 OK")

# 2) reload_self 改为不停定时器
P = r"D:\ai\meshkit\app\reload_self.py"
r = io.open(P, encoding="utf-8").read()
OLD = '''    stopped = 0
    for t in list(g.get("_keep") or []):
        try:
            t.Stop()
            stopped += 1
        except Exception:
            pass
    g["_keep"] = []
    res["stopped"] = stopped'''
NEW = '''    # 不停旧定时器：它们会继续调用同一命名空间里的（已被替换的）函数
    res["timers_alive"] = len(g.get("_keep") or [])'''
if OLD in r:
    r = r.replace(OLD, NEW, 1)
    print("reload_self 已改为不停定时器")
OLD2 = '''    try:
        if not (g.get("_keep") or []):
            starter = g.get("start")
            if starter is not None:
                starter()
                res["started"] = True
            else:
                res["started"] = False
        else:
            res["started"] = "already"
            res["timers"] = len(g.get("_keep") or [])
    except Exception:
        res["start_error"] = traceback.format_exc()[-300:]'''
NEW2 = '''    # 如果确实没有任何定时器（比如之前被停过），补启动一次
    try:
        if not (g.get("_keep") or []):
            starter = g.get("start")
            if starter is not None:
                starter()
                res["started"] = True
        else:
            res["timers"] = len(g.get("_keep") or [])
    except Exception:
        res["start_error"] = traceback.format_exc()[-300:]'''
if OLD2 in r:
    r = r.replace(OLD2, NEW2, 1)
    print("reload_self 兜底保留")
io.open(P, "w", encoding="utf-8").write(r)
print("reload_self 已更新")
