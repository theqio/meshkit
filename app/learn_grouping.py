# -*- coding: utf-8 -*-
"""
learn_grouping.py —— 从用户的实际分组里"学出"分类逻辑

用户描述的逻辑（三级）：
  1) 大小形状相同 -> 一组        （尺寸完全一致、实心/空心一致）
  2) 相同类别（如壳单元波纹板外壁） -> 一组   （同材质/同构成形式）
  3) 结构作用相同（如顶部支撑梁、上下框架） -> 一组

做法：对每个体提取 (语义类型, 相对高度, 朝向, 壳体, 薄板, 尺寸簇)，
统计用户的每一组在这些维度上的取值，归纳成可执行的规则，
再用同一模型回测，验证规则能否 100% 复现用户的分组。
"""
import collections
import io
import json
import sys
import urllib.request

sys.path.insert(0, r"D:\ai\meshkit\app")


def post(path, payload, timeout=300):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + path, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def feats(b, ymin, ymax):
    dims = sorted([b["lx"], b["ly"], b["lz"]])
    d1, d2, d3 = dims
    bbox = d1 * d2 * d3 or 1.0
    fill = b["vol"] / bbox if bbox else 0.0
    z = (b["cy"] - ymin) / ((ymax - ymin) or 1.0)
    # 语义类型：取体名前缀（去掉编号/尺寸段）
    base = b["category"]
    # 朝向：最长边对应的轴
    axes = [("X", b["lx"]), ("Y", b["ly"]), ("Z", b["lz"])]
    axes.sort(key=lambda t: -t[1])
    return {
        "name": b["name"], "base": base, "shell": b["shell"],
        "zone": "下" if z < 0.34 else ("中" if z < 0.62 else "上"),
        "z": round(z, 3),
        "r1": round(d1 / d3, 4), "r2": round(d2 / d3, 4),
        "fill": round(fill, 4),
        "dims": tuple(round(x, 1) for x in dims),
        "orient": axes[0][0],
        "hollow": fill < 0.5,
        "thin": (d1 / d3) < 0.03,
    }


def main():
    plan = post("/api/op", {"op": "read_plan", "payload": {}})
    bodies = post("/api/op", {"op": "list_bodies", "payload": {}})["bodies"]
    label = {}
    for c in plan["controls"]:
        for n in (c.get("bodies") or []):
            label[n] = c["name"]

    ys = [b["cy"] for b in bodies] or [0]
    ymin, ymax = min(ys), max(ys)
    F = {b["name"]: feats(b, ymin, ymax) for b in bodies}

    # ---------- 逐组归纳 ----------
    groups = collections.defaultdict(list)
    for nm, g in label.items():
        groups[g].append(nm)

    print(u"共 %d 组 / %d 体\n" % (len(groups), len(label)))
    print(u"%-12s %4s %-26s %-14s %-7s %s" % (
        u"组名", u"体数", u"尺寸簇", u"语义类型", u"位置", u"特征"))
    rules = []
    for g, names in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        fs = [F[n] for n in names if n in F]
        if not fs:
            continue
        size_clusters = collections.Counter(f_["dims"] for f_ in fs)
        bases = collections.Counter(f_["base"] for f_ in fs)
        zones = collections.Counter(f_["zone"] for f_ in fs)
        shells = collections.Counter(f_["shell"] for f_ in fs)
        orient = collections.Counter(f_["orient"] for f_ in fs)
        hollow = collections.Counter(f_["hollow"] for f_ in fs)
        r1 = round(sum(f_["r1"] for f_ in fs) / len(fs), 3)
        r2 = round(sum(f_["r2"] for f_ in fs) / len(fs), 3)
        ncl = len(size_clusters)
        size_desc = (u"单一尺寸簇" if ncl == 1 else u"%d 个尺寸簇" % ncl)
        base_desc = u"/".join(b for b, _ in bases.most_common(2))
        zone_desc = u"/".join("%s×%d" % (z, c) for z, c in zones.most_common(2))
        print(u"%-12s %4d %-26s %-14s %-7s r1=%.3f r2=%.3f 壳=%s 空=%s" % (
            g[:12], len(fs), size_desc, base_desc[:14], zone_desc,
            r1, r2, "Y" if shells[True] else "N",
            "Y" if hollow[True] else "N"))
        # 归纳成规则条目
        rules.append({
            "group": g,
            "size_clusters": [[list(map(float, k)), v] for k, v in size_clusters.items()],
            "bases": dict(bases), "zones": dict(zones),
            "shell": shells[True] > shells[False],
            "orient": dict(orient), "hollow": hollow[True] > hollow[False],
            "r1": r1, "r2": r2, "count": len(fs),
        })

    # ---------- 回测：用规则重新分组 ----------
    # 规则优先级：尺寸簇 -> 语义类型+位置 -> 语义类型
    by_size = {}
    for r in rules:
        for dims, cnt in r["size_clusters"]:
            key = tuple(round(x, 1) for x in dims)
            by_size.setdefault(key, r["group"])
    by_base_zone = {}
    by_base = {}
    for r in rules:
        for b, cnt in (r["bases"] or {}).items():
            by_base.setdefault(b, []).append((cnt, r["group"]))
            for z, cntz in (r["zones"] or {}).items():
                by_base_zone.setdefault((b, z), []).append((cnt * cntz, r["group"]))
    for b in list(by_base):
        by_base[b] = max(by_base[b])[1]
    for k in list(by_base_zone):
        by_base_zone[k] = max(by_base_zone[k])[1]

    correct = wrong = 0
    errors = []
    for nm, f in F.items():
        want = label.get(nm)
        got = by_size.get(f["dims"])
        if got is None:
            got = by_base_zone.get((f["base"], f["zone"]))
        if got is None:
            got = by_base.get(f["base"])
        if got == want:
            correct += 1
        else:
            wrong += 1
            if len(errors) < 10:
                errors.append((nm, want, got))

    print(u"\n回测：正确 %d / %d（%.1f%%)" % (correct, correct + wrong,
          100.0 * correct / max(1, correct + wrong)))
    if errors:
        print(u"错判示例：")
        for nm, want, got in errors:
            print(u"   %-40s 应为 %-14s 判为 %s" % (nm[:40], want, got))

    out = {"rules": rules, "accuracy": round(100.0 * correct / max(1, correct + wrong), 2)}
    io.open(r"D:\ai\meshkit\reports\分组规则.json", "w", encoding="utf-8").write(
        json.dumps(out, ensure_ascii=False, indent=1))
    print(u"\n规则已写入 reports\\分组规则.json")


if __name__ == "__main__":
    main()
