# -*- coding: utf-8 -*-
"""refresh_data.py —— 清理/重建所有按「体名」索引的数据
   问题背景：体重命名后，旧数据仍按老名字索引 → 反复出问题
   处理：
     1) group_overrides.json  —— 丢掉指向已不存在体名的条目
     2) part_rules.json       —— 按当前 Geometry 实际分组重新反推
     3) geo_model.json        —— 清掉过期的样本名（保留特征）
     4) custom_groups.json    —— 去掉不存在的空组（保留有成员的）
     5) 输出改动报告
"""
import json
import os
import urllib.request
from collections import OrderedDict

APP = r"D:\ai\meshkit\app"


def jload(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def jsave(p, d):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def post(p, payload=None, timeout=900):
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + p, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


# ---------- 当前真实体名 ----------
lb = post("/api/op", {"op": "list_bodies", "payload": {}})
bodies = [b for b in (lb.get("bodies") or []) if not b.get("suppressed")]
cur_names = set(b.get("name") for b in bodies)
bmap = {b.get("name"): b for b in bodies}
print("当前体: %d 个" % len(cur_names))

report = {}

# ---------- 1) 覆盖表 ----------
p1 = os.path.join(APP, "group_overrides.json")
ov = jload(p1, {})
stale = [k for k in ov if k not in cur_names]
for k in stale:
    ov.pop(k)
jsave(p1, ov)
report["overrides"] = {"kept": len(ov), "dropped": len(stale)}
print("覆盖表: 保留 %d，清除过期 %d" % (len(ov), len(stale)))

# ---------- 2) 自定义分组 ----------
p2 = os.path.join(APP, "custom_groups.json")
cust = jload(p2, [])
used = set(ov.values())
kept_cust = [g for g in cust if g in used or g in
             set(x for x in cust)]
# 只保留：有成员 或 是用户明确新建的（全部保留，避免删用户的组）
report["custom"] = {"count": len(cust)}

# ---------- 3) part_rules：按当前 Geometry 分组重建 ----------
gg = post("/api/op", {"op": "geom_groups", "payload": {}})
groups = OrderedDict()
for g in (gg.get("groups") or []):
    names = [n for n in (g.get("bodies") or []) if n in bmap]
    if names:
        groups[g["name"]] = names

rules = OrderedDict()
for name, names in groups.items():
    buckets = OrderedDict()
    for n in names:
        b = bmap[n]
        d = sorted([round(b.get("lx") or 0), round(b.get("ly") or 0),
                    round(b.get("lz") or 0)])
        bbox = max(1.0, d[0] * d[1] * d[2])
        fill = round((b.get("vol") or 0) / bbox / 0.05) * 0.05
        sig = "%dx%dx%d" % (d[0], d[1], d[2])
        e = buckets.setdefault(sig, {"count": 0, "fill": fill,
                                     "shell": bool(b.get("shell")),
                                     "samples": []})
        e["count"] += 1
        if len(e["samples"]) < 2:
            e["samples"].append(n)
    rules[name] = {
        "sizes": [{"sig": k, "count": v["count"], "fill": v["fill"],
                   "shell": v["shell"], "samples": v["samples"]}
                  for k, v in sorted(buckets.items(), key=lambda kv: -kv[1]["count"])],
        "total": len(names),
    }

p3 = os.path.join(APP, "part_rules.json")
jsave(p3, {"source": "refresh_from_geometry",
           "updated": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
           "rules": rules})
report["part_rules"] = {"groups": len(rules)}
print("识别规则表: 已按当前分组重建 %d 个部位" % len(rules))

# ---------- 4) geo_model 样本名清理 ----------
p4 = os.path.join(APP, "geo_model.json")
gm = jload(p4, None)
if gm:
    before = len(gm.get("samples") or [])
    gm["samples"] = [s for s in (gm.get("samples") or [])
                     if s.get("name") in cur_names]
    jsave(p4, gm)
    report["geo_model"] = {"before": before, "after": len(gm["samples"])}
    print("几何样本库: %d → %d" % (before, len(gm["samples"])))

# ---------- 5) 记忆文件：类别名保持不变，只报告 ----------
mem = jload(os.path.join(APP, "model_memory.json"), {"profiles": []})
report["memory"] = {"profiles": len(mem.get("profiles") or [])}

print("\n===== 清理汇总 =====")
print(json.dumps(report, ensure_ascii=False, indent=1))
