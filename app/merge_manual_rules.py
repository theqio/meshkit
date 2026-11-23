# -*- coding: utf-8 -*-
"""把手工分组结果合并进 part_rules.json（扩充尺寸表）
   - 跳过「未分组」「其他」
   - 同名分组：追加新尺寸
   - 新分组：新建条目
   用法： python app/merge_manual_rules.py [快照文件]
"""
import glob
import json
import os
import sys
from collections import Counter

APP = os.path.dirname(os.path.abspath(__file__))
REP = os.path.join(os.path.dirname(APP), "reports")
RULES = os.path.join(APP, "part_rules.json")

SKIP = {"未分组", "其他"}

if len(sys.argv) > 1:
    snap_path = sys.argv[1]
else:
    fs = sorted(glob.glob(os.path.join(REP, "分组快照_手动_*.json")))
    if not fs:
        raise SystemExit("找不到手动快照")
    snap_path = fs[-1]

snap = json.load(open(snap_path, encoding="utf-8"))
print("使用快照:", os.path.basename(snap_path), " 体数:", snap["total"])

rules = json.load(open(RULES, encoding="utf-8"))
r = rules["rules"]
added, created, skipped = 0, [], 0

for gname, items in snap["groups"].items():
    if gname in SKIP:
        skipped += len(items)
        continue
    sigs = Counter()
    for it in items:
        d = it.get("dims") or []
        if len(d) != 3 or min(d) <= 0:
            continue
        sigs["%dx%dx%d" % (d[0], d[1], d[2])] += 1
    if not sigs:
        continue
    if gname not in r:
        r[gname] = {"sizes": [], "total": 0, "canonical": True}
        created.append(gname)
    have = set(s.get("sig") for s in r[gname].get("sizes") or [])
    for sig, n in sigs.items():
        if sig in have:
            continue
        r[gname]["sizes"].append({"sig": sig, "count": n, "samples": []})
        added += 1

rules["updated"] = snap["ts"]
rules["source"] = "canonical + manual(12mm)"
with open(RULES, "w", encoding="utf-8") as f:
    json.dump(rules, f, ensure_ascii=False, indent=1)

print("新增分组: %s" % ("、".join(created) or "无"))
print("新增尺寸档: %d 个" % added)
print("跳过（未分组/其他）: %d 体" % skipped)
print("\n各分组尺寸档数:")
for k, v in sorted(r.items()):
    print("   %-16s %2d 档  %d 体(记录数)" % (
        k, len(v.get("sizes") or []), v.get("total") or 0))
