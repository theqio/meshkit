# -*- coding: utf-8 -*-
"""诊断复盘：对比之前失败体与成功体的几何特征，找出可提前判定的失效判据"""
import collections
import json
import sys
import urllib.request

sys.path.insert(0, r"D:\ai\meshkit\app")


def post(path, payload, timeout=300):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + path, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


FAILED_BEFORE = {
    "3_上_纵梁_1752x105x185_01": "10mm/5mm/2mm 都失败",
    "2_中_侧封板_204x2380x46_04": "10mm 修好",
    "2_中_侧封板_204x2380x46_06": "10mm 修好",
    "2_中_侧封板_204x2380x46_07": "10mm 修好",
    "2_中_侧封板_204x2380x46_08": "10mm 修好",
    "2_中_侧板固定件_209x200x50_01": "10mm 修好",
    "2_中_板_235x25x235_02": "10mm 修好",
    "2_中_板_235x25x235_04": "10mm 修好",
}


def wall_est(dims, fill):
    """壁厚估算：实心件=最薄边；空心件由截面填充率反推"""
    d1, d2, d3 = dims
    if fill >= 0.6:
        return d1, "实心(最薄边)"
    ratio = 1.0 - fill
    if ratio <= 0:
        return d1, "实心(最薄边)"
    w = 0.5 * d2 * (1.0 - ratio ** 0.5)
    return w, "空心(填充率反推)"


def main():
    bodies = post("/api/op", {"op": "list_bodies", "payload": {}})["bodies"]
    global_size = 40.0

    rows = []
    for b in bodies:
        dims = sorted([b["lx"], b["ly"], b["lz"]])
        bbox = dims[0] * dims[1] * dims[2] or 1.0
        fill = b["vol"] / bbox
        w, how = wall_est(dims, fill)
        rows.append({
            "name": b["name"], "cat": b["category"],
            "dims": dims, "t": dims[0], "fill": round(fill, 4),
            "wall": round(w, 2), "how": how,
            "ratio_t": round(global_size / dims[0], 2) if dims[0] else 0,
            "ratio_wall": round(global_size / w, 2) if w > 0 else 0,
            "aspect": round(dims[2] / dims[0], 1) if dims[0] else 0,
            "failed": b["name"] in FAILED_BEFORE,
        })

    print(u"=== 之前失败过的体 ===")
    print(u"%-38s %-16s %8s %8s %8s %s" % (
        u"体名", u"尺寸", u"最薄", u"壁厚", u"40/最薄", u"40/壁厚"))
    for r in sorted([x for x in rows if x["failed"]], key=lambda d: -d["ratio_wall"]):
        print(u"%-38s %-16s %8.1f %8.1f %8.1f %8.1f" % (
            r["name"][:38], "x".join("%.0f" % d for d in r["dims"]),
            r["t"], r["wall"], r["ratio_t"], r["ratio_wall"]))

    ok = [x for x in rows if not x["failed"]]
    print(u"\n=== 成功体统计（%d 个）===" % len(ok))
    for key in ("ratio_t", "ratio_wall"):
        vals = sorted(x[key] for x in ok)
        print(u"  %-10s 中位=%.1f 90%%=%.1f 最大=%.1f" % (
            key, vals[len(vals) // 2], vals[int(len(vals) * 0.9)], vals[-1]))
    fails = [x for x in rows if x["failed"]]
    for key in ("ratio_t", "ratio_wall"):
        vals = sorted(x[key] for x in fails)
        print(u"  失败体 %-8s 最小=%.1f 中位=%.1f" % (key, vals[0], vals[len(vals) // 2]))

    # 用阈值分割检验
    print(u"\n=== 阈值检验：size/最薄边 > 2.0 判风险 ===")
    for thr in (1.5, 2.0, 2.5, 3.0):
        hit = [x for x in rows if x["ratio_t"] > thr]
        tp = len([x for x in hit if x["failed"]])
        fp = len([x for x in hit if not x["failed"]])
        print(u"  阈值 %.1f：命中 %d 个（其中真失败 %d，误报 %d），"
              u"漏掉失败 %d" % (thr, len(hit), tp, fp, len(fails) - tp))

    print(u"\n=== 阈值检验：size/壁厚 > 3.0 判风险 ===")
    for thr in (2.0, 3.0, 4.0, 5.0):
        hit = [x for x in rows if x["ratio_wall"] > thr]
        tp = len([x for x in hit if x["failed"]])
        fp = len([x for x in hit if not x["failed"]])
        print(u"  阈值 %.1f：命中 %d 个（其中真失败 %d，误报 %d），"
              u"漏掉失败 %d" % (thr, len(hit), tp, fp, len(fails) - tp))

    json.dump(rows, open(r"D:\ai\meshkit\reports\失败诊断数据.json", "w",
                         encoding="utf-8"), ensure_ascii=False, indent=1)
    print(u"\n明细已写入 reports\\失败诊断数据.json")


if __name__ == "__main__":
    main()
