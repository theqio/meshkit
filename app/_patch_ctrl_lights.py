# -*- coding: utf-8 -*-
"""类别灯色改为依据「Mesh 里已有控制项的真实校验结果」
   - 调代理 validate_controls（缓存 30s）
   - 每个类别：有 fatal → 红；只有 warn → 黄；无问题 → 绿；没有控制项 → 红
   - 红灯/黄灯的原因直接写控制项的具体问题
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

HELPER = '''
def _ctrl_validation():
    """调用代理校验已有控制项（缓存 30 秒）"""
    tp = target_pid()
    return cache_get(("ctrlval", tp), 30,
                     lambda: call_bridge("validate_controls", timeout=120))


def _strip_ctrl_prefix(name):
    for pre in ("SIZE_FIX_", "SIZE_", "METHOD_", "SWEEP_", "SHELL_"):
        if name.startswith(pre):
            return name[len(pre):]
    return name


def _lights_from_controls(cats):
    """用控制项校验结果覆盖类别灯色；返回是否成功"""
    try:
        val = _ctrl_validation()
    except Exception:
        return False
    if not isinstance(val, dict) or not val.get("ok"):
        return False
    fatal, warn = {}, {}
    for item in (val.get("controls") or []):
        cat = _strip_ctrl_prefix(item.get("name") or "")
        if not cat:
            continue
        if item.get("fatal"):
            fatal.setdefault(cat, []).extend(item["fatal"])
        elif item.get("warn"):
            warn.setdefault(cat, []).extend(item["warn"])
    have = set()
    for item in (val.get("controls") or []):
        have.add(_strip_ctrl_prefix(item.get("name") or ""))

    for c in (cats or []):
        name = c.get("name")
        if c.get("is_all_suppressed"):
            c["light"], c["light_reason"] = "muted", "全部已抑制（不参与网格）"
            continue
        if name in fatal:
            c["light"] = "red"
            c["light_reason"] = "控制项问题：" + "；".join(fatal[name][:3])
        elif name in warn:
            c["light"] = "yellow"
            c["light_reason"] = "控制项告警：" + "；".join(warn[name][:3])
        elif name in have:
            c["light"] = "green"
            c["light_reason"] = "控制项正常"
        else:
            c["light"] = "red"
            c["light_reason"] = "Mesh 里没有该类别控制项（点「应用设置与尺寸」生成）"
    return True

'''

if "_lights_from_controls" not in s:
    s = s.replace("def _mech_busy():", HELPER.strip() + "\n\n\ndef _mech_busy():", 1)
    print("已加控制项灯色逻辑")

# 在返回前应用
OLD = '''    cats.sort(key=lambda c: -c["bodies"])
    return {"ok": True, "categories": cats,'''
NEW = '''    cats.sort(key=lambda c: -c["bodies"])
    # 灯色以「Mesh 里已有控制项的真实校验」为准
    try:
        _ok_lights = _lights_from_controls(cats)
    except Exception:
        _ok_lights = False
    if not _ok_lights:
        for _c in cats:
            if _c.get("light") != "muted":
                _c["light_reason"] = "控制项状态读取中…"
    return {"ok": True, "categories": cats,'''
if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("灯色已改为控制项校验")
else:
    print("未找到返回段")

# 新接口：直接看控制项清单与问题
ROUTE = '''            if path == "/api/controls_validate":
                return self._json(_ctrl_validation())

'''
if "/api/controls_validate" not in s:
    anchor = '            if path == "/api/premesh":'
    pos = s.find(anchor, s.find("def do_GET"))
    if pos > 0:
        s = s[:pos] + ROUTE + s[pos:]
        print("已加 /api/controls_validate")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("server 语法 OK")
