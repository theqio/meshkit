# -*- coding: utf-8 -*-
"""① 与 ② 用同一个分组来源，避免"扫出来有、落地没有"
   1) ② 直接复用 scan_groups() 的结果（不再自己重新识别）
   2) 落地前剔除成员名在模型里不存在的组（并记录下来）
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

OLD = '''                # 落地时以「几何识别 + 人工调整」为准（Geometry 现有分组只是输出）
                lb0 = call_bridge("list_bodies", timeout=300)
                _rec0 = geo_recognize.recognize(lb0.get("bodies") or [], kb_extra_rules())
                groups = _rec0.get("groups") or {}
                _sg = {"total": _rec0.get("total"), "source": "rules+overrides"}'''

NEW = '''                # 落地与「① 扫描」用同一来源，避免两边不一致
                _sg = scan_groups()
                groups = dict(_sg.get("groups") or {})

                # 剔除成员名不存在的组（例如知识包里残留的旧体名）
                _lb0 = call_bridge("list_bodies", timeout=300)
                _names0 = set(b.get("name") for b in (_lb0.get("bodies") or []))
                _dropped = {}
                _clean = {}
                for _g, _ns in groups.items():
                    _keep = [n for n in _ns if n in _names0]
                    if _keep:
                        if len(_keep) != len(_ns):
                            _dropped[_g] = len(_ns) - len(_keep)
                        _clean[_g] = _keep
                if _dropped:
                    out_extra_dropped = _dropped
                groups = _clean'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("② 已改为复用 ① 的分组来源")
else:
    print("未找到 ② 的分组来源段")

# 把剔除信息写进返回
OLD2 = '''                out = {"ok": True, "renamed": 0, "geom_groups": 0, "errors": [],
                       "group_count": len(groups),
                       "total": _sg.get("total") or 0,
                       "source": _sg.get("source")}'''
NEW2 = '''                out = {"ok": True, "renamed": 0, "geom_groups": 0, "errors": [],
                       "group_count": len(groups),
                       "total": _sg.get("total") or 0,
                       "source": _sg.get("source")}
                try:
                    if _dropped:
                        out["dropped_members"] = out_extra_dropped
                        out["errors"].append(
                            "以下分组的成员在模型里不存在，已剔除：%s"
                            % ", ".join("%s(%d)" % (k, v)
                                        for k, v in list(_dropped.items())[:5]))
                except Exception:
                    pass'''
if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)
    print("② 返回已带剔除信息")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("server 语法 OK")
