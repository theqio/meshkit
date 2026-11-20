# -*- coding: utf-8 -*-
"""按 7 条规则「逐条顺序」分组，每步输出中间结果
   规则顺序：1 单元类型 → 2 形状尺寸 → 3 内外+上中下 → 4 上层整体
             → 5 内侧(波纹板/其余) → 6 下层(底板/其余) → 7 外侧(波纹板/其余)
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

# ---------- 基础属性 ----------
pos = sum(1 for b in bs if (b.get("cx") or 0) > 0)
INNER_POS = pos >= (len(bs) - pos)


def elem_of(b):
    return "壳" if b.get("shell") else "实体"


def side_of(b):
    return "内" if ((b.get("cx") or 0) > 0) == INNER_POS else "外"


def layer_of(b):
    y = b.get("cy") or 0
    return "下" if y < 3748 else ("上" if y > 6664 else "中")


def size_sig(b):
    d = sorted([round(b.get("lx") or 0), round(b.get("ly") or 0), round(b.get("lz") or 0)])
    return "%dx%dx%d" % (d[0], d[1], d[2])


def is_wave(b):
    n = b.get("name") or ""
    return ("波纹" in n) or ("WAVE" in n.upper())


def is_bottom(b):
    d = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
    n = b.get("name") or ""
    return (d[0] < 60 and d[1] > 400 and d[2] > 400) or ("底板" in n)


for b in bs:
    b["_elem"] = elem_of(b)
    b["_side"] = side_of(b)
    b["_layer"] = layer_of(b)
    b["_sig"] = size_sig(b)

# 当前分组 = list of dict(label, bodies)
cur = [{"label": "全部", "bodies": bs}]


def report(stage, title):
    sizes = sorted(((g["label"], len(g["bodies"])) for g in cur),
                   key=lambda kv: -kv[1])
    print("\n" + "=" * 78)
    print("【%s】%s" % (stage, title))
    print("分组数: %d   体数: %d" % (len(cur), sum(s[1] for s in sizes)))
    for lab, n in sizes[:20]:
        print("   %-46s %3d" % (lab, n))
    if len(sizes) > 20:
        print("   ... 其余 %d 组" % (len(sizes) - 20))
    return sizes


def split(key_fn, suffix_fn=None):
    out = []
    for g in cur:
        buckets = OrderedDict()
        for b in g["bodies"]:
            k = key_fn(b)
            buckets.setdefault(k, []).append(b)
        for k, items in buckets.items():
            label = k if suffix_fn is None else "%s|%s" % (g["label"], k)
            out.append({"label": label, "bodies": items})
    return out


# ---------- 规则 1：单元类型不能混 ----------
cur = split(lambda b: b["_elem"])
report("规则1", "按单元类型分离（壳/实体不得混组）")

# ---------- 规则 2：形状尺寸一致优先成组 ----------
cur = split(lambda b: b["_sig"], suffix_fn=True)
s1 = report("规则2", "同形状同尺寸合为一组（每档独立）")

# ---------- 规则 3：内/外 + 上/中/下，两个维度都不许混 ----------
cur = split(lambda b: "%s侧-%s层" % (b["_side"], b["_layer"]), suffix_fn=True)
report("规则3", "按 内/外 × 上/中/下 拆分（六个区互不混）")

# ---------- 规则 4：上层整体一组 ----------
out = []
for g in cur:
    parts = g["label"].split("|")
    elem = g["label"].split("|")[0]
    if "上层" in parts[-1]:
        out.append({"label": "%s|上层整体" % elem, "bodies": g["bodies"],
                    "_merge": "%s|上层整体" % elem})
    else:
        out.append(g)
merged = OrderedDict()
for g in out:
    k = g.get("_merge") or g["label"]
    merged.setdefault(k, []).extend(g["bodies"])
cur = [{"label": k, "bodies": v} for k, v in merged.items()]
report("规则4", "上层结构整体并为一组（不再按尺寸细分）")

# ---------- 规则 5：内侧 → 波纹板一组 + 其余一组 ----------
def r5_key(b):
    return "%s|内侧-%s层-%s" % (b["_elem"], b["_layer"],
                                "波纹板" if is_wave(b) else "其余")


out = []
for g in cur:
    if "内侧" not in g["label"]:
        out.append(g)
        continue
    if "上层整体" in g["label"]:
        out.append(g)          # 规则4 已定，不再拆
        continue
    buckets = OrderedDict()
    for b in g["bodies"]:
        k = ("波纹板" if is_wave(b) else "其余")
        buckets.setdefault(k, []).append(b)
    for k, items in buckets.items():
        out.append({"label": "%s-%s" % (g["label"], k), "bodies": items})
cur = out
report("规则5", "内侧：波纹板一组，其余一组")

# ---------- 规则 6：下层 → 大平面底板一组 + 其余一组 ----------
out = []
for g in cur:
    if "下层" not in g["label"] or "上层整体" in g["label"]:
        out.append(g)
        continue
    buckets = OrderedDict()
    for b in g["bodies"]:
        k = ("大平面底板" if is_bottom(b) else "其余")
        buckets.setdefault(k, []).append(b)
    for k, items in buckets.items():
        out.append({"label": "%s-%s" % (g["label"], k), "bodies": items})
cur = out
report("规则6", "下层：大平面底板一组，其余一组")

# ---------- 规则 7：外侧 → 波纹板一组 + 其余一组 ----------
out = []
for g in cur:
    if "外侧" not in g["label"] or "上层整体" in g["label"]:
        out.append(g)
        continue
    buckets = OrderedDict()
    for b in g["bodies"]:
        k = ("波纹板" if is_wave(b) else "其余")
        buckets.setdefault(k, []).append(b)
    for k, items in buckets.items():
        out.append({"label": "%s-%s" % (g["label"], k), "bodies": items})
cur = out
final = report("规则7", "外侧：波纹板一组，其余一组")

print("\n" + "=" * 78)
print("最终结果：%d 组 / %d 体" % (len(cur), sum(len(g["bodies"]) for g in cur)))
lines = []
for i, g in enumerate(sorted(cur, key=lambda x: -len(x["bodies"])), 1):
    lines.append("%2d. %-46s %3d 体" % (i, g["label"], len(g["bodies"])))
txt = "\n".join(lines)
print(txt)
open(r"D:\ai\meshkit\reports\七步分组结果.txt", "w", encoding="utf-8").write(txt)
