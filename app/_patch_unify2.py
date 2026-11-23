# -*- coding: utf-8 -*-
"""② 复用 ① 的分组来源 + 剔除成员不存在的组"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

OLD = '''                sys.path.insert(0, APP_DIR)
                import geo_recognize
                rec = geo_recognize.recognize(lb.get("bodies") or [], kb_extra_rules())
                groups = merge_custom_groups(rec.get("groups") or {})
                if not groups:
                    return self._json({"ok": False, "error": "识别结果为空"})
                out = {"ok": True, "renamed": 0, "geom_groups": 0, "errors": [],
                       "group_count": len(groups), "total": rec.get("total") or 0}'''

NEW = '''                sys.path.insert(0, APP_DIR)
                import geo_recognize
                # 与「① 扫描」用同一来源，避免两边不一致
                _sg = scan_groups()
                groups = dict(_sg.get("groups") or {})
                if not groups:
                    return self._json({"ok": False, "error": "分组结果为空"})

                # 剔除成员名在模型里不存在的组（知识包残留旧体名等）
                _allnames = set(b.get("name") for b in (lb.get("bodies") or []))
                _dropped = {}
                _clean = {}
                for _g, _ns in groups.items():
                    _keep = [n for n in _ns if n in _allnames]
                    if _keep:
                        if len(_keep) != len(_ns):
                            _dropped[_g] = len(_ns) - len(_keep)
                        _clean[_g] = _keep
                groups = _clean
                if not groups:
                    return self._json({"ok": False, "error": "分组结果为空（成员都不存在）"})

                out = {"ok": True, "renamed": 0, "geom_groups": 0, "errors": [],
                       "group_count": len(groups),
                       "total": _sg.get("total") or len(_allnames),
                       "source": _sg.get("source")}
                if _dropped:
                    out["dropped_members"] = _dropped
                    out["errors"].append(
                        "以下分组的部分成员在模型里不存在，已忽略：%s"
                        % ", ".join("%s(%d)" % (k, v)
                                    for k, v in list(_dropped.items())[:6]))'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("② 已改用 scan_groups() 并剔除无效成员")
else:
    print("未找到 ② 来源段")

# 重命名后重新识别也要用 scan_groups，避免又走回旧路径
OLD2 = '''                    # 名字变了 → 重新识别一次，保证分组用的是新名字
                    lb2 = call_bridge("list_bodies", timeout=300)
                    if lb2.get("ok"):
                        rec2 = geo_recognize.recognize(lb2.get("bodies") or [], kb_extra_rules())
                        if rec2.get("groups"):
                            groups = rec2["groups"]'''
NEW2 = '''                    # 名字变了 → 重新扫描一次，保证分组用的是新名字
                    try:
                        _sg2 = scan_groups()
                        if _sg2.get("groups"):
                            groups = {g: [n for n in ns if n in
                                          set(b.get("name") for b in
                                              (call_bridge("list_bodies", timeout=300).get("bodies") or []))]
                                      for g, ns in _sg2["groups"].items()}
                            groups = {g: ns for g, ns in groups.items() if ns}
                    except Exception:
                        pass'''
if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)
    print("重命名后也改用 scan_groups()")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("server 语法 OK")
