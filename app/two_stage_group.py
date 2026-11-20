# -*- coding: utf-8 -*-
"""两阶段分组
   阶段A（最高优先级）：尺寸+形状相同的件成组 → 这些件从待分组集合中排除
   阶段B：剩余（尺寸唯一的零星件）再按规则 1~7 顺序分组
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
    return "%dx%dx%d|%.2f|%s" % (round(d[0]), round(d[1]), round(d[2]),
                                 round(fill / 0.05) * 0.05,
                                 "壳" if b.get("shell") else "实体")


def layer_of(b):
    y = b.get("cy") or 0
    return "下" if y < 3748 else ("上" if y > 6664 else "中")


def side_of(b):
    return "内" if ((b.get("cx") or 0) > 0) == INNER_POS else "外"


def is_wave(b):
    n = b.get("name") or ""
    return ("波纹" in n) or ("WAVE" in n.upper())


def is_bottom(b):
    d = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
    n = b.get("name") or ""
    return (d[0] < 60 and d[1] > 400 and d[2] > 400) or ("底板" in n)


# ============ 阶段A：尺寸+形状相同成组，然后排除 ============
buckets = OrderedDict()
for b in bs:
    buckets.setdefault(shape_sig(b), []).append(b)

stage_a = []          # 已完成的组
rest = []             # 剩余待分组
for k, items in buckets.items():
    if len(items) >= 2:
        stage_a.append({"label": "★" + k, "bodies": items})
    else:
        rest.extend(items)

print("=" * 90)
print("【阶段A】尺寸+形状相同的件先成组（然后排除）")
print("   成组: %d 组 / %d 体" % (len(stage_a), sum(len(g["bodies"]) for g in stage_a)))
print("   剩余待分组: %d 体" % len(rest))
print("=" * 90)
for g in sorted(stage_a, key=lambda x: -len(x["bodies"])):
    items = g["bodies"]
    ls = sorted(set(layer_of(b) for b in items))
    ss = sorted(set(side_of(b) for b in items))
    print("   %-30s %3d 体   层:%-6s 侧:%s" % (
        g["label"], len(items), "/".join(ls), "/".join(ss)))

# ============ 阶段B：剩余件按规则 1~7 ============
print("\n" + "=" * 90)
print("【阶段B】剩余 %d 件按结构规则分组" % len(rest))
print("=" * 90)

cur = [{"label": "剩余", "bodies": rest}]


def split(key_fn, suffix=False):
    out = []
    for g in cur:
        bk = OrderedDict()
        for b in g["bodies"]:
            bk.setdefault(key_fn(b), []).append(b)
        for k, items in bk.items():
            out.append({"label": ("%s|%s" % (g["label"], k)) if suffix else k,
                        "bodies": items})
    return out


def show(tag, title):
    print("\n-- %s %s → %d 组" % (tag, title, len(cur)))
    for g in sorted(cur, key=lambda x: -len(x["bodies"])):
        print("     %-40s %3d 体" % (g["label"], len(g["bodies"])))


# 规则1 单元类型
cur = split(lambda b: "壳" if b.get("shell") else "实体")
show("规则1", "按单元类型分离")

# 规则3 内外 + 上中下
cur = split(lambda b: "%s%s" % (side_of(b), ""), )
cur = split(lambda b: "%s侧-%s层" % (side_of(b), layer_of(b)), suffix=True)
show("规则3", "按 内/外 × 上/中/下 拆分")

# 规则4 上层整体
out = OrderedDict()
for g in cur:
    if "上层" in g["label"]:
        k = g["label"].split("|")[0] + "|上层整体"
        out.setdefault(k, []).extend(g["bodies"])
    else:
        out.setdefault(g["label"], []).extend(g["bodies"])
cur = [{"label": k, "bodies": v} for k, v in out.items()]
show("规则4", "上层整体合并")

# 规则5/6/7 角色桶
def role_of(b, label):
    layer = layer_of(b)
    if layer == "上":
        return "上层整体"
    if is_wave(b):
        return "波纹板"
    if layer == "下" and is_bottom(b):
        return "大平面底板"
    return "其余"


out = OrderedDict()
for g in cur:
    for b in g["bodies"]:
        k = g["label"].split("|")[0] + "|" + role_of(b, g["label"])
        out.setdefault(k, []).append(b)
cur = [{"label": k, "bodies": v} for k, v in out.items()]
show("规则5-7", "波纹板 / 大平面底板 / 其余 分开")

# ============ 汇总 ============
final = stage_a + cur
print("\n" + "=" * 90)
print("最终: %d 组 / %d 体（阶段A %d 组 + 阶段B %d 组）" % (
    len(final), sum(len(g["bodies"]) for g in final), len(stage_a), len(cur)))
print("=" * 90)
lines = []
for i, g in enumerate(sorted(final, key=lambda x: -len(x["bodies"])), 1):
    lines.append("%2d. %-42s %3d 体" % (i, g["label"], len(g["bodies"])))
txt = "\n".join(lines)
print(txt)
open(r"D:\ai\meshkit\reports\两阶段分组.txt", "w", encoding="utf-8").write(txt)
