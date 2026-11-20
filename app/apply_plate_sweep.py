# -*- coding: utf-8 -*-
"""薄板类自动 Sweep + 2 层：识别 -> 设方法/层数 -> 重划"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, r"D:\ai\meshkit\app")
import budget_plan as BP
import model_memory as M
import mesh_risk


def post(path, payload, timeout=3000):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + path, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main(do_mesh=True):
    prof = M.get_profile("集装箱-我的划分")
    bodies = post("/api/op", {"op": "list_bodies", "payload": {}})["bodies"]
    res = M.plan_for(bodies, prof)
    name2b = {b["name"]: b for b in bodies}

    plan = []
    print(u"%-14s %4s %-10s %7s %8s %s" % (u"类别", u"体数", u"识别", u"厚度", u"尺寸", u"方法"))
    for p in res["plan"]:
        cat = p["category"]
        members = [name2b[n] for n in p["bodies"] if n in name2b]
        kinds = {}
        for b in members:
            k, dims = BP.classify(b)
            kinds[k] = kinds.get(k, 0) + 1
        kind = max(kinds.items(), key=lambda kv: kv[1])[0]
        dims_all = [sorted([b["lx"], b["ly"], b["lz"]]) for b in members]
        thick = sorted(d[0] for d in dims_all)[len(dims_all) // 2]

        if kind == "plate":
            size = 30.0 if thick >= 4 else 20.0
            method, div, way = "Sweep", 2, u"薄板"
        elif kind == "slender":
            walls = sorted(mesh_risk._wall_and_fill(b)[0] for b in members)
            size = max(8.0, min(40.0, round(3.0 * walls[0], 1)))
            method, div, way = "Automatic", None, u"细长(四面体)"
        else:
            walls = sorted(mesh_risk._wall_and_fill(b)[0] for b in members)
            size = max(8.0, min(40.0, round(3.0 * walls[0], 1)))
            method, div, way = "Automatic", None, u"块体(四面体)"

        item = {"category": cat, "size": size, "method": method,
                "bodies": p["bodies"]}
        if div:
            item["divisions"] = div
        plan.append(item)
        print(u"%-14s %4d %-10s %7.1f %8.1f %s%s" % (
            cat, len(members), way, thick, size, method,
            (u" + %d 层" % div) if div else ""))

    print(u"\n== 套用方法/尺寸 ==")
    r = post("/api/op", {"op": "controls_from_plan",
                         "payload": {"plan": plan}}, timeout=1800)
    print(u"  控制项 %d 个，方法 %d 个，错误 %d" % (
        len(r.get("created") or []), len(r.get("methods") or []),
        len(r.get("errors") or [])))
    for e in (r.get("errors") or [])[:6]:
        print(u"   ", e)

    if not do_mesh:
        return
    print(u"\n== 生成网格 ==")
    t0 = time.time()
    m = post("/api/op", {"op": "mesh", "payload": {"timeout": 3600}}, timeout=4000)
    print(u"  用时 %.1f 分钟" % ((time.time() - t0) / 60.0))
    print(u"  单元=%s 节点=%s 状态=%s" % (m.get("elements"), m.get("nodes"),
                                        m.get("mesh_state")))
    fb = m.get("failed_bodies") or []
    print(u"  失败体 %d 个" % len(fb))
    for b in fb[:10]:
        print(u"     ", b.get("name"))


if __name__ == "__main__":
    main("--nomesh" not in sys.argv)
