# -*- coding: utf-8 -*-
"""输出每个分组的具体位置（中心坐标 + 范围 + 方位描述）"""
import json
import urllib.request


def post(p, payload=None, timeout=600):
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + p, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


lb = post("/api/op", {"op": "list_bodies", "payload": {}})
bs = [b for b in (lb.get("bodies") or []) if not b.get("suppressed")]


def is_wave(n):
    return ("波纹" in n) or ("WAVE" in n.upper())


def is_bottom(b, n):
    d = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
    return (d[0] < 60 and d[1] > 400 and d[2] > 400) or ("底板" in n)


ys = sorted([b.get("cy") or 0 for b in bs])
y0, y1 = ys[0], ys[-1]
span = max(1.0, y1 - y0)
pos = sum(1 for b in bs if (b.get("cx") or 0) > 0)
inner_pos = pos >= (len(bs) - pos)

for b in bs:
    n = (b.get("name") or "").strip()
    _cyv = b.get("cy") or 0
    layer = "下" if _cyv < 3748 else ("上" if _cyv > 6664 else "中")
    side = "内" if ((b.get("cx") or 0) > 0) == inner_pos else "外"
    elem = "壳" if b.get("shell") else "实体"
    if layer == "上":
        role = "上层整体"
    elif is_wave(n):
        role = "波纹板"
    elif layer == "下" and is_bottom(b, n):
        role = "大平面底板"
    else:
        role = "其余结构"
    dd = sorted([round(b.get("lx") or 0), round(b.get("ly") or 0),
                 round(b.get("lz") or 0)])
    b["_sig"] = "%dx%dx%d" % (dd[0], dd[1], dd[2])
    b["_key"] = "%s_%s侧_%s层_%s_%s" % (elem, side, layer, role, b["_sig"])

counts = {}
for b in bs:
    counts[b["_key"]] = counts.get(b["_key"], 0) + 1
order = sorted(counts.items(), key=lambda kv: -kv[1])
num = {k: i for i, (k, c) in enumerate(order, 1)}

xs = [abs(b.get("cx") or 0) for b in bs]
zs = [abs(b.get("cz") or 0) for b in bs]

groups = {}
for b in bs:
    groups.setdefault(num[b["_key"]], []).append(b)

lines = []
for no in sorted(groups):
    items = groups[no]
    xs_ = [b.get("cx") or 0 for b in items]
    ys_ = [b.get("cy") or 0 for b in items]
    zs_ = [b.get("cz") or 0 for b in items]
    key = items[0]["_key"]
    dims = items[0]["_sig"]
    sx = "右" if sum(1 for v in xs_ if v > 0) > len(xs_) / 2 else "左"
    sz = "前" if sum(1 for v in zs_ if v > 0) > len(zs_) / 2 else "后"
    layer = "上" if "_上" in key else ("下" if "_下" in key else "中")
    lines.append(
        "%2d | %-40s | %3d | %-14s | X %7.0f~%-7.0f | Y %7.0f~%-7.0f | Z %7.0f~%-7.0f | %s-%s-%s"
        % (no, key, len(items), dims,
           min(xs_), max(xs_), min(ys_), max(ys_), min(zs_), max(zs_),
           sx, sz, layer))

head = ("序号 | 分组                                    | 体数 | 尺寸           | "
        "X 范围            | Y 范围            | Z 范围            | 方位")
out = head + "\n" + "-" * 150 + "\n" + "\n".join(lines)
with open(r"D:\ai\meshkit\reports\结构位置.txt", "w", encoding="utf-8") as f:
    f.write(out)
print(out[:3000])
