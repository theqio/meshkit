# -*- coding: utf-8 -*-
"""1) 薄板/圆孔/锁定/扫掠 标记改为「按模型」存储，取消后不会被自动判定覆盖
   2) 尺寸输入框只显示 Mesh 里控制项的真实值（配置值只作推荐/占位）
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

HELPER = '''
def _flags_path(key=None):
    return os.path.join(APP_DIR, "model_flags__%s.json" % (key or _model_key()))


def _flags_load():
    """按模型读标记：thin_cats/thin_off/hole_cats/locked_cats/sweep_ok"""
    try:
        p = _flags_path()
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                d = json.load(f) or {}
            return d, True
    except Exception:
        pass
    return {}, False


def _flags_save(d):
    try:
        with open(_flags_path(), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False


def _flags_get(cfg, key):
    """优先按模型；没有记录时回退全局配置"""
    d, ok = _flags_load()
    if ok and key in d:
        return list(d.get(key) or [])
    return list(cfg.get(key) or [])


def _flags_set(key, value):
    d, _ = _flags_load()
    d[key] = list(value or [])
    return _flags_save(d)

'''

if "_flags_path" not in s:
    s = s.replace("def _mech_busy():", HELPER.strip() + "\n\n\ndef _mech_busy():", 1)
    print("已加按模型标记存储")

# 类别读取改用 _flags_get
OLD = '''    thin = _only_current(cfg.get("thin_cats") or [])
    hole = _only_current(cfg.get("hole_cats") or [])
    lock = _only_current(cfg.get("locked_cats") or [])
    sw = _only_current(cfg.get("sweep_ok") or [])
    _thin_off = _only_current(cfg.get("thin_off") or [])'''
NEW = '''    thin = _only_current(_flags_get(cfg, "thin_cats"))
    hole = _only_current(_flags_get(cfg, "hole_cats"))
    lock = _only_current(_flags_get(cfg, "locked_cats"))
    sw = _only_current(_flags_get(cfg, "sweep_ok"))
    _thin_off = _only_current(_flags_get(cfg, "thin_off"))'''
if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("类别读取已用按模型标记")
else:
    print("未找到标记读取段")

# 取消薄板：尊重 thin_off（去掉 ratio 覆盖）
OLD2 = '''        _ratio = (float(_plate) / len(names)) if names else 0.0
        # 薄板体占多数（≥60%）时以几何为准，不被残留的 thin_off 抑制
        _auto = auto_thin and (g not in _thin_off or _ratio >= 0.6)'''
NEW2 = '''        _ratio = (float(_plate) / len(names)) if names else 0.0
        # 用户明确取消过（thin_off）就不再自动标薄板
        _auto = auto_thin and (g not in _thin_off)'''
if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)
    print("取消薄板已生效")

# 尺寸：只显示 Mesh 控制项真实值
OLD3 = '''        ci = ctrl_info.get(g) or {}
        size = ci.get("size") if ci.get("size") is not None else sz.get(g)'''
NEW3 = '''        ci = ctrl_info.get(g) or {}
        # 输入框只显示 Mesh 里的真实尺寸；配置值仅作为推荐/占位
        size = ci.get("size") if ci.get("size") is not None else None
        if size is None and ci:
            size = None'''
if OLD3 in s:
    s = s.replace(OLD3, NEW3, 1)
    print("尺寸已改为只取 Mesh 值")
else:
    print("未找到尺寸来源段")

# /api/thin 写入按模型标记
OLD4 = '''                cfg["thin_cats"] = cats
                cfg["methods"] = methods
                cfg["thin_divisions"] = divs
                save_config(cfg)'''
NEW4 = '''                cfg["thin_cats"] = cats
                cfg["methods"] = methods
                cfg["thin_divisions"] = divs
                save_config(cfg)
                # 同时写入按模型的标记（取消后不会被自动判定覆盖）
                try:
                    _flags_set("thin_cats", cats)
                    _flags_set("thin_off", off)
                except Exception:
                    pass'''
if OLD4 in s:
    s = s.replace(OLD4, NEW4, 1)
    print("/api/thin 已写按模型标记")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("server 语法 OK")
