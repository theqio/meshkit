# -*- coding: utf-8 -*-
"""补上漏掉的路径：set_body_size / set_bodies_size 建好 SIZE_FIX 后，
   把该体从原类别尺寸/方法控制项里摘出来
"""
import io
import shutil
import time

A = r"D:\ai\meshkit\app\bridge_agent.py"
shutil.copyfile(A, A + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(A, encoding="utf-8-sig").read()

# 1) 已有 SIZE_FIX：改尺寸后也摘一次
OLD = '''    if ctrl is not None:
        try:
            ctrl.ElementSize = Quantity(u"%.3f [mm]" % size)
            return {"ok": True, "control": _txt(_safe(ctrl, "Name", u"")),
                    "size_mm": size, "created": False,
                    "value": _txt(_safe(ctrl, "ElementSize", u""))}
        except Exception:
            return {"ok": False, "error": _tail(traceback.format_exc(), 300)}'''

NEW = '''    if ctrl is not None:
        try:
            ctrl.ElementSize = Quantity(u"%.3f [mm]" % size)
            out = {"ok": True, "control": _txt(_safe(ctrl, "Name", u"")),
                   "size_mm": size, "created": False,
                   "value": _txt(_safe(ctrl, "ElementSize", u""))}
            # 关键：从类别控制项里摘除，否则类别尺寸仍会覆盖该体
            try:
                _rm, _er = _exclude_body_from_categories(bid, body_name)
                if _rm:
                    out["excluded_from"] = _rm
                if _er:
                    out["exclude_errors"] = _er
            except Exception as _e:
                out["exclude_errors"] = [_txt(_e)[:60]]
            return out
        except Exception:
            return {"ok": False, "error": _tail(traceback.format_exc(), 300)}'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("已补：已有 SIZE_FIX 分支")
else:
    print("未找到已有分支")

# 2) 新建 SIZE_FIX 后摘
OLD2 = '''    ctrl, err = _new_sizing_named(u"SIZE_FIX_" + body_name.replace(u"\\\\", u"_")[:24],
                                  [bid], size)
    if ctrl is None:
        return {"ok": False, "error": err}
    return {"ok": True, "control": _txt(_safe(ctrl, "Name", u"")),'''

NEW2 = '''    ctrl, err = _new_sizing_named(u"SIZE_FIX_" + body_name.replace(u"\\\\", u"_")[:24],
                                  [bid], size)
    if ctrl is None:
        return {"ok": False, "error": err}
    _out2 = {"excluded_from": []}
    try:
        _rm2, _er2 = _exclude_body_from_categories(bid, body_name)
        if _rm2:
            _out2["excluded_from"] = _rm2
        if _er2:
            _out2["exclude_errors"] = _er2
    except Exception as _e:
        _out2["exclude_errors"] = [_txt(_e)[:60]]
    return dict(_out2, **{"ok": True, "control": _txt(_safe(ctrl, "Name", u"")),'''

if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)
    print("已补：新建 SIZE_FIX 分支")

io.open(A, "w", encoding="utf-8-sig").write(s)
print("已写入")
