# -*- coding: utf-8 -*-
"""分组：尺寸/形状优先级最高（B 方案）
   1) 先按 (单元类型, 尺寸指纹, 形状指纹) 成组 —— 此后不再拆
   2) 规则 4~7 只能"合并"、不能拆已有尺寸组；本版先只做规则 2
   3) 内外侧/上中下 只作为标签记录，不作为拆分依据
"""
import json
import urllib.request
from collections import OrderedDict


def post(p, payload=None, timeout=600):
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + p, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


lb = post("/api/op", {"op": "list_bodies", "payload": {}})
bs = [b for b in (lb.get("bodies") or []) if not b.get("suppressed")]
pos = sum(1 for b in bs if (b.get("cx") or 0) > 0)
INNER_POS = pos >= (len(bs) - pos)


def shape_sig(b):
    d = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
    bbox = max(1.0, d[0] * d[1] * d[2])
    fill = (b.get("vol") or 0) / bbox
    # 形状指纹：尺寸 + 填充率档（0.05 一档）+ 是否壳体
    return "%dx%dx%d|填充%.2f|%s" % (
        round(d[0]), round(d[1]), round(d[2]),
        round(fill / 0.05) * 0.05,
        "壳" if b.get("shell") else "实体")


def layer_of(b):
    y = b.get("cy") or 0
    return "下" if y < 3748 else ("上" if y > 6664 else "中")


def side_of(b):
    return "内" if ((b.get("cx") or 0) > 0) == INNER_POS else "外"


groups = OrderedDict()
for b in bs:
    k = shape_sig(b)
    groups.setdefault(k, []).append(b)

print("=" * 88)
print("【最高优先级：尺寸相同 + 形状相同 → 同一组】")
print("分组数: %d   体数: %d" % (len(groups), sum(len(v) for v in groups.values())))
print("=" * 88)

rows = []
for k, items in groups.items():
    layers = sorted(set(layer_of(b) for b in items))
    sides = sorted(set(side_of(b) for b in items))
    rows.append((k, len(items), "/".join(layers), "/".join(sides)))
rows.sort(key=lambda r: -r[1])

lines = []
for i, (k, n, layers, sides) in enumerate(rows, 1):
    line = "%2d. %-34s %3d 体   层:%-6s 侧:%s" % (i, k, n, layers, sides)
    lines.append(line)
    print(line)

txt = "\n".join(lines)
open(r"D:\ai\meshkit\reports\按尺寸形状分组.txt", "w", encoding="utf-8").write(txt)

cross = [r for r in rows if "/" in r[2] or "/" in r[3]]
print("\n跨层或跨内外侧的组（因尺寸相同而合并）: %d 组" % len(cross))
for k, n, layers, sides in cross[:10]:
    print("   %-34s %3d 体  层:%-6s 侧:%s" % (k, n, layers, sides))
