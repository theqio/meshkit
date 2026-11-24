# -*- coding: utf-8 -*-
"""薄板类别写入扫掠层数：
   原来 _apply_sweep_divisions 有个"是否板状"的几何门槛，
   垫板这种 114 体的类别过不了门槛 → 层数没写进去。
   现在：只要调用方明确给了层数（薄板类别），就强制写入并回读确认。
"""
import io
import shutil
import time

A = r"D:\ai\meshkit\app\bridge_agent.py"
shutil.copyfile(A, A + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(A, encoding="utf-8-sig").read()

OLD = '''def _apply_sweep_divisions(ctrl, method, divisions, ids=None):
    """扫掠层数（穿过厚度方向分段数）；任何异常都不外抛"""
    try:
        if method not in (u"Sweep", u"MultiZone") or not divisions:
            return False
        if ids is not None and not _is_plate_like(ids):
            return False
        try:
            n = int(divisions)
        except Exception:
            return False
        for prop in (u"SweepNumDivisions", u"SweepNumberDivisions"):
            try:
                setattr(ctrl, prop, n)
                return True
            except Exception:
                pass
        for pname in (u"SweepNumDivisions", u"Divisions"):
            try:
                obj = ctrl.PropertyByAPIName(pname)
                if obj is not None:
                    obj.InternalValue = n
                    return True
            except Exception:
                pass
        return False
    except Exception:
        return False'''

NEW = '''def _apply_sweep_divisions(ctrl, method, divisions, ids=None, force=False):
    """扫掠层数（穿过厚度方向分段数）；任何异常都不外抛
       force=True：调用方已明确这是薄板类别，跳过"板状"几何门槛
    """
    try:
        if method not in (u"Sweep", u"MultiZone") or not divisions:
            return False
        if (not force) and ids is not None and not _is_plate_like(ids):
            return False
        try:
            n = int(divisions)
        except Exception:
            return False
        for prop in (u"SweepNumDivisions", u"SweepNumberDivisions"):
            try:
                setattr(ctrl, prop, n)
                # 回读确认，避免"设了但没生效"
                try:
                    cur = _mm_value(_safe(ctrl, prop, 0))
                    if int(cur) == n:
                        return True
                except Exception:
                    return True
            except Exception:
                pass
        for pname in (u"SweepNumDivisions", u"SweepNumberDivisions", u"Divisions"):
            try:
                obj = ctrl.PropertyByAPIName(pname)
                if obj is not None:
                    obj.InternalValue = n
                    return True
            except Exception:
                pass
        return False
    except Exception:
        return False'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("已改 _apply_sweep_divisions（支持强制写入）")
else:
    print("未找到 _apply_sweep_divisions")

# _set_method_for 里把 divisions 当明确意图 → force
OLD2 = '''        if divisions:
            _apply_sweep_divisions(ctrl, method, divisions, ids)'''
NEW2 = '''        if divisions:
            # 明确给了层数（薄板类别）→ 强制写入
            _apply_sweep_divisions(ctrl, method, divisions, ids, force=True)'''
if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)
    print("_set_method_for 已改为强制写入层数")
else:
    print("未找到 _set_method_for 调用段")

s = s.replace('u"2026-12-02.5"', 'u"2026-12-02.6"', 1)
io.open(A, "w", encoding="utf-8-sig").write(s)
compile(io.open(A, encoding="utf-8-sig").read(), A, "exec")
print("版本 2026-12-02.6，语法 OK")
