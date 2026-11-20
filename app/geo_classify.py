# -*- coding: utf-8 -*-
"""纯几何识别：用用户在 Geometry 里分好的 23 组作为训练样本
   特征 = 三向尺寸(对数) + 填充率 + 质心位置(归一化)
   方法 = k 近邻（k=3），留一验证测准确率
   输出 app/geo_model.json（样本库）+ 准确率报告
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

samples = []          # (features, label)
for g in (gg.get("groups") or []):
    label = g.get("name")
    for n in (g.get("bodies") or []):
        b = bmap.get(n)
        if not b:
            continue
        samples.append((b, label))

print("训练样本: %d 体 / %d 个部位" % (len(samples), len(set(s[1] for s in samples))))

# 归一化基准
xs = [b.get("cx") or 0 for b, _ in samples]
ys = [b.get("cy") or 0 for b, _ in samples]
zs = [b.get("cz") or 0 for b, _ in samples]
X0, X1 = min(xs), max(xs)
Y0, Y1 = min(ys), max(ys)
Z0, Z1 = min(zs), max(zs)


def feat(b):
    d = sorted([max(1.0, b.get("lx") or 0), max(1.0, b.get("ly") or 0),
                max(1.0, b.get("lz") or 0)])
    bbox = d[0] * d[1] * d[2]
    fill = (b.get("vol") or 0) / bbox if bbox else 0
    return [
        math.log(d[0]), math.log(d[1]), math.log(d[2]),
        min(1.0, fill),
        ((b.get("cx") or 0) - X0) / max(1.0, X1 - X0),
        ((b.get("cy") or 0) - Y0) / max(1.0, Y1 - Y0),
        ((b.get("cz") or 0) - Z0) / max(1.0, Z1 - Z0),
    ]


def dist(a, b):
    # 尺寸权重 1.0，位置权重 0.55（位置用于区分同尺寸不同部件）
    w = [1.0, 1.0, 1.0, 1.6, 0.55, 0.55, 0.55]
    s = 0.0
    for i in range(7):
        dd = (a[i] - b[i]) * w[i]
        s += dd * dd
    return math.sqrt(s)


data = [(feat(b), lab) for b, lab in samples]

# ---------- 留一验证 ----------
correct = 0
wrong = []
for i, (f, lab) in enumerate(data):
    best = sorted(((dist(f, g), j) for j, (g, l) in enumerate(data) if j != i))[:3]
    votes = Counter(data[j][1] for _, j in best)
    pred, cnt = votes.most_common(1)[0]
    if pred == lab:
        correct += 1
    else:
        wrong.append((samples[i][0].get("name"), lab, pred))
acc = correct / len(data)
print("\n留一验证准确率: %.1f%%  (%d/%d)" % (acc * 100, correct, len(data)))

print("\n错分的体（前 20）:")
for n, t, p in wrong[:20]:
    print("   %-22s 实际:%-12s 预测:%-12s" % (n, t, p))

# ---------- 保存样本库 ----------
model = {
    "version": 1,
    "norm": {"x": [X0, X1], "y": [Y0, Y1], "z": [Z0, Z1]},
    "labels": sorted(set(s[1] for s in samples)),
    "samples": [{"f": data[i][0], "label": data[i][1],
                 "name": samples[i][0].get("name")} for i in range(len(data))],
    "accuracy": round(acc, 3),
}
out = r"D:\ai\meshkit\app\geo_model.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(model, f, ensure_ascii=False, indent=1)
print("\n样本库已保存:", out)

# 每类准确率
per = OrderedDict()
for lab in model["labels"]:
    idx = [i for i, (f, l) in enumerate(data) if l == lab]
    ok = sum(1 for i in idx
             if data[i][1] == Counter(
                 data[j][1] for _, j in
                 sorted(((dist(data[i][0], g), k) for k, (g, l2) in enumerate(data) if k != i))[:3]
             ).most_common(1)[0][0])
    per[lab] = (ok, len(idx))
print("\n各部位准确率:")
for lab, (ok, n) in sorted(per.items(), key=lambda kv: kv[1][0] / max(1, kv[1][1])):
    print("   %-14s %3d/%-3d  %5.0f%%" % (lab, ok, n, 100.0 * ok / max(1, n)))
