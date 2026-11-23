# -*- coding: utf-8 -*-
"""分组快照 / 对比

用法：
    python app/snapshot_groups.py 自动            # 存一份当前分组
    python app/snapshot_groups.py 手动            # 手动重分组后再存
    python app/compare_groups.py                  # 对比最近两份

快照存到 reports/分组快照_<标签>_<时间>.json
"""
import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
APP = os.path.dirname(os.path.abspath(__file__))
REP = os.path.join(os.path.dirname(APP), "reports")


def get(p, timeout=180):
    with urllib.request.urlopen(BASE + p, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post(p, payload=None, timeout=600):
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + p, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def op(name, payload=None):
    return post("/api/op", {"op": name, "payload": payload or {}})


def snapshot(tag):
    agents = get("/api/agents")
    pid = agents.get("target")
    proj = None
    for a in agents.get("agents") or []:
        if a.get("pid") == pid:
            proj = a.get("project")

    lb = op("list_bodies")
    bodies = lb.get("bodies") or []
    bmap = {b.get("name"): b for b in bodies}

    gg = op("geom_groups")
    groups = {}
    for g in gg.get("groups") or []:
        items = []
        for n in g.get("bodies") or []:
            b = bmap.get(n) or {}
            d = sorted([round(b.get("lx") or 0), round(b.get("ly") or 0),
                        round(b.get("lz") or 0)])
            items.append({"name": n, "id": b.get("id"), "dims": d})
        groups[g.get("name")] = items
    un = []
    for n in gg.get("ungrouped") or []:
        b = bmap.get(n) or {}
        d = sorted([round(b.get("lx") or 0), round(b.get("ly") or 0),
                    round(b.get("lz") or 0)])
        un.append({"name": n, "id": b.get("id"), "dims": d})
    if un:
        groups["未分组"] = un

    snap = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "tag": tag,
            "pid": pid, "project": proj, "total": len(bodies),
            "group_count": len(groups),
            "groups": groups}
    os.makedirs(REP, exist_ok=True)
    fn = os.path.join(REP, "分组快照_%s_%s.json"
                      % (tag, time.strftime("%Y%m%d_%H%M%S")))
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)

    print("已保存: %s" % os.path.basename(fn))
    print("  工程=%s  pid=%s  体数=%s  分组数=%s"
          % (proj, pid, len(bodies), len(groups)))
    for name, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print("    %-16s %3d 体" % (name, len(items)))
    return fn


def latest(tag):
    fs = [f for f in os.listdir(REP) if f.startswith("分组快照_%s_" % tag)]
    if not fs:
        return None
    fs.sort()
    return os.path.join(REP, fs[-1])


def compare(a_tag="自动", b_tag="手动", path_a=None, path_b=None):
    fa = path_a or latest(a_tag)
    fb = path_b or latest(b_tag)
    if not fa or not fb:
        print("缺少快照: A=%s B=%s" % (fa, fb))
        return
    A = json.load(open(fa, encoding="utf-8"))
    B = json.load(open(fb, encoding="utf-8"))
    print("对比：\n  A=%s (%s 体)\n  B=%s (%s 体)"
          % (os.path.basename(fa), A["total"], os.path.basename(fb), B["total"]))

    def index(snap):
        m = {}
        for g, items in snap["groups"].items():
            for it in items:
                m[it["id"] or it["name"]] = (g, it)
        return m

    ia, ib = index(A), index(B)
    only_a = [k for k in ia if k not in ib]
    only_b = [k for k in ib if k not in ia]
    moved = [(k, ia[k][0], ib[k][0], ia[k][1]) for k in ia
             if k in ib and ia[k][0] != ib[k][0]]

    print("\n=== 分组名差异 ===")
    ga, gb = set(A["groups"]), set(B["groups"])
    print("  只 A 有: %s" % ("、".join(sorted(ga - gb)) or "无"))
    print("  只 B 有: %s" % ("、".join(sorted(gb - ga)) or "无"))

    print("\n=== 同名分组体数差异 ===")
    diff = [(g, len(A["groups"].get(g, [])), len(B["groups"].get(g, [])))
            for g in sorted(ga & gb)
            if len(A["groups"].get(g, [])) != len(B["groups"].get(g, []))]
    if diff:
        for g, x, y in diff:
            print("  %-16s A=%3d  B=%3d  (%+d)" % (g, x, y, y - x))
    else:
        print("  无")

    print("\n=== 体归属变化: %d 个 ===" % len(moved))
    from collections import Counter
    pairs = Counter((a, b) for _, a, b, _ in moved)
    for (a, b), n in pairs.most_common(20):
        print("  %-14s → %-14s  %3d 体" % (a, b, n))
    if moved:
        print("\n  例子：")
        for k, a, b, it in moved[:12]:
            print("    %-18s %-12s → %-12s  尺寸=%s"
                  % (it["name"], a, b, "×".join(str(x) for x in it["dims"])))

    if only_a:
        print("\n=== 只在 A 出现的体（B 里缺/改名）: %d ===" % len(only_a))
        for k in only_a[:10]:
            print("    %-18s 在 %s" % (ia[k][1]["name"], ia[k][0]))
    if only_b:
        print("\n=== 只在 B 出现的体: %d ===" % len(only_b))
        for k in only_b[:10]:
            print("    %-18s 在 %s" % (ib[k][1]["name"], ib[k][0]))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "自动"
    if cmd == "compare":
        compare()
    else:
        snapshot(cmd)
