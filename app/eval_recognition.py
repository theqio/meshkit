# -*- coding: utf-8 -*-
"""用「手动分组快照」评估识别准确率
   python app/eval_recognition.py
输出：总体准确率、逐组混淆、错分清单
"""
import glob
import json
import os
import sys

APP = os.path.dirname(os.path.abspath(__file__))
REP = os.path.join(os.path.dirname(APP), "reports")
sys.path.insert(0, APP)

import geo_recognize  # noqa


def load_snapshot(tag="手动"):
    fs = sorted(glob.glob(os.path.join(REP, "分组快照_%s_*.json" % tag)))
    if not fs:
        return None, None
    p = fs[-1]
    return json.load(open(p, encoding="utf-8")), p


def main():
    snap, sp = load_snapshot("手动")
    if not snap:
        raise SystemExit("找不到手动快照")
    truth = {}
    for g, items in snap["groups"].items():
        for it in items:
            key = it.get("id")
            truth[key if key else it["name"]] = g

    # 取当前模型的体（走服务，保证 lx/ly/lz 与识别一致）
    import urllib.request
    req = urllib.request.Request(
        "http://127.0.0.1:8765/api/op",
        json.dumps({"op": "list_bodies", "payload": {}}).encode("utf-8"),
        {"Content-Type": "application/json"}, method="POST")
    lb = json.loads(urllib.request.urlopen(req, timeout=300).read().decode("utf-8"))
    bodies = lb.get("bodies") or []

    rec = geo_recognize.recognize(bodies)
    bid = {b.get("name"): b.get("id") for b in bodies}
    pred = {}
    for g, ns in rec["groups"].items():
        for n in ns:
            pred[n] = g
    
    def key_of(name):
        return bid.get(name) or name

    total = len(bodies)
    hit = 0
    wrong = []
    for b in bodies:
        n = b.get("name")
        k = key_of(n)
        t = truth.get(k) or truth.get(n)
        p = pred.get(n, "其他")
        if t is None:
            continue
        if t == p:
            hit += 1
        else:
            d = sorted([round(b.get("lx") or 0), round(b.get("ly") or 0),
                        round(b.get("lz") or 0)])
            wrong.append((n, t, p, "×".join(str(x) for x in d)))

    judge = sum(1 for b in bodies if (truth.get(key_of(b.get("name"))) or truth.get(b.get("name"))))
    acc = hit / judge * 100 if judge else 0
    print("快照: %s" % os.path.basename(sp))
    print("体数 %d，可比对 %d，正确 %d，准确率 %.1f%%"
          % (total, judge, hit, acc))

    if wrong:
        print("\n=== 错分 %d 个 ===" % len(wrong))
        from collections import Counter
        pair = Counter((t, p) for _, t, p, _ in wrong)
        print("混淆（真值 → 预测）：")
        for (t, p), n in pair.most_common(15):
            print("   %-14s → %-14s %2d" % (t, p, n))
        print("\n明细：")
        for n, t, p, d in wrong[:30]:
            print("   %-22s 真=%-12s 预=%-12s 尺寸=%s" % (n, t, p, d))

    print("\n=== 预测分组规模 ===")
    for g, ns in sorted(rec["groups"].items(), key=lambda kv: -len(kv[1])):
        real = sum(1 for n in ns if truth.get(n) == g)
        print("   %-16s 预测 %3d  其中正确 %3d" % (g, len(ns), real))


if __name__ == "__main__":
    main()
