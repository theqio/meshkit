# -*- coding: utf-8 -*-
"""在类别输出里带上薄板判定依据（plate_bodies / thin_ratio），便于定位"没标上"的原因"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

OLD = '''            "is_thin_cat": (g in thin) or _auto,
            "auto_thin": _auto,'''

NEW = '''            "is_thin_cat": (g in thin) or _auto,
            "auto_thin": _auto,
            "plate_bodies": _plate,
            "thin_ratio": (round(float(_plate) / len(names), 3) if names else 0),'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    io.open(S, "w", encoding="utf-8", newline="\n").write(s)
    print("已输出 plate_bodies/thin_ratio")
else:
    print("未找到插入点")

compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
