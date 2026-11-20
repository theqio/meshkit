# -*- coding: utf-8 -*-
"""基于接触关系 + 朝向的识别（v3）
   规则1 上支撑梁：13 体互相接触 → 同一装配体（连通分量）
   规则2 上梁：先找与纵梁接触的，再扩散到与这些上梁接触的；剩余同类为横梁
   规则3 中隔断板=YZ 大平面（X 最薄）；底板=XZ 大平面（Y 最薄）
   规则4 侧固定件：尺寸 50×200×209 且与波纹板接触
   其余未识别 → 其他
"""
import json
import math
import urllib.request
from collections import Counter, OrderedDict


def post(p, payload=None, timeout=900):
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + p, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


gg = post("/api/op", {"op": "geom_groups", "payload": {}})
lb = post("/api/op", {"op": "list_bodies", "payload": {}})
bodies = [b for b in (lb.get("bodies") or []) if not b.get("suppressed")]
truth = {}
for g in (gg.get("groups") or []):
    for n in (g.get("bodies") or []):
        truth[n] = g.get("name")

TOL = 3.0   # 接触容差 mm


def box(b):
    cx, cy, cz = b.get("cx") or 0, b.get("cy") or 0, b.get("cz") or 0
    hx, hy, hz = (b.get("lx") or 0) / 2.0, (b.get("ly") or 0) / 2.0, (b.get("lz") or 0) / 2.0
    return (cx - hx, cx + hx, cy - hy, cy + hy, cz - hz, cz + hz)


BOX = {b["name"]: box(b) for b in bodies}


def gap(a, b):
    ax0, ax1, ay0, ay1, az0, az1 = BOX[a]
    bx0, bx1, by0, by1, bz0, bz1 = BOX[b]
    gx = max(0.0, max(ax0, bx0) - min(ax1, bx1))
    gy = max(0.0, max(ay0, by0) - min(ay1, by1))
    gz = max(0.0, max(az0, bz0) - min(az1, bz1))
    return math.sqrt(gx * gx + gy * gy + gz * gz)


def touching(a, b):
    return gap(a, b) <= TOL


names = [b["name"] for b in bodies]
adj = {n: set() for n in names}
for i in range(len(names)):
    for j in range(i + 1, len(names)):
        if touching(names[i], names[j]):
            adj[names[i]].add(names[j])
            adj[names[j]].add(names[i])

print("接触对数量:", sum(len(v) for v in adj.values()) // 2)


def component(seed, pool=None):
    seen = {seed}
    stack = [seed]
    while stack:
        cur = stack.pop()
        for nb in adj[cur]:
            if nb in seen:
                continue
            if pool is not None and nb not in pool:
                continue
            seen.add(nb)
            stack.append(nb)
    return seen


dmap = {b["name"]: b for b in bodies}


def dims(n):
    b = dmap[n]
    return sorted([round(b.get("lx") or 0), round(b.get("ly") or 0),
                   round(b.get("lz") or 0)])


pred = {}

# ---- 规则3：朝向判板（先做，避免被别的规则抢走）----
for b in bodies:
    n = b["name"]
    lx, ly, lz = b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0
    if lx > 0 and lx < 12 and ly > 400 and lz > 400:
        pred[n] = "中隔断薄板"
    elif ly > 0 and ly < 12 and lx > 400 and lz > 400:
        pred[n] = "底板"

# ---- 规则1：上支撑梁 = 含已知尺寸的连通装配体 ----
support_sizes = {"50x50x100", "55x80x150", "58x100x100", "3x48x98",
                 "10x100x200", "50x100x2594"}
seeds = [n for n in names
         if "%dx%dx%d" % tuple(dims(n)) in support_sizes
         and (dmap[n].get("cy") or 0) > 6664]
support = set()
for s in seeds:
    comp = component(s)
    if len(comp) >= 5 and all((dmap[c].get("cy") or 0) > 6000 for c in comp):
        support |= comp
for n in support:
    pred[n] = "上支撑梁"

# ---- 规则2：上梁 = 与纵梁接触的连通体 ----
beam_sizes = {"100x150x2194", "100x200x2114"}
long_sizes = {"100x150x11868", "100x200x11836"}
纵向 = [n for n in names if "%dx%dx%d" % tuple(dims(n)) in long_sizes]
上梁 = set()
for n in 纵向:
    for nb in adj[n]:
        if "%dx%dx%d" % tuple(dims(nb)) in beam_sizes:
            上梁 |= component(nb, pool=set(
                x for x in names if "%dx%dx%d" % tuple(dims(x)) in beam_sizes
                or "%dx%dx%d" % tuple(dims(x)) in long_sizes))
for n in 上梁:
    if n not in pred:
        pred[n] = "上梁"
for n in names:
    if n not in pred and "%dx%dx%d" % tuple(dims(n)) in beam_sizes:
        pred[n] = "横梁"

# ---- 规则4：侧固定件 = 50×200×209 且接触波纹板 ----
wave = set(n for n in names if dmap[n].get("shell"))
for n in names:
    if n in pred:
        continue
    if "%dx%dx%d" % tuple(dims(n)) == "50x200x209":
        if any(nb in wave for nb in adj[n]):
            pred[n] = "侧固定件"

# ---- 其余：用尺寸表 + 最近邻补 ----
rule = json.load(open(r"D:\ai\meshkit\app\part_rules.json", encoding="utf-8"))
sig2lab = {}
for lab, info in rule["rules"].items():
    for s in info["sizes"]:
        sig2lab.setdefault(s["sig"], lab)

for n in names:
    if n in pred:
        continue
    sg = "%dx%dx%d" % tuple(dims(n))
    if sg in sig2lab:
        pred[n] = sig2lab[sg]

# ---- 剩下的归"其他" ----
for n in names:
    pred.setdefault(n, "其他")

# ---- 评估 ----
ok = sum(1 for n in names if pred.get(n) == truth.get(n))
print("识别准确率: %.1f%% (%d/%d)" % (100.0 * ok / len(names), ok, len(names)))

per = OrderedDict()
for n in names:
    t = truth.get(n, "?")
    per.setdefault(t, [0, 0])
    per[t][1] += 1
    if pred.get(n) == t:
        per[t][0] += 1
print("\n各部位准确率:")
for lab, (a, t) in sorted(per.items(), key=lambda kv: kv[1][0] / max(1, kv[1][1])):
    print("   %-14s %3d/%-3d %5.0f%%" % (lab, a, t, 100.0 * a / max(1, t)))

print("\n错分（前 15）:")
cnt = 0
for n in names:
    if pred.get(n) != truth.get(n) and cnt < 15:
        print("   %-20s 实际:%-10s 预测:%-10s" % (n, truth.get(n), pred.get(n)))
        cnt += 1

with open(r"D:\ai\meshkit\reports\识别结果.json", "w", encoding="utf-8") as f:
    json.dump({"pred": pred, "truth": truth}, f, ensure_ascii=False, indent=1)
print("\n结果已保存 reports\\识别结果.json")
