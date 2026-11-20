# -*- coding: utf-8 -*-
"""几何识别 v2：加强特征（B 方案）+ 置信度（A 方案配套）
   新增特征：
     - 长径比 log(max/min)
     - 贯通比 max_dim / 容器最长边
     - 精确高度占比 y
     - 到两侧壁距离（x/z 归一化）
     - 附近件数（半径内邻居数，代理"连接/装配关系"）
     - 是否壳体
   输出：准确率、各类准确率、置信度分布、样本库 geo_model.json
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
bmap = {b.get("name"): b for b in (lb.get("bodies") or [])}

samples = []
for g in (gg.get("groups") or []):
    for n in (g.get("bodies") or []):
        b = bmap.get(n)
        if b:
            samples.append((b, g.get("name")))

xs = [b.get("cx") or 0 for b, _ in samples]
ys = [b.get("cy") or 0 for b, _ in samples]
zs = [b.get("cz") or 0 for b, _ in samples]
X0, X1 = min(xs), max(xs)
Y0, Y1 = min(ys), max(ys)
Z0, Z1 = min(zs), max(zs)
SPAN = max(X1 - X0, Z1 - Z0, 1.0)

# 预存质心，用于邻居统计
cent = [(b.get("cx") or 0, b.get("cy") or 0, b.get("cz") or 0) for b, _ in samples]


def neighbors(b, radius_ratio=0.12):
    bx, by, bz = b.get("cx") or 0, b.get("cy") or 0, b.get("cz") or 0
    r = SPAN * radius_ratio
    n = 0
    for (x, y, z) in cent:
        d = math.sqrt((x - bx) ** 2 + (y - by) ** 2 + (z - bz) ** 2)
        if 0 < d <= r:
            n += 1
    return n


def feat(b):
    d = sorted([max(1.0, b.get("lx") or 0), max(1.0, b.get("ly") or 0),
                max(1.0, b.get("lz") or 0)])
    bbox = d[0] * d[1] * d[2]
    fill = min(1.0, (b.get("vol") or 0) / bbox) if bbox else 0.0
    aspect = math.log(d[2] / max(d[0], 0.1))
    span = d[2] / SPAN
    cx = (b.get("cx") or 0 - X0) / max(1.0, X1 - X0)
    cy = ((b.get("cy") or 0) - Y0) / max(1.0, Y1 - Y0)
    cz = ((b.get("cz") or 0) - Z0) / max(1.0, Z1 - Z0)
    wd = min(abs(cx), abs(1 - cx))
    wd2 = min(abs(cz), abs(1 - cz))
    nb = neighbors(b) / 20.0
    return [math.log(d[0]), math.log(d[1]), math.log(d[2]),
            fill, aspect, span, cy, wd, wd2, nb,
            1.0 if b.get("shell") else 0.0]


WEIGHTS = [1.0, 1.0, 1.0, 1.6, 0.9, 0.9, 0.75, 0.5, 0.5, 0.6, 1.4]


def dist(a, b):
    s = 0.0
    for i in range(len(WEIGHTS)):
        dd = (a[i] - b[i]) * WEIGHTS[i]
        s += dd * dd
    return math.sqrt(s)


data = [(feat(b), lab) for b, lab in samples]

correct = 0
wrong = []
conf = []
for i, (f, lab) in enumerate(data):
    ds = sorted(((dist(f, g), j) for j, (g, l) in enumerate(data) if j != i))[:5]
    votes = Counter(data[j][1] for _, j in ds[:3])
    pred, cnt = votes.most_common(1)[0]
    similarity = 1.0 - ds[0][0] / 12.0
    conf.append(max(0.0, min(1.0, similarity)))
    if pred == lab:
        correct += 1
    else:
        wrong.append((samples[i][0].get("name"), lab, pred, round(max(0, similarity), 2)))

acc = correct / len(data)
print("样本: %d 体 / %d 部位" % (len(data), len(set(l for _, l in data))))
print("留一验证准确率: %.1f%%  (%d/%d)\n" % (acc * 100, correct, len(data)))

per = OrderedDict()
for lab in sorted(set(l for _, l in data)):
    idx = [i for i, (f, l) in enumerate(data) if l == lab]
    ok = 0
    for i in idx:
        ds = sorted(((dist(data[i][0], g), j) for j, (g, l2) in enumerate(data) if j != i))[:3]
        if Counter(data[j][1] for _, j in ds).most_common(1)[0][0] == lab:
            ok += 1
    per[lab] = (ok, len(idx))

print("各部位准确率:")
for lab, (ok, n) in sorted(per.items(), key=lambda kv: kv[1][0] / max(1, kv[1][1])):
    print("   %-14s %3d/%-3d %5.0f%%" % (lab, ok, n, 100.0 * ok / max(1, n)))

print("\n错分（前 15）:")
for n, t, p, c in wrong[:15]:
    print("   %-20s 实际:%-10s 预测:%-10s" % (n, t, p))

model = {"version": 2, "weights": WEIGHTS,
         "norm": {"x": [X0, X1], "y": [Y0, Y1], "z": [Z0, Z1], "span": SPAN},
         "samples": [{"f": data[i][0], "label": data[i][1],
                      "name": samples[i][0].get("name")} for i in range(len(data))],
         "accuracy": round(acc, 3)}
with open(r"D:\ai\meshkit\app\geo_model.json", "w", encoding="utf-8") as f:
    json.dump(model, f, ensure_ascii=False, indent=1)
print("\n样本库: app\\geo_model.json  准确率 %.1f%%" % (acc * 100))
