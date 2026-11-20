# -*- coding: utf-8 -*-
"""用实测数据标定估算系数，并为不同预算给出尺寸方案"""
import json
import sys
import urllib.request

sys.path.insert(0, r"D:\ai\meshkit\app")
import budget_sizing as B
import model_memory as M


def post(path, payload, timeout=300):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + path, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    prof = M.get_profile("集装箱-我的划分")
    bodies = post("/api/op", {"op": "list_bodies", "payload": {}})["bodies"]
    res = M.plan_for(bodies, prof)
    name2b = {b["name"]: b for b in bodies}
    by_group = {}
    for p in res["plan"]:
        by_group[p["category"]] = [name2b[n] for n in p["bodies"] if n in name2b]

    # 当前实际尺寸（刚划完那套）与实测单元数
    cur = post("/api/op", {"op": "read_plan", "payload": {}})
    def _plain(nm):
        for pre in ("SIZE_FIX_", "SIZE_", "METHOD_", "SWEEP_", "SHELL_"):
            if nm.startswith(pre):
                return nm[len(pre):]
        return nm
    cur_sizes = {}
    for c in cur["controls"]:
        if c.get("size_mm"):
            cur_sizes[_plain(c["name"])] = c["size_mm"]
    actual = 3443976.0
    k = B.calibrate(by_group, cur_sizes, actual)
    print(u"标定：估算系数 K = %.4f（当前尺寸下估 %s 单元，实测 %s）" % (
        k, "{:,.0f}".format(B.estimate(cur_sizes, by_group, k)), "{:,.0f}".format(actual)))

    for target in (800_000, 1_200_000, 1_500_000, 2_500_000):
        r = B.plan(res["plan"], by_group, target, k)
        flag = u"可达" if r["reached"] else u"不可达"
        print(u"\n目标 %s 单元 -> %s，规划 %s 单元，放大系数 %.2f" % (
            "{:,.0f}".format(target), flag,
            "{:,.0f}".format(r["planned_elements"]), r["scale"]))
        print(u"  最小可行单元数（全部按下限）= %s" % "{:,.0f}".format(r["min_feasible_elements"]))
        if target == 1_200_000 or not r["reached"]:
            print(u"  耗单元最多的组：")
            for g in r["groups"][:6]:
                print(u"    %-12s %3d 体 尺寸=%5.1f 下限=%4.1f 单元=%9s 占比%5.1f%%" % (
                    g["group"], g["bodies"], g["size"], g["floor"],
                    "{:,.0f}".format(g["elements"]), g["share"]))


if __name__ == "__main__":
    main()
