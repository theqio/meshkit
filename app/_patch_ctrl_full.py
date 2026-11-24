# -*- coding: utf-8 -*-
"""修两点：
   1) 快速模式也要带尺寸/方法（缓存 read_plan 的完整信息，而不是只有名字）
   2) 切换薄板/锁定后清缓存，页面立刻看到变化
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

# 1) 缓存内容改为完整信息
OLD = '''            try:
                rp = call_bridge("read_plan", timeout=120)
                names = []
                for c in (rp.get("controls") or []):
                    nm = (c.get("name") or "").strip()
                    if not nm:
                        continue
                    for pre in ("SIZE_FIX_", "SIZE_", "METHOD_", "SWEEP_", "SHELL_"):
                        if nm.startswith(pre):
                            nm = nm[len(pre):]
                            break
                    names.append(nm)
                if names:
                    _CTRL_CACHE[pid] = (time.time(), names)'''
NEW = '''            try:
                rp = call_bridge("read_plan", timeout=120)
                items = []
                for c in (rp.get("controls") or []):
                    nm = (c.get("name") or "").strip()
                    if not nm:
                        continue
                    for pre in ("SIZE_FIX_", "SIZE_", "METHOD_", "SWEEP_", "SHELL_"):
                        if nm.startswith(pre):
                            nm = nm[len(pre):]
                            break
                    items.append({"name": nm,
                                  "size": c.get("size_mm"),
                                  "method": c.get("method"),
                                  "bodies": c.get("count")})
                if items:
                    _CTRL_CACHE[pid] = (time.time(), items)'''
if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("缓存已改为完整控制项信息")
else:
    print("未找到缓存写入段")

# 同步读取分支同样
OLD2 = '''            rp = call_bridge("read_plan", timeout=120)
            names = []
            for c in (rp.get("controls") or []):
                nm = (c.get("name") or "").strip()
                if not nm:
                    continue
                for pre in ("SIZE_FIX_", "SIZE_", "METHOD_", "SWEEP_", "SHELL_"):
                    if nm.startswith(pre):
                        nm = nm[len(pre):]
                        break
                names.append(nm)
            if names:
                _CTRL_CACHE[pid] = (time.time(), names)
                _CTRL_BUSY[pid] = False
                return set(names), 0.0'''
NEW2 = '''            rp = call_bridge("read_plan", timeout=120)
            items = []
            for c in (rp.get("controls") or []):
                nm = (c.get("name") or "").strip()
                if not nm:
                    continue
                for pre in ("SIZE_FIX_", "SIZE_", "METHOD_", "SWEEP_", "SHELL_"):
                    if nm.startswith(pre):
                        nm = nm[len(pre):]
                        break
                items.append({"name": nm,
                              "size": c.get("size_mm"),
                              "method": c.get("method"),
                              "bodies": c.get("count")})
            if items:
                _CTRL_CACHE[pid] = (time.time(), items)
                _CTRL_BUSY[pid] = False
                return items, 0.0'''
if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)
    print("同步读取已改为完整信息")

# 命中缓存时返回 list（原来是 set）
OLD3 = '''    if hit and now - hit[0] < ttl:
        return set(hit[1]), round(now - hit[0], 1)'''
NEW3 = '''    if hit and now - hit[0] < ttl:
        return list(hit[1]), round(now - hit[0], 1)'''
if OLD3 in s:
    s = s.replace(OLD3, NEW3, 1)
    print("缓存命中返回完整列表")

OLD4 = '''    if hit:
        if not _CTRL_BUSY.get(pid):
            _spawn()
        return set(hit[1]), round(now - hit[0], 1)'''
NEW4 = '''    if hit:
        if not _CTRL_BUSY.get(pid):
            _spawn()
        return list(hit[1]), round(now - hit[0], 1)'''
if OLD4 in s:
    s = s.replace(OLD4, NEW4, 1)

# 2) 快速分支：用完整信息构建 ctrl_info
OLD5 = '''        if fast:
            _cn, _cage = ctrl_names_cached(wait=True)
            if _cn is None:
                ctrl_known = False          # 还在后台读，先不判灯
                _cn = set()
            for _n in _cn:
                if _n and _n != "未覆盖":
                    ctrl_info.setdefault(_n, {})'''
NEW5 = '''        if fast:
            _cn, _cage = ctrl_names_cached(wait=True)
            if _cn is None:
                ctrl_known = False          # 还在后台读，先不判灯
                _cn = []
            for _it in _cn:
                if isinstance(_it, dict):
                    _n = _it.get("name")
                    if _n and _n != "未覆盖":
                        ctrl_info.setdefault(_n, {"size": _it.get("size"),
                                                  "method": _it.get("method")})
                elif _it and _it != "未覆盖":
                    ctrl_info.setdefault(_it, {})'''
if OLD5 in s:
    s = s.replace(OLD5, NEW5, 1)
    print("快速分支已用完整信息")
else:
    print("未找到快速分支")

# 3) thin / lock 之后清缓存
OLD6 = '''                try:
                    _flags_set("thin_cats", cats)
                    _flags_set("thin_off", off)
                except Exception:
                    pass'''
NEW6 = '''                try:
                    _flags_set("thin_cats", cats)
                    _flags_set("thin_off", off)
                except Exception:
                    pass
                try:
                    cache_invalidate()
                except Exception:
                    pass'''
if OLD6 in s:
    s = s.replace(OLD6, NEW6, 1)
    print("thin 已清缓存")

OLD7 = '''                cfg["locked_cats"] = cats
                save_config(cfg)
                return self._json({"ok": True, "locked_cats": cats})'''
NEW7 = '''                cfg["locked_cats"] = cats
                save_config(cfg)
                try:
                    _flags_set("locked_cats", cats)
                    cache_invalidate()
                except Exception:
                    pass
                return self._json({"ok": True, "locked_cats": cats})'''
if OLD7 in s:
    s = s.replace(OLD7, NEW7, 1)
    print("lock 已写标记并清缓存")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
