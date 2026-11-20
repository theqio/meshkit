# -*- coding: utf-8 -*-
"""从用户在 Geometry 里的手工分组，反推「部位识别规则」
   规则 = 尺寸指纹 + 填充率 → 部位名
   输出 app/part_rules.json
"""
import json
import urllib.request
from collections import OrderedDict


def post(p, payload=None, timeout=900):
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + p, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


gg = post("/api/op", {"op": "geom_groups", "payload": {}})
lb = post("/api/op", {"op": "list_bodies", "payload": {}})
bmap = {b.get("name"): b for b in (lb.get("bodies") or [])}
bs = [b for b in (lb.get("bodies") or []) if not b.get("suppressed")]
pos = sum(1 for b in bs if (b.get("cx") or 0) > 0)
INNER_POS = pos >= (len(bs) - pos)


def layer_of(b):
    y = b.get("cy") or 0
    return "下" if y < 3748 else ("上" if y > 6664 else "中")


def side_of(b):
    return "内" if ((b.get("cx") or 0) > 0) == INNER_POS else "外"


rules = OrderedDict()
for g in (gg.get("groups") or []):
    name = g.get("name")
    names = [n for n in (g.get("bodies") or []) if n in bmap]
    if not names:
        continue
    buckets = OrderedDict()
    for n in names:
        b = bmap[n]
        d = sorted([round(b.get("lx") or 0), round(b.get("ly") or 0),
                    round(b.get("lz") or 0)])
        bbox = max(1.0, d[0] * d[1] * d[2])
        fill = round((b.get("vol") or 0) / bbox / 0.05) * 0.05
        sig = "%dx%dx%d" % (d[0], d[1], d[2])
        buckets.setdefault(sig, {"count": 0, "fill": fill,
                                 "shell": bool(b.get("shell")),
                                 "layers": set(), "sides": set(),
                                 "samples": []})
        e = buckets[sig]
        e["count"] += 1
        e["layers"].add(layer_of(b))
        e["sides"].add(side_of(b))
        if len(e["samples"]) < 2:
            e["samples"].append(n)
    # 收集该部位的名字前缀（去掉末尾序号），用于名字识别
    _pref = OrderedDict()
    for n in names:
        import re as _re
        _p = _re.sub(r"[_\-\s]*\d+$", "", n).strip()
        _pref[_p] = _pref.get(_p, 0) + 1
    name_prefixes = [{"prefix": p, "count": c}
                     for p, c in sorted(_pref.items(), key=lambda kv: -kv[1]) if p]

    rules[name] = [{"sig": k, "count": v["count"], "fill": v["fill"],
                    "shell": v["shell"],
                    "layers": sorted(v["layers"]), "sides": sorted(v["sides"]),
                    "samples": v["samples"]}
                   for k, v in sorted(buckets.items(), key=lambda kv: -kv[1]["count"])]
    rules[name] = {"sizes": rules[name], "name_prefixes": name_prefixes,
                   "total": len(names)}

out = r"D:\ai\meshkit\app\part_rules.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump({"source": "user_geometry_groups", "rules": rules},
              f, ensure_ascii=False, indent=1)

print("从你的分组反推出规则：%d 个部位\n" % len(rules))
print("%-12s %-18s %5s %6s %-8s %-6s %s" %
      ("部位", "尺寸(mm)", "体数", "填充率", "层", "侧", "示例"))
print("-" * 96)
for name, info in rules.items():
    items = info["sizes"]
    total = info["total"]
    for i, r in enumerate(items):
        print("%-12s %-18s %5d %6.2f %-8s %-6s %s" % (
            name if i == 0 else "",
            r["sig"], r["count"], r["fill"],
            "/".join(r["layers"]), "/".join(r["sides"]),
            (r["samples"] or [""])[0]))
    print("%-12s ↳ %d 个尺寸档 / %d 体   名字: %s" % (
        "", len(items), total,
        "、".join(x["prefix"] for x in info["name_prefixes"][:3])))
print("\n规则已保存:", out)

multi = {k: v for k, v in rules.items() if len(v["sizes"]) > 1}
print("\n包含多个尺寸的部位（%d 个）:" % len(multi))
for k, v in multi.items():
    print("   %-12s %s" % (k, " + ".join(x["sig"] for x in v["sizes"])))
