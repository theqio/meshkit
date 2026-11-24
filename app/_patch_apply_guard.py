# -*- coding: utf-8 -*-
"""修 apply 的一个危险分支：
   之前 read_plan 拿不到数据（返回 busy）时，_existing 为空 →
   程序误以为"所有类别都没控制项"，于是新建控制项，可能建出空作用域的方法控制项。
   现在：拿不到真实控制项清单就直接中止，不做任何修改。
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

OLD = '''                            _existing = set()
                            try:
                                _rp = call_bridge("read_plan", timeout=120)
                                for _c in (_rp.get("controls") or []):'''

NEW = '''                            _existing = set()
                            try:
                                _rp = call_bridge("read_plan", timeout=120)
                                if not _rp.get("ok"):
                                    return self._json({
                                        "ok": False, "busy": bool(_rp.get("busy")),
                                        "error": "读不到现有控制项清单，已中止（避免建出重复/空控制项）：%s"
                                                 % (_rp.get("error") or "")})
                                for _c in (_rp.get("controls") or []):'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("apply 已加保护：读不到控制项就中止")
else:
    print("未找到 apply _existing 段")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
