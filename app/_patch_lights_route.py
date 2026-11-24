# -*- coding: utf-8 -*-
"""灯色在返回前实时套用（不受类别缓存影响）"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

OLD = '''                if _rec.get("loading"):
                    return self._json(_rec)'''
NEW = '''                if _rec.get("loading"):
                    return self._json(_rec)
                # 灯色每次按「控制项校验」实时套用（校验结果自身有 30 秒缓存）
                try:
                    if _rec.get("categories"):
                        _lights_from_controls(_rec["categories"])
                except Exception:
                    pass'''
if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("类别接口已实时套用灯色")
else:
    print("未找到 categories 缓存分支")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
