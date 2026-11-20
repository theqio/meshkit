# -*- coding: utf-8 -*-
"""按失败体定位到组：失败的组退回 Automatic，成功的组保留 Sweep+Automatic Thin，再划一次"""
import json
import sys
import time
import urllib.request
from collections import Counter


def post(path, payload, timeout=3000):
    req = urllib.request.Request("http://127.0.0.1:8765" + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def plain(nm):
    for pre in ("SIZE_FIX_", "SIZE_", "METHOD_", "SWEEP_", "SHELL_"):
        if nm.startswith(pre):
            return nm[len(pre):]
    return nm


def main(do_mesh=True):
    plan = post("/api/op", {"op": "read_plan", "payload": {}})
    group_of = {}
    for c in plan["controls"]:
        if c["name"].startswith("METHOD_"):
            continue
        g = plain(c["name"])
        for b in (c.get("bodies") or []):
            group_of[b] = g

    st = post("/api/op", {"op": "status", "payload": {}})
    failed = [b.get("name") for b in (st.get("failed_bodies") or [])]
    print("当前失败 %d 体" % len(failed))

    bad_groups = Counter(group_of.get(n, "未分组") for n in failed)
    print("\n失败体所属组：")
    for g, n in bad_groups.most_common():
        print("   %-14s %d 个" % (g, n))

    sweep_groups = []
    for c in plan["controls"]:
        if c["name"].startswith("METHOD_") and c.get("method") == "Sweep":
            sweep_groups.append(plain(c["name"]))
    print("\n当前用 Sweep 的组:", sweep_groups)

    revert = [g for g in sweep_groups if g in bad_groups]
    keep = [g for g in sweep_groups if g not in bad_groups]
    print("\n保留 Sweep:", keep)
    print("退回自动:", revert)

    if revert:
        r = post("/api/op", {"op": "apply",
                             "payload": {"methods": {g: "Automatic" for g in revert}}})
        mm = r.get("methods") or {}
        print("\n退回结果 deleted:", mm.get("deleted"), " 错误:", (mm.get("errors") or [])[:3])

    if not do_mesh:
        return
    print("\n== 重新划分 ==")
    t0 = time.time()
    m = post("/api/op", {"op": "mesh", "payload": {"timeout": 2400}}, timeout=2700)
    print("  用时 %.1f 分钟" % ((time.time() - t0) / 60.0))
    print("  单元=%s 节点=%s 状态=%s 失败=%d" % (
        m.get("elements"), m.get("nodes"), m.get("mesh_state"),
        len(m.get("failed_bodies") or [])))
    for b in (m.get("failed_bodies") or [])[:10]:
        print("     ", b.get("name"))


if __name__ == "__main__":
    main("--nomesh" not in sys.argv)
