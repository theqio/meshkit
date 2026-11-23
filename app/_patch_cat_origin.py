# -*- coding: utf-8 -*-
"""给类别/参数接口标注"数据来自哪个实例"，页面据此核对，避免显示成另一个模型"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

OLD = '''                # ⓿ 优先用几何识别结果（与扫描分组一致）
                _rec = categories_from_recognition()
                if _rec.get("ok"):'''

NEW = '''                # ⓿ 优先用几何识别结果（与扫描分组一致）
                _rec = categories_from_recognition()
                if _rec.get("ok"):
                    try:
                        _tp0 = target_pid()
                        _pn0, _pp0 = _project_of_pid(_tp0)
                        _rec["pid"] = _tp0
                        _rec["project"] = _pn0
                    except Exception:
                        pass'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("categories 已标注来源实例")
else:
    print("未找到 categories 分支")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
