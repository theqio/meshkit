# -*- coding: utf-8 -*-
"""用你的真实分组训练规则，并测试它在"尺寸变化的新模型"上还能不能分对"""
import json
import random
import sys
import urllib.request

sys.path.insert(0, r"D:\ai\meshkit\app")
import grouping_rules as G


def post(path, payload, timeout=300):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + path, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    plan = post("/api/op", {"op": "read_plan", "payload": {}})
    bodies = post("/api/op", {"op": "list_bodies", "payload": {}})["bodies"]
    label = {}
    for c in plan["controls"]:
        for n in (c.get("bodies") or []):
            label[n] = c["name"]

    rules = G.learn_from_labels(bodies, label)
    r0 = G.evaluate(bodies, label, rules)
    print(u"原模型回测：%d 正确 / %d 错  准确率 %.1f%%" % (r0["right"], r0["wrong"], r0["accuracy"]))

    # ---- 模拟"新模型"：尺寸随机 ±8%，轻微位移 ----
    for pct in (0.05, 0.08, 0.15):
        random.seed(11)
        newb = []
        for b in bodies:
            nb = dict(b)
            for k in ("lx", "ly", "lz"):
                nb[k] = b[k] * (1.0 + random.uniform(-pct, pct))
            nb["vol"] = b["vol"] * (1.0 + random.uniform(-pct, pct))
            nb["cy"] = b["cy"] * (1.0 + random.uniform(-0.01, 0.01))
            newb.append(nb)
        r = G.evaluate(newb, label, rules)
        print(u"尺寸扰动 ±%d%%：准确率 %.1f%%（错 %d 体）" % (
            int(pct * 100), r["accuracy"], r["wrong"]))

    # ---- 整体放大 1.4 倍 ----
    big = []
    for b in bodies:
        nb = dict(b)
        for k in ("lx", "ly", "lz", "cx", "cy", "cz"):
            nb[k] = b[k] * 1.4
        nb["vol"] = b["vol"] * 1.4 ** 3
        big.append(nb)
    rb = G.evaluate(big, label, rules)
    print(u"整体 ×1.4：准确率 %.1f%%（错 %d 体）" % (rb["accuracy"], rb["wrong"]))

    # ---- 打印学到的规则摘要 ----
    print(u"\n学到的规则（%d 组）：" % len(rules["groups"]))
    print(u"%-14s %-8s %-6s %-22s %s" % (u"组", u"角色", u"壳体", u"区域", u"尺寸簇数"))
    for g, v in sorted(rules["groups"].items(), key=lambda kv: -kv[1]["count"]):
        zones = u"/".join("%s×%d" % (z, c) for z, c in
                         sorted((v.get("zones") or {}).items(), key=lambda t: -t[1]))
        print(u"%-14s %-8s %-6s %-22s %d" % (
            g[:14], v["role"], u"是" if v["shell"] else u"否",
            zones, len(v["sizes"])))

    G.save_rules(rules, r"D:\ai\meshkit\reports\分组规则.json")
    print(u"\n规则已保存 reports\\分组规则.json")


if __name__ == "__main__":
    main()
