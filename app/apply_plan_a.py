# -*- coding: utf-8 -*-
"""方案 A：薄板类改扫掠 + 其余按 3×壁厚放宽，然后生成网格"""
import json
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, r"D:\ai\meshkit\app")
import model_memory as M
import mesh_risk


def post(path, payload, timeout=3000):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + path, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def plain(nm):
    for pre in ("SIZE_FIX_", "SIZE_", "METHOD_", "SWEEP_", "SHELL_"):
        if nm.startswith(pre):
            return nm[len(pre):]
    return nm


# 扫掠组（薄板类）：面内尺寸
SWEEP_GROUPS = {"底板": 30.0, "中间隔断板": 30.0, "薄板": 20.0, "垫板": 20.0}
# 其余：3×壁厚，下限 8mm
OTHER_RATIO = 3.0
OTHER_FLOOR = 8.0


def main():
    prof = M.get_profile("集装箱-我的划分")
    bodies = post("/api/op", {"op": "list_bodies", "payload": {}})["bodies"]
    res = M.plan_for(bodies, prof)
    name2b = {b["name"]: b for b in bodies}

    plan, sizes, methods = [], {}, {}
    print(u"%-14s %4s %-8s %7s %8s" % (u"类别", u"体数", u"方式", u"壁厚", u"尺寸"))
    for p in res["plan"]:
        cat = p["category"]
        members = [name2b[n] for n in p["bodies"] if n in name2b]
        walls = sorted(mesh_risk._wall_and_fill(b)[0] for b in members)
        wmin = walls[0]
        if cat in SWEEP_GROUPS:
            size = SWEEP_GROUPS[cat]
            method = "Sweep"
            way = "扫掠"
        else:
            size = max(OTHER_FLOOR, min(40.0, round(OTHER_RATIO * wmin, 1)))
            method = "Automatic"
            way = "四面体"
        sizes[cat] = size
        methods[cat] = method
        plan.append({"category": cat, "size": size, "method": method,
                     "bodies": p["bodies"]})
        print(u"%-14s %4d %-8s %7.2f %8.1f" % (cat, len(members), way, wmin, size))

    print(u"\n== 建控制项 + 套尺寸/方法 ==")
    r = post("/api/op", {"op": "controls_from_plan",
                         "payload": {"plan": plan}}, timeout=1800)
    print(u"  新建/更新 %d，错误 %d" % (len(r.get("created") or []),
                                     len(r.get("errors") or [])))
    for e in (r.get("errors") or [])[:6]:
        print(u"   ", e)

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
    main()
