# -*- coding: utf-8 -*-
"""新增代理命令 dedupe_controls：删除重复的尺寸/方法控制项（同名同类型只保留一个）
   并在 apply 末尾自动去重，避免"点两次应用出现两套控制项"
"""
import io
import shutil
import time

A = r"D:\ai\meshkit\app\bridge_agent.py"
shutil.copyfile(A, A + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(A, encoding="utf-8-sig").read()

FUNC = '''
def op_dedupe_controls(payload):
    """删除重复控制项：同一 (名字, 类型) 只保留一个
       保留策略：作用域体数多的优先；相同时保留先出现的
       返回 {ok, removed:[...], kept:[...]}
    """
    try:
        _touch_ctrl()
    except Exception:
        pass
    mesh = _mesh()
    groups = {}
    removed, kept, errors = [], [], []
    for c in list(mesh.Children or []):
        nm = _txt(_safe(c, "Name", u""))
        cat = _txt(_safe(c, "DataModelObjectCategory", u"")) or u""
        low = cat.lower()
        is_sizing = (u"sizing" in low) or nm.startswith(u"SIZE_")
        is_method = (u"method" in low) or nm.startswith(u"METHOD_")
        if not (is_sizing or is_method):
            continue
        kind = "sizing" if is_sizing else "method"
        try:
            ids = [int(x) for x in (_safe(_safe(c, "Location"), "Ids") or [])]
        except Exception:
            ids = []
        groups.setdefault((nm, kind), []).append((len(ids), c))
    for (nm, kind), items in groups.items():
        if len(items) <= 1:
            continue
        items.sort(key=lambda x: -x[0])
        for cnt, c in items[1:]:
            try:
                c.Delete()
                removed.append(nm)
            except Exception as e:
                errors.append(u"%s: %s" % (nm, _txt(e)[:50]))
        kept.append(nm)
    return {"ok": True, "removed": removed, "kept": kept,
            "removed_count": len(removed), "errors": errors,
            "groups": len(groups)}

'''

if "def op_dedupe_controls" not in s:
    s = s.replace("def op_fix_body(payload):",
                  FUNC.strip() + "\n\n\ndef op_fix_body(payload):", 1)
    print("已加 dedupe_controls")

OLD = '''    "pre_mesh_check": op_pre_mesh_check,'''
NEW = '''    "pre_mesh_check": op_pre_mesh_check,
    "dedupe_controls": op_dedupe_controls,'''
if OLD in s and '"dedupe_controls"' not in s:
    s = s.replace(OLD, NEW, 1)
    print("已注册 dedupe_controls")

# apply 末尾自动去重
OLD2 = '''    applied["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return applied'''
NEW2 = '''    # 自动去重：避免"点了两次应用"产生两套控制项
    try:
        _dd = op_dedupe_controls({})
        if _dd.get("removed_count"):
            applied["deduped"] = _dd.get("removed")
    except Exception:
        pass
    applied["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return applied'''
if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)
    print("apply 末尾已加自动去重")
else:
    print("未找到 apply 返回段")

s = s.replace('u"2026-12-02.4"', 'u"2026-12-02.5"', 1)
io.open(A, "w", encoding="utf-8-sig").write(s)
compile(io.open(A, encoding="utf-8-sig").read(), A, "exec")
print("已写入，版本 2026-12-02.5，语法 OK")
