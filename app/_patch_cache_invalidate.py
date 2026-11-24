# -*- coding: utf-8 -*-
"""写操作后清缓存 + 页面遇到 loading 自动重试"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

# op 成功后清缓存
OLD = '''                if _ran_on and isinstance(res, dict):
                    res["ran_on"] = _ran_on
                return self._json(res)'''
NEW = '''                if _ran_on and isinstance(res, dict):
                    res["ran_on"] = _ran_on
                try:
                    if op in MUTATING_OPS or op in ("mesh", "apply", "fix_failed"):
                        cache_invalidate()
                except Exception:
                    pass
                return self._json(res)'''
if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("op 后已清缓存")

# scan_apply 后清缓存
OLD2 = '''                # 4) 自动保存（防崩溃丢结果）
                out["saved"] = safe_save("scan_apply")'''
NEW2 = '''                # 4) 自动保存（防崩溃丢结果）
                try:
                    cache_invalidate()
                except Exception:
                    pass
                out["saved"] = safe_save("scan_apply")'''
if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)
    print("scan_apply 后已清缓存")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("server 语法 OK")

# ---- 页面：loading 自动重试 ----
P = r"D:\ai\meshkit\app\static\index.html"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

OLD3 = '''    const r = await api('/api/categories');
    if(!r.ok){
      $('catHint').innerHTML = '<span style="color:#ffb4b4">' + (r.error||'读取失败') + '</span>';'''
NEW3 = '''    const r = await api('/api/categories');
    if(r && r.loading){
      $('catHint').innerHTML = '<span style="color:#ffd48a">正在读取类别（Mechanical 较慢）…</span>';
      _catBusy = false;
      setTimeout(loadCategories, 2500);
      return;
    }
    if(!r.ok){
      $('catHint').innerHTML = '<span style="color:#ffb4b4">' + (r.error||'读取失败') + '</span>';'''
if OLD3 in src:
    src = src.replace(OLD3, NEW3, 1)
    print("页面已支持 loading 重试")
else:
    print("未找到页面类别读取段")

io.open(P, "w", encoding="utf-8").write(src)
print("完成")
