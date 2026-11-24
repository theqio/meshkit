# -*- coding: utf-8 -*-
"""新增代理命令 validate_controls：逐个检查 Mesh 里已有控制项是否缺参数/冲突
"""
import io
import shutil
import time

A = r"D:\ai\meshkit\app\bridge_agent.py"
shutil.copyfile(A, A + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(A, encoding="utf-8-sig").read()

FUNC = '''
def op_validate_controls(payload):
    """逐个校验 Mesh 里已有的尺寸/方法控制项
       返回每个控制项：类别、体数、尺寸、方法、状态、问题列表（fatal/warn）
    """
    out = {"ok": True, "controls": [], "fatal": [], "warn": [],
           "settings": {}, "errors": []}
    mesh = _mesh()

    def mm(obj, name, default=0.0):
        try:
            return _mm_value(_safe(obj, name, 0))
        except Exception:
            return default

    g_min = mm(mesh, "MinimumSize")
    g_def = mm(mesh, "DefeatureTolerance")
    g_size = mm(mesh, "ElementSize")
    out["settings"] = {"ElementSize": g_size, "MinimumSize": g_min,
                       "DefeatureTolerance": g_def}

    ok_states = (u"FullyDefined", u"Solved", u"UpToDate", u"NotSolved", u"")

    for c in list(mesh.Children or []):
        nm = _txt(_safe(c, "Name", u""))
        cat = _txt(_safe(c, "DataModelObjectCategory", u"")) or u""
        low = cat.lower()
        is_sizing = (u"sizing" in low) or nm.startswith(u"SIZE_")
        is_method = (u"method" in low) or nm.startswith(u"METHOD_")
        if not (is_sizing or is_method):
            continue
        item = {"name": nm, "category": cat,
                "kind": ("sizing" if is_sizing else "method"),
                "bodies": 0, "size_mm": None, "method": None,
                "state": u"", "problems": [], "fatal": [], "warn": []}
        try:
            ids = [int(x) for x in (_safe(_safe(c, "Location"), "Ids") or [])]
            item["bodies"] = len(ids)
        except Exception:
            ids = []
            item["bodies"] = 0
        if not ids:
            item["fatal"].append(u"作用域为空（控制项没有指定任何体）")

        if is_sizing:
            sz = mm(c, "ElementSize")
            item["size_mm"] = round(sz, 2) if sz > 0 else None
            if sz <= 0:
                item["fatal"].append(u"未设置尺寸（ElementSize 为空）")
            else:
                if g_min > 0 and sz < g_min:
                    item["warn"].append(
                        u"尺寸 %.2f 小于全局最小尺寸 %.2f（会被顶住）" % (sz, g_min))
                if g_def > 0 and sz < g_def:
                    item["warn"].append(
                        u"尺寸 %.2f 小于去特征尺寸 %.2f（细碎特征会被吃掉）" % (sz, g_def))
                if g_size > 0 and sz > g_size * 2.5:
                    item["warn"].append(
                        u"尺寸 %.2f 明显大于全局 %.2f（网格会很粗）" % (sz, g_size))
            try:
                beh = _txt(_safe(c, "Behavior", u""))
                if not beh:
                    item["warn"].append(u"Behavior 为空")
            except Exception:
                pass

        if is_method:
            mth = u""
            try:
                mth = _txt(_safe(c, "Method", u""))
            except Exception:
                mth = u""
            item["method"] = mth or None
            if not mth:
                item["fatal"].append(u"方法类型未设置")
            else:
                ml = mth.lower()
                if "sweep" in ml:
                    # 扫掠需要源/目标面或可扫掠拓扑
                    try:
                        has_src = bool(_safe(_safe(c, "SweepNumberDivisions"), None) is not None)
                    except Exception:
                        has_src = True
                    try:
                        nm_div = int(_mm_value(_safe(c, "SweepNumberDivisions", 0)) or 0)
                    except Exception:
                        nm_div = 0
                    if nm_div < 1:
                        item["warn"].append(u"扫掠分割数未设置")
                if "hex" in ml or "multizone" in ml:
                    pass
            try:
                _eo = _txt(_safe(c, "ElementOrder", u""))
            except Exception:
                _eo = u""

        st = u""
        for key in ("ObjectState", "State"):
            try:
                st = _txt(_safe(c, key, u""))
                if st:
                    break
            except Exception:
                continue
        item["state"] = st
        if st and st not in ok_states:
            item["fatal"].append(u"控制项状态异常：%s" % st)

        item["problems"] = item["fatal"] + item["warn"]
        if item["fatal"]:
            out["fatal"].append({"name": nm, "problems": item["fatal"]})
        elif item["warn"]:
            out["warn"].append({"name": nm, "problems": item["warn"]})
        out["controls"].append(item)

    # 网格对象本身的状态
    try:
        out["mesh_state"] = [_txt(_safe(mesh, "ObjectState", u"")),
                             _txt(_safe(mesh, "State", u""))]
    except Exception:
        out["mesh_state"] = []
    return out

'''

if "def op_validate_controls" not in s:
    s = s.replace("def op_pre_mesh_check(payload):",
                  FUNC.strip() + "\n\n\ndef op_pre_mesh_check(payload):", 1)
    print("已加 op_validate_controls")
else:
    print("已存在")

# 注册命令
OLD = '''    "pre_mesh_check": op_pre_mesh_check,'''
NEW = '''    "pre_mesh_check": op_pre_mesh_check,
    "validate_controls": op_validate_controls,'''
if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("已注册命令")
else:
    # 尝试另一种注册格式
    i = s.find('"pre_mesh_check"')
    print("注册格式需确认，附近内容:", s[max(0,i-80):i+120].replace("\n"," ")[:200])

# 版本号
s = s.replace('u"2026-12-02.1"', 'u"2026-12-02.2"', 1)
io.open(A, "w", encoding="utf-8-sig").write(s)
print("已写入")
