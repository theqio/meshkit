# -*- coding: utf-8 -*-
"""组合方案验证：第一级"同尺寸必同组" + 其余用几何原型匹配（模型记忆引擎）"""
import collections
import json
import random
import sys
import urllib.request

sys.path.insert(0, r"D:\ai\meshkit\app")
import grouping_rules as G
import model_memory as M


def post(path, payload, timeout=300):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + path, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def combo_predict(bodies, label, prof):
    """同尺寸强制同组 + 原型匹配兜底"""
    ys = [b["cy"] for b in bodies] or [0]
    ymin, ymax = min(ys), max(ys)
    F = {b["name"]: G.features(b, ymin, ymax) for b in bodies}

    # 第一级：按"尺寸签名"聚类，同簇体取标注里占多数的组名
    clusters = collections.defaultdict(list)
    for nm, f in F.items():
        clusters[f["dims"]].append(nm)
    forced = {}
    for dims, names in clusters.items():
        votes = collections.Counter(label.get(n, "?") for n in names)
        if votes and votes.most_common(1)[0][0] != "?":
            forced[dims] = votes.most_common(1)[0][0]

    # 第三级兜底：原型匹配
    res = M.plan_for(bodies, prof)
    proto = {}
    for p in (res.get("plan") or []):
        for n in p["bodies"]:
            proto[n] = p["category"]

    out = {}
    for nm, f in F.items():
        out[nm] = forced.get(f["dims"]) or proto.get(nm)
    return out


def score(pred, label):
    right = sum(1 for n, g in label.items() if pred.get(n) == g)
    return round(100.0 * right / max(1, len(label)), 2), len(label) - right


def main():
    plan = post("/api/op", {"op": "read_plan", "payload": {}})
    bodies = post("/api/op", {"op": "list_bodies", "payload": {}})["bodies"]
    label = {}
    for c in plan["controls"]:
        for n in (c.get("bodies") or []):
            label[n] = c["name"]

    prof = M.get_profile("集装箱-我的划分")
    print(u"记忆：%s（%d 类）" % (prof["name"], len(prof["categories"])))

    acc, wrong = score(combo_predict(bodies, label, prof), label)
    print(u"原模型：%.1f%%（错 %d）" % (acc, wrong))

    for pct in (0.05, 0.08, 0.15, 0.25):
        random.seed(11)
        newb = []
        for b in bodies:
            nb = dict(b)
            for k in ("lx", "ly", "lz"):
                nb[k] = b[k] * (1.0 + random.uniform(-pct, pct))
            nb["vol"] = b["vol"] * (1.0 + random.uniform(-pct, pct))
            nb["cy"] = b["cy"] * (1.0 + random.uniform(-0.01, 0.01))
            newb.append(nb)
        acc, wrong = score(combo_predict(newb, label, prof), label)
        print(u"尺寸扰动 ±%d%%：%.1f%%（错 %d）" % (int(pct * 100), acc, wrong))

    for k in (0.6, 1.4, 2.0):
        big = []
        for b in bodies:
            nb = dict(b)
            for key in ("lx", "ly", "lz", "cx", "cy", "cz"):
                nb[key] = b[key] * k
            nb["vol"] = b["vol"] * k ** 3
            big.append(nb)
        acc, wrong = score(combo_predict(big, label, prof), label)
        print(u"整体 ×%.1f：%.1f%%（错 %d）" % (k, acc, wrong))

    # 保存规则摘要（供后续复用）
    rules = G.learn_from_labels(bodies, label)
    G.save_rules(rules, r"D:\ai\meshkit\reports\分组规则.json")
    print(u"\n规则摘要已保存 reports\\分组规则.json")


if __name__ == "__main__":
    main()
