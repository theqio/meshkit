# -*- coding: utf-8 -*-
"""1) 校验时忽略"没有可用体"的类别；2) 写操作返回 ran_on（在哪个模型上执行的）"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

OLD = '''                    _bad = _verify_groups({g: ns for g, ns in groups.items() if ns})'''
NEW = '''                    _failed_groups = set()
                    for _m in (gg.get("failed") or []):
                        try:
                            _failed_groups.add(str(_m).split(":")[0].strip())
                        except Exception:
                            pass
                    _want = {g: ns for g, ns in groups.items()
                             if ns and g not in _failed_groups}
                    _bad = _verify_groups(_want)'''
if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("校验已跳过无法解析的组")
else:
    print("未找到校验行")

OLD2 = '''                if op in MUTATING_OPS:
                    try:
                        _guard = ensure_target_front(strict=True)
                    except Exception:
                        _guard = {"ok": True}
                    if not _guard.get("ok"):
                        return self._json({
                            "ok": False, "blocked": True,
                            "error": _guard.get("error") or "目标实例不在前台",
                            "front_pid": _guard.get("front_after"),
                            "target_pid": target_pid()})'''
NEW2 = '''                _ran_on = None
                if op in MUTATING_OPS:
                    try:
                        _guard = ensure_target_front(strict=True)
                    except Exception:
                        _guard = {"ok": True}
                    if not _guard.get("ok"):
                        return self._json({
                            "ok": False, "blocked": True,
                            "error": _guard.get("error") or "目标实例不在前台",
                            "front_pid": _guard.get("front_after"),
                            "target_pid": target_pid()})
                    try:
                        _tp = target_pid()
                        _pn, _pp = _project_of_pid(_tp)
                        _ran_on = {"pid": _tp, "project": _pn}
                    except Exception:
                        _ran_on = {"pid": target_pid()}'''
if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)
    print("/api/op 已加 ran_on")

# op 执行完把 ran_on 塞进结果
OLD3 = '''                    res = _exec_op(op, payload)'''
if OLD3 in s:
    OLD4 = '''                    if isinstance(res, dict):
                        res.setdefault("op", op)'''
    NEW4 = '''                    if isinstance(res, dict):
                        res.setdefault("op", op)
                        if _ran_on:
                            res["ran_on"] = _ran_on'''
    if OLD4 in s:
        s = s.replace(OLD4, NEW4, 1)
        print("op 结果已带 ran_on")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
