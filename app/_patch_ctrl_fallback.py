# -*- coding: utf-8 -*-
"""validate_controls 不存在时（代理没重载）用 read_plan 做等价校验兜底"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

OLD = '''def _ctrl_validation():
    """调用代理校验已有控制项（缓存 30 秒）"""
    tp = target_pid()
    return cache_get(("ctrlval", tp), 30,
                     lambda: call_bridge("validate_controls", timeout=120))'''

NEW = '''def _ctrl_validation_fallback():
    """用 read_plan + 全局设置做校验（代理未重载时的兜底）"""
    try:
        rp = call_bridge("read_plan", timeout=120)
    except Exception:
        return {"ok": False, "error": "read_plan 失败"}
    if not rp.get("ok"):
        return rp
    cfg = load_config()
    st = cfg.get("settings") or {}
    g_min = float(st.get("MinimumSize") or 0)
    g_def = float(st.get("DefeatureTolerance") or 0)
    g_size = float(st.get("ElementSize") or 0)
    controls, fatal, warn = [], [], []
    for c in (rp.get("controls") or []):
        nm = (c.get("name") or "").strip()
        if not nm:
            continue
        kind = "sizing" if ("Sizing" in (c.get("category") or "")
                            or nm.startswith("SIZE_")) else "method"
        item = {"name": nm, "kind": kind, "bodies": c.get("count") or 0,
                "size_mm": c.get("size_mm"), "method": c.get("method"),
                "fatal": [], "warn": []}
        if not item["bodies"]:
            item["fatal"].append("作用域为空（控制项没有指定任何体）")
        if kind == "sizing":
            sz = item["size_mm"] or 0
            if not sz:
                item["fatal"].append("未设置尺寸（ElementSize 为空）")
            else:
                if g_min and sz < g_min:
                    item["warn"].append("尺寸 %.2f 小于全局最小尺寸 %.2f" % (sz, g_min))
                if g_def and sz < g_def:
                    item["warn"].append("尺寸 %.2f 小于去特征尺寸 %.2f" % (sz, g_def))
                if g_size and sz > g_size * 2.5:
                    item["warn"].append("尺寸 %.2f 明显大于全局 %.2f" % (sz, g_size))
        else:
            if not item["method"]:
                item["fatal"].append("方法类型未设置")
        item["problems"] = item["fatal"] + item["warn"]
        if item["fatal"]:
            fatal.append({"name": nm, "problems": item["fatal"]})
        elif item["warn"]:
            warn.append({"name": nm, "problems": item["warn"]})
        controls.append(item)
    return {"ok": True, "controls": controls, "fatal": fatal, "warn": warn,
            "settings": {"ElementSize": g_size, "MinimumSize": g_min,
                         "DefeatureTolerance": g_def},
            "source": "fallback"}


def _ctrl_validation():
    """调用代理校验已有控制项（缓存 30 秒）；代理不支持时用 read_plan 兜底"""
    tp = target_pid()
    cached = cache_get(("ctrlval", tp), 30,
                       lambda: call_bridge("validate_controls", timeout=120),
                       block=False)
    if isinstance(cached, dict) and cached.get("ok"):
        return cached
    # 代理还没有 validate_controls（未重载）→ 兜底
    try:
        return cache_get(("ctrlval_fb", tp), 30, _ctrl_validation_fallback)
    except Exception:
        return {"ok": False, "error": "控制项校验不可用"}'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("已加兜底校验")
else:
    print("未找到 _ctrl_validation")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
