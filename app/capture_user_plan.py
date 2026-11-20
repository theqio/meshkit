# -*- coding: utf-8 -*-
"""
capture_user_plan.py —— 把用户在 Mechanical 里的实际划分存成模型记忆

规则（按用户要求）：
  1. 类别 = 用户选的体集合（不按尺寸，不按控制项名字）
  2. 类别名 = 该组里体最多的那个语义类别（重名时加序号）
  3. 垫板：用户没建控制项，但确认分类正确 -> 单独作为一类补进去
  4. 其余漏选的散体：按语义类别各自成组，并在报告里列出来
"""
import collections
import io
import json
import sys
import urllib.request

sys.path.insert(0, r"D:\ai\meshkit\app")
import model_memory


def post(path, payload, timeout=300):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + path, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def is_my_control(name):
    """排除程序自己建的 SIZE_/METHOD_/SWEEP_/SHELL_ 控制项"""
    n = name or ""
    for p in ("SIZE_FIX_", "SIZE_", "METHOD_", "SWEEP_", "SHELL_"):
        if n.startswith(p):
            return False
    return True


def main():
    profile_name = sys.argv[1] if len(sys.argv) > 1 else "集装箱-我的划分"

    plan = post("/api/op", {"op": "read_plan", "payload": {}})
    bodies = post("/api/op", {"op": "list_bodies", "payload": {}})["bodies"]
    cat_of = {b["name"]: b["category"] for b in bodies}
    all_names = [b["name"] for b in bodies]

    covered = set()
    groups = []          # [{name, bodies:[...], size}]  "用户选出来的组"
    for c in sorted(plan.get("controls") or [], key=lambda d: -d["count"]):
        if not is_my_control(c.get("name")):
            continue
        members = [n for n in (c.get("bodies") or [])]
        if not members:
            continue
        covered.update(members)
        dist = collections.Counter(cat_of.get(n, "?") for n in members)
        groups.append({"name": (c.get("name") or "").strip(),
                       "base": dist.most_common(1)[0][0] if dist else u"组",
                       "bodies": members, "size": c.get("size_mm"),
                       "src": c.get("name"), "dist": dist})

    # 未覆盖的体：垫板单独一类，其余按语义类别各自成组（用户要求）
    uncovered = [n for n in all_names if n not in covered]
    rest_by_cat = collections.defaultdict(list)
    for n in uncovered:
        rest_by_cat[cat_of.get(n, "未分类")].append(n)
    for cat, names in rest_by_cat.items():
        groups.append({"name": cat, "base": cat, "bodies": names, "size": None,
                       "src": u"未覆盖补入", "dist": collections.Counter({cat: len(names)})})

    # 名字去重
    seen_names = collections.Counter()
    for g in groups:
        nm = g["name"] or g["base"]
        seen_names[nm] += 1
        if seen_names[nm] > 1:
            g["name"] = u"%s_%d" % (nm, seen_names[nm])
        else:
            g["name"] = nm

    # 构建记忆（只用几何指纹，不用尺寸）
    ys = [float(b.get("cy") or 0) for b in bodies] or [0.0]
    ymin, ymax = min(ys), max(ys)
    feats = {}
    for b in bodies:
        f = model_memory.body_features(b, ymin, ymax)
        if f:
            feats[b["name"]] = f

    cats = []
    for g in groups:
        samples, seen = [], set()
        for nm in g["bodies"]:
            f = feats.get(nm)
            if not f:
                continue
            key = model_memory._round_sig(f)
            if key in seen:
                continue
            seen.add(key)
            samples.append({"r1": round(f["r1"], 5), "r2": round(f["r2"], 5),
                            "fill": round(f["fill"], 4), "shell": f["shell"],
                            "z": round(f["z"], 4)})
            if len(samples) >= model_memory.MAX_SAMPLES:
                break
        cats.append({"name": g["name"], "size": g["size"], "method": "Automatic",
                     "count": len(g["bodies"]), "samples": samples,
                     "source_control": g["src"],
                     "mixed": [k for k, v in g["dist"].most_common() if v >= 2][1:]})

    profile = {"name": profile_name,
               "created": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
               "body_count": len(bodies),
               "y_range": [round(ymin, 1), round(ymax, 1)],
               "source": "user_plan",
               "size_ignored": True,
               "categories": cats}
    model_memory.save_profile(profile)

    print(u"已保存记忆：%s（%d 类 / %d 体）" % (profile_name, len(cats), len(bodies)))
    print()
    print(u"%-14s %5s %-22s %s" % (u"类别名", u"体数", u"成员类别分布", u"控制项"))
    for g in groups:
        dist = u", ".join(u"%s×%d" % (k, v) for k, v in g["dist"].most_common(3))
        print(u"%-14s %5d %-22s %s" % (g["name"], len(g["bodies"]), dist, g["src"]))
    print()
    tot = sum(g["bodies"].__len__() for g in groups)
    print(u"覆盖体数合计：%d / %d" % (tot, len(bodies)))


if __name__ == "__main__":
    main()
