# -*- coding: utf-8 -*-
"""修 /api/thin：改为操作「按模型」的 thin_cats / thin_off，并同步 methods"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

i = s.find('if path == "/api/thin":')
j = s.find("\n            if path ==", i + 10)
if i < 0 or j < 0:
    raise SystemExit("找不到 /api/thin 段")
old = s[i:j]
print("原代码长度:", len(old))

NEW = '''if path == "/api/thin":
                body = self._body()
                cat = (body.get("cat") or "").strip()
                on = bool(body.get("thin"))
                cfg = load_config()
                d, _ok = _flags_load()
                cats = list(d.get("thin_cats") or
                            _flags_get(cfg, "thin_cats"))
                off = list(d.get("thin_off") or
                           _flags_get(cfg, "thin_off"))
                methods = cfg.setdefault("methods", {})
                divs = cfg.setdefault("thin_divisions", {})
                if on:
                    if cat and cat not in cats:
                        cats.append(cat)
                    off = [x for x in off if x != cat]
                    methods[cat] = "Sweep"
                    divs[cat] = 2
                else:
                    cats = [x for x in cats if x != cat]
                    if cat and cat not in off:
                        off.append(cat)
                    methods[cat] = "Automatic"
                    divs.pop(cat, None)
                cfg["methods"] = methods
                cfg["thin_divisions"] = divs
                save_config(cfg)
                d["thin_cats"] = cats
                d["thin_off"] = off
                _flags_save(d)
                try:
                    cache_invalidate()
                except Exception:
                    pass
                return self._json({"ok": True, "thin_cats": cats,
                                   "thin_off": off,
                                   "methods": methods, "divisions": divs})

'''
s = s[:i] + NEW + s[j+1:]
io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("已替换 /api/thin，语法 OK")
