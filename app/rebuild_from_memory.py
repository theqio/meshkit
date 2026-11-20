# -*- coding: utf-8 -*-
"""从头做一遍：按记忆重建用户分组 + 按风险建议给尺寸 + 生成网格"""
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


def main():
    prof = M.get_profile("集装箱-我的划分")
    if not prof:
        raise SystemExit("找不到记忆")

    bodies = post("/api/op", {"op": "list_bodies", "payload": {}})["bodies"]
    res = M.plan_for(bodies, prof)
    print(u"记忆识别：匹配 %d/%d" % (res["matched_bodies"], res["total_bodies"]))

    name2body = {b["name"]: b for b in bodies}
    plan, sizes = [], {}
    for p in res["plan"]:
        cat = p["category"]
        members = [name2body[n] for n in p["bodies"] if n in name2body]
        # 该组的几何：壁厚 / 最薄边
        walls, mins = [], []
        for b in members:
            w, fill, hollow = mesh_risk._wall_and_fill(b)
            dims = sorted([b["lx"], b["ly"], b["lz"]])
            walls.append(w)
            mins.append(dims[0])
        wall = statistics.median(walls)
        dmin = statistics.median(mins)
        # 薄壁空心：2.5×壁厚；实心/薄板：2.5×最薄边；下限 6mm 防止单元爆炸
        if wall < dmin * 0.8:
            size = 2.5 * wall
            basis = u"2.5×壁厚(%.1f)" % wall
        else:
            size = 2.5 * dmin
            basis = u"2.5×最薄边(%.1f)" % dmin
        size = max(6.0, min(40.0, round(size, 1)))
        sizes[cat] = size
        plan.append({"category": cat, "size": size, "method": "Automatic",
                     "bodies": p["bodies"]})
        print(u"  %-12s %3d 体  壁厚≈%5.1f 最薄≈%6.1f -> %.1f mm  (%s)" % (
            cat, len(p["bodies"]), wall, dmin, size, basis))

    print(u"\n建控制项 ...")
    r = post("/api/op", {"op": "controls_from_plan",
                         "payload": {"plan": plan}}, timeout=1200)
    print(u"  新建/更新 %d 个；错误 %d" % (len(r.get("created") or []),
                                        len(r.get("errors") or [])))
    for e in (r.get("errors") or [])[:5]:
        print(u"   ", e)

    print(u"\n应用全局设置（最小尺寸 2mm，去特征 1mm，全局 40mm）...")
    st = post("/api/op", {"op": "apply", "payload": {
        "settings": {"ElementSize": 40, "MinimumSize": 2,
                     "DefeatureTolerance": 1, "CurvatureNormalAngle": 18}}})
    print(u"  设置:", st.get("settings"), u" 错误:", (st.get("errors") or [])[:2])

    print(u"\n生成网格（约 3 分钟）...")
    t0 = time.time()
    m = post("/api/op", {"op": "mesh", "payload": {"timeout": 2400}}, timeout=2700)
    print(u"  用时 %.1f 分钟" % ((time.time() - t0) / 60.0))
    print(u"  单元=%s 节点=%s 状态=%s" % (m.get("elements"), m.get("nodes"),
                                        m.get("mesh_state")))
    fb = m.get("failed_bodies") or []
    print(u"  失败体 %d 个" % len(fb))
    for b in fb[:12]:
        print(u"     ", b.get("name"), b.get("state"))


if __name__ == "__main__":
    main()
