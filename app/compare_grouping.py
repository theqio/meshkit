# -*- coding: utf-8 -*-
"""对比：用户在 Geometry 里的手工分组 vs 我按规则算的分组"""
import json
import urllib.request
from collections import OrderedDict, Counter


def post(p, payload=None, timeout=900):
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + p, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


# ---------- 用户手工分组 ----------
gg = post("/api/op", {"op": "geom_groups", "payload": {}})
user_groups = OrderedDict()
for g in (gg.get("groups") or []):
    names = set(g.get("bodies") or [])
    if names:
        user_groups[g["name"]] = names
print("用户在 Geometry 里的分组: %d 组" % len(user_groups))
for n, s in sorted(user_groups.items(), key=lambda kv: -len(kv[1])):
    print("   %-24s %3d 体" % (n, len(s)))

# ---------- 我的算法分组 ----------
lb = post("/api/op", {"op": "list_bodies", "payload": {}})
bs = [b for b in (lb.get("bodies") or []) if not b.get("suppressed")]
pos = sum(1 for b in bs if (b.get("cx") or 0) > 0)
INNER_POS = pos >= (len(bs) - pos)


def shape_sig(b):
    d = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
    bbox = max(1.0, d[0] * d[1] * d[2])
    fill = (b.get("vol") or 0) / bbox
    return "%dx%dx%d|%.2f|%s" % (round(d[0]), round(d[1]), round(d[2]),
                                 round(fill / 0.05) * 0.05,
                                 "壳" if b.get("shell") else "实体")


def layer_of(b):
    y = b.get("cy") or 0
    return "下" if y < 3748 else ("上" if y > 6664 else "中")


buckets = OrderedDict()
for b in bs:
    buckets.setdefault(shape_sig(b), []).append(b)

mine = OrderedDict()
rest = []
for k, items in buckets.items():
    if len(items) >= 2:
        mine["★" + k] = set(b.get("name") for b in items)
    else:
        rest.extend(items)

# 剩余件按 层 归并（简化比对用）
rem = OrderedDict()
for b in rest:
    rem.setdefault("剩余-" + layer_of(b), set()).add(b.get("name"))
mine.update(rem)

print("\n我的算法分组: %d 组" % len(mine))

# ---------- 逐组比对 ----------
print("\n" + "=" * 92)
print("逐组对照（用户组 → 我最接近的算法组）")
print("=" * 92)
used = set()
rows = []
for un, us in sorted(user_groups.items(), key=lambda kv: -len(kv[1])):
    best, best_j, best_score = None, 0.0, -1.0
    for mn, ms in mine.items():
        inter = len(us & ms)
        if inter == 0:
            continue
        j = inter / float(len(us | ms))
        if j > best_score:
            best, best_j, best_score = mn, j, j
    rows.append((un, len(us), best, best_score))
    if best:
        used.add(best)
    print("%-22s %3d 体  →  %-38s  重合度 %.0f%%" % (
        un, len(us), (best or "（无对应）")[:38], best_score * 100))

print("\n我的算法里有、但用户没分的组:")
for mn, ms in mine.items():
    if mn not in used:
        print("   %-40s %3d 体" % (mn, len(ms)))

avg = sum(r[3] for r in rows) / max(1, len(rows))
print("\n平均重合度: %.0f%%" % (avg * 100))
