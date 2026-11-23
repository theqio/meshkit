# -*- coding: utf-8 -*-
"""按当前 Geometry 分组统一重命名体：部位_序号
   - 「其他」「未分组」组内的体不改名
   - 序号从该组已有最大值继续，保证全局唯一
用法： python app/normalize_names.py [--dry]
"""
import json
import re
import sys
import urllib.request

BASE = "http://127.0.0.1:8765"
DRY = "--dry" in sys.argv
SKIP = {"其他", "未分组"}


def post(p, payload=None, timeout=1800):
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + p, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def op(name, payload=None):
    return post("/api/op", {"op": name, "payload": payload or {}})


def main():
    lb = op("list_bodies")
    bodies = lb.get("bodies") or []
    names = [b.get("name") for b in bodies]

    gg = op("geom_groups")
    groups = {}
    for g in gg.get("groups") or []:
        groups[g.get("name")] = list(g.get("bodies") or [])
    for n in gg.get("ungrouped") or []:
        groups.setdefault("未分组", []).append(n)

    pat = re.compile(r"^(.+)_(\d+)$")
    used = set(names)
    next_idx = {}
    # 先给每组定起点序号
    for g, members in groups.items():
        mx = 0
        for n in members:
            m = pat.match(n or "")
            if m and m.group(1) == g:
                try:
                    mx = max(mx, int(m.group(2)))
                except Exception:
                    pass
        next_idx[g] = mx

    items, planned = [], []
    for g, members in groups.items():
        if g in SKIP:
            continue
        for n in members:
            m = pat.match(n or "")
            if m and m.group(1) == g:
                continue          # 已规范
            # 找一个没被占用的名字
            while True:
                next_idx[g] += 1
                cand = "%s_%02d" % (g, next_idx[g])
                if cand not in used:
                    break
            used.add(cand)
            items.append({"body": n, "group": g})
            planned.append((n, cand))

    print("待改名 %d 个（跳过 其他/未分组）" % len(items))
    for a, b in planned[:15]:
        print("   %-28s → %s" % (a, b))
    if len(planned) > 15:
        print("   ... 还有 %d 个" % (len(planned) - 15))

    if DRY or not items:
        return

    r = op("rename_bodies", {"mode": "prefix", "items": items})
    print("\n改名结果: ok=%s 改名=%s 跳过=%s 失败=%s"
          % (r.get("ok"), r.get("count"),
             len(r.get("skipped") or []), len(r.get("failed") or [])))
    for f in (r.get("failed") or [])[:10]:
        print("   失败:", f)

    # 复核
    lb2 = op("list_bodies")
    names2 = set(b.get("name") for b in (lb2.get("bodies") or []))
    bad = [(a, b) for a, b in planned if b not in names2]
    print("复核：计划改名 %d，实际生效 %d，未生效 %d"
          % (len(planned), len(planned) - len(bad), len(bad)))
    for a, b in bad[:10]:
        print("   未生效: %s → %s" % (a, b))


if __name__ == "__main__":
    main()
