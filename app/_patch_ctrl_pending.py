# -*- coding: utf-8 -*-
"""修「全灰」：控制项清单没读到时不缓存、页面自动重试
   1) 结果里带 _ctrl_known / ctrl_pending
   2) 未读到控制项的结果不写缓存（避免把"灰"缓存 60 秒）
   3) 页面遇到 ctrl_pending 3 秒后自动重取
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

# 1) 结果里带标记
OLD = '''    cats.sort(key=lambda c: -c["bodies"])
    return {"ok": True, "categories": cats,'''
NEW = '''    cats.sort(key=lambda c: -c["bodies"])
    return {"ok": True, "categories": cats,
            "_ctrl_known": bool(ctrl_known),
            "ctrl_pending": (not ctrl_known),'''
if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("已输出 ctrl_pending")
else:
    print("未找到返回段")

# 2) 未读到控制项的结果不写缓存
OLD2 = '''    val = producer()
    if isinstance(val, dict) and val.get("ok"):
        _CACHE[key] = (time.time(), val)
    return val'''
NEW2 = '''    val = producer()
    if isinstance(val, dict) and val.get("ok"):
        # 控制项还没读到 -> 不缓存，否则"全灰"会停留 60 秒
        if not (isinstance(val, dict) and val.get("_ctrl_known") is False):
            _CACHE[key] = (time.time(), val)
    return val'''
if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)
    print("未就绪结果不再缓存")

# 后台刷新线程同理
OLD3 = '''            def _bg():
                try:
                    val = producer()
                    if isinstance(val, dict) and val.get("ok"):
                        _CACHE[key] = (time.time(), val)'''
NEW3 = '''            def _bg():
                try:
                    val = producer()
                    if isinstance(val, dict) and val.get("ok"):
                        if not (isinstance(val, dict) and val.get("_ctrl_known") is False):
                            _CACHE[key] = (time.time(), val)'''
if OLD3 in s:
    s = s.replace(OLD3, NEW3, 1)
    print("后台刷新也不缓存未就绪结果")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("server 语法 OK")

# 3) 页面重试
P = r"D:\ai\meshkit\app\static\index.html"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

OLD4 = '''    LAST_CATS = r.categories || [];
    LAST_SUG = r.suggestion || null;
    renderCategories(LAST_CATS, r.methods_available);'''
NEW4 = '''    LAST_CATS = r.categories || [];
    LAST_SUG = r.suggestion || null;
    renderCategories(LAST_CATS, r.methods_available);
    if(r.ctrl_pending){
      // 控制项清单还没读到：3 秒后自动重取，灯色会自动更新
      _catBusy = false;
      setTimeout(loadCategories, 3000);
    }'''
if OLD4 in src:
    src = src.replace(OLD4, NEW4, 1)
    io.open(P, "w", encoding="utf-8").write(src)
    print("页面已加 ctrl_pending 重试")
else:
    print("未找到页面渲染段")
