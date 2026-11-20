# -*- coding: utf-8 -*-
"""把失败体单独成组（异形纵梁），并从 上梁 组移出；然后用去特征 2mm 重划"""
import json
import sys
import time
import urllib.request

TARGET = "3_上_纵梁_1752x105x185_01"
OLD_GROUP = "上梁"
NEW_GROUP = "异形纵梁"
NEW_SIZE = 4.0          # 该体薄壁约 2.2mm，单独给细一点
OLD_SIZE = 8.0


def post(path, payload, timeout=3000):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + path, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main(do_mesh=True):
    def plain(nm):
        for pre in ("SIZE_FIX_", "SIZE_", "METHOD_", "SWEEP_", "SHELL_"):
            if nm.startswith(pre):
                return nm[len(pre):]
        return nm

    plan = post("/api/op", {"op": "read_plan", "payload": {}})
    groups, ctrl_names = {}, {}
    for c in plan["controls"]:
        nm = c["name"]
        if nm.startswith("METHOD_"):
            continue
        key = plain(nm)
        groups.setdefault(key, [])
        groups[key].extend(c.get("bodies") or [])
        ctrl_names[key] = nm
    if OLD_GROUP not in groups:
        raise SystemExit("找不到组 %s" % OLD_GROUP)
    old_members = groups[OLD_GROUP]
    if TARGET not in old_members:
        print("注意：%s 不在 %s 组里，仍然新建单独组" % (TARGET, OLD_GROUP))
    remain = [n for n in old_members if n != TARGET]
    print("原 %s 组 %d 体 -> 保留 %d 体；单出 1 体 -> %s" % (
        OLD_GROUP, len(old_members), len(remain), NEW_GROUP))

    # 1) 删掉旧组，重建（去掉目标体）+ 新建单独组
    r = post("/api/op", {"op": "delete_controls",
                         "payload": {"names": [ctrl_names.get(OLD_GROUP, OLD_GROUP)]}})
    print("删除旧组:", r.get("removed"), " 失败:", r.get("failed"))

    build = [
        {"category": OLD_GROUP, "size": OLD_SIZE, "method": "Automatic",
         "bodies": remain},
        {"category": NEW_GROUP, "size": NEW_SIZE, "method": "Automatic",
         "bodies": [TARGET]},
    ]
    r2 = post("/api/op", {"op": "controls_from_plan", "payload": {"plan": build}})
    print("新建:", r2.get("created"), " 错误:", (r2.get("errors") or [])[:3])

    # 2) 全局去特征 1 -> 2mm
    print("\n== 全局去特征 1 -> 2.5mm ==")
    a = post("/api/op", {"op": "apply", "payload": {
        "settings": {"ElementSize": 40, "MinimumSize": 2,
                     "DefeatureTolerance": 2.5, "CurvatureNormalAngle": 18}}})
    print("  设置:", a.get("settings"), " 错误:", (a.get("errors") or [])[:2])

    # 3) 单独给该组套尺寸
    a2 = post("/api/op", {"op": "apply", "payload": {
        "sizes": {NEW_GROUP: NEW_SIZE, OLD_GROUP: OLD_SIZE}}})
    print("  尺寸:", a2.get("sizes"), " 错误:", (a2.get("errors") or [])[:2])

    if not do_mesh:
        return
    print("\n== 生成网格 ==")
    t0 = time.time()
    m = post("/api/op", {"op": "mesh", "payload": {"timeout": 3600}}, timeout=4000)
    print("  用时 %.1f 分钟" % ((time.time() - t0) / 60.0))
    print("  单元=%s 节点=%s 状态=%s" % (m.get("elements"), m.get("nodes"),
                                        m.get("mesh_state")))
    fb = m.get("failed_bodies") or []
    print("  失败体 %d 个" % len(fb))
    for b in fb[:10]:
        print("     ", b.get("name"))


if __name__ == "__main__":
    main("--nomesh" not in sys.argv)
