# -*- coding: utf-8 -*-
"""第二阶段：把写入处改为 _ovr_load/_ovr_save（按模型）"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

OLD = '''                p_ov = os.path.join(APP_DIR, "group_overrides.json")
                try:
                    with open(p_ov, encoding="utf-8") as f:
                        ov = json.load(f) or {}
                except Exception:
                    ov = {}
                ov[str(bid)] = to
                with open(p_ov, "w", encoding="utf-8") as f:
                    json.dump(ov, f, ensure_ascii=False, indent=1)'''

NEW = '''                ov, _src = _ovr_load()
                ov[str(bid)] = to
                _ovr_save(ov)'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("body_move 写入已改为按模型")
else:
    print("未找到 body_move 写入段")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
