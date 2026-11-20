# -*- coding: utf-8 -*-
"""把所有细长薄壁组改为 Sweep + Automatic Thin，然后验证网格结果"""
import json
import time
import urllib.request

# 需要改的组（薄壁空心件；壳体组与薄板组不在此列）
TARGETS = ["底部横梁", "窗框架", "两侧隔断支撑架", "顶部支撑梁", "侧封板",
           "上梁", "侧支撑板", "上下框架", "大三角板", "连接结构"]


def post(path, payload, timeout=3000):
    req = urllib.request.Request("http://127.0.0.1:8765" + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def main(do_mesh=True):
    print("== 1. 设为 Sweep 方法 ==")
    methods = {c: "Sweep" for c in TARGETS}
    r = post("/api/op", {"op": "apply", "payload": {"methods": methods}})
    mm = r.get("methods") or {}
    print("  新建:", mm.get("created"))
    print("  更新:", mm.get("updated"))
    print("  错误:", (mm.get("errors") or [])[:4])

    print("\n== 2. 逐个设为 Automatic Thin（读回标签验证）==")
    ok, fail = [], []
    for c in TARGETS:
        res = post("/api/op", {"op": "set_by_label", "payload": {
            "control": "METHOD_" + c, "prop": "SourceTargetSelection",
            "label": "Automatic Thin", "max": 8}})
        if res.get("ok"):
            ok.append(u"%s=%s" % (c, res.get("label_now")))
        else:
            fail.append(u"%s: %s" % (c, res.get("error")))
    print("  成功 %d 个:" % len(ok))
    for x in ok:
        print("     ", x)
    if fail:
        print("  失败 %d 个:" % len(fail))
        for x in fail:
            print("     ", x)

    if not do_mesh:
        return
    print("\n== 3. 网格前检查 ==")
    pc = post("/api/op", {"op": "pre_mesh_check", "payload": {"fix": True}})
    print("  ok:", pc.get("ok"), " 修正:", pc.get("fixes"), " 错误:", pc.get("errors"))

    print("\n== 4. 生成网格 ==")
    t0 = time.time()
    m = post("/api/op", {"op": "mesh", "payload": {"timeout": 2400}}, timeout=2700)
    print("  用时 %.1f 分钟" % ((time.time() - t0) / 60.0))
    print("  单元=%s 节点=%s 状态=%s 失败=%d" % (
        m.get("elements"), m.get("nodes"), m.get("mesh_state"),
        len(m.get("failed_bodies") or [])))
    for b in (m.get("failed_bodies") or [])[:12]:
        print("     ", b.get("name"))


if __name__ == "__main__":
    main("--nomesh" not in __import__("sys").argv)
