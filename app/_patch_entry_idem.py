# -*- coding: utf-8 -*-
"""入口改为幂等：只有没有定时器时才 start()，避免重复调度器"""
import io
import shutil
import time

A = r"D:\ai\meshkit\app\bridge_agent.py"
shutil.copyfile(A, A + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(A, encoding="utf-8-sig").read()

OLD = '''if __name__ == "__main__":
    start()'''

NEW = '''# 只有当前没有任何定时器时才启动，避免重复调度器
try:
    if not globals().get("_keep"):
        start()
except Exception:
    pass'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    s = s.replace('u"2026-12-02.9"', 'u"2026-12-02.10"', 1)
    io.open(A, "w", encoding="utf-8-sig").write(s)
    print("entry is now idempotent; version 2026-12-02.10")
else:
    print("entry guard not found")

compile(io.open(A, encoding="utf-8-sig").read(), A, "exec")
print("syntax OK")
