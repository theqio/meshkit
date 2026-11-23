# -*- coding: utf-8 -*-
"""修复人工覆盖表跨模型误用：
   1) 覆盖表键只能解析为「当前模型的体 Id」或「当前模型的体名」
   2) 解析不到的键直接跳过（不再把 Id 当体名，虚构出垫板那种组）
   3) 记录被跳过的数量，便于清理
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

OLD = '''        for key, target in ov.items():
            n = id2name.get(str(key)) or key
            if not n:
                continue'''

NEW = '''        _names_set = set(id2name.values())
        _stale = 0
        for key, target in ov.items():
            n = id2name.get(str(key))
            if not n:
                # 兼容旧数据：键本身就是当前模型的体名才认
                if str(key) in _names_set:
                    n = str(key)
                else:
                    _stale += 1
                    continue
            if not n:
                continue'''
if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("覆盖表已加解析校验")
else:
    print("未找到覆盖表循环")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
