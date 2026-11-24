# -*- coding: utf-8 -*-
"""薄板标签与实际方法对齐：
   1) 配置里其他模型的残留类别名（thin_cats/thin_off/sweep_ok/...）先按当前分组过滤掉
   2) 自动判定为薄板时，不再被"别的模型的 thin_off"抑制
   3) 方法来自"手动扫掠标记"但本体不是薄板时，给出说明（method_note）
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

# 1) 读取配置后按当前分组过滤
OLD = '''    thin = cfg.get("thin_cats") or []
    hole = cfg.get("hole_cats") or []
    lock = cfg.get("locked_cats") or []
    sw = cfg.get("sweep_ok") or []
    setts = cfg.get("settings") or {}
    minsize = setts.get("MinimumSize") or 10'''

NEW = '''    _cur_names = set(groups.keys())
    def _only_current(lst):
        return [x for x in (lst or []) if x in _cur_names]

    thin = _only_current(cfg.get("thin_cats") or [])
    hole = _only_current(cfg.get("hole_cats") or [])
    lock = _only_current(cfg.get("locked_cats") or [])
    sw = _only_current(cfg.get("sweep_ok") or [])
    _thin_off = _only_current(cfg.get("thin_off") or [])
    setts = cfg.get("settings") or {}
    minsize = setts.get("MinimumSize") or 10'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("配置列表已按当前模型过滤")
else:
    print("未找到配置读取段")

# 2) 薄板判定：薄板体占多数时，忽略 thin_off
OLD2 = '''        _off = cfg.get("thin_off") or []
        _auto = auto_thin and (g not in _off)'''

NEW2 = '''        _ratio = (float(_plate) / len(names)) if names else 0.0
        # 薄板体占多数（≥60%）时以几何为准，不被残留的 thin_off 抑制
        _auto = auto_thin and (g not in _thin_off or _ratio >= 0.6)'''

if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)
    print("薄板判定已忽略残留 thin_off")
else:
    print("未找到薄板判定段")

# 3) 方法说明：手动扫掠标记且非薄板
OLD3 = '''            "is_thin_cat": (g in thin) or _auto,
            "auto_thin": _auto,
            "plate_bodies": _plate,'''

NEW3 = '''            "is_thin_cat": (g in thin) or _auto,
            "auto_thin": _auto,
            "method_note": ("扫掠（手动标记扫掠可用）"
                            if (method == "Sweep" and not ((g in thin) or _auto))
                            else ""),
            "plate_bodies": _plate,'''

if OLD3 in s:
    s = s.replace(OLD3, NEW3, 1)
    print("已加方法说明")
else:
    print("未找到 is_thin_cat 段")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
