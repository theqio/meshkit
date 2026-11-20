# -*- coding: utf-8 -*-
"""找出能提前区分"会失败"的判据：对多个候选分数做排序检验"""
import json
import sys
import urllib.request

sys.path.insert(0, r"D:\ai\meshkit\app")

FAILED = {
    "3_上_纵梁_1752x105x185_01",
    "2_中_侧封板_204x2380x46_04",
    "2_中_侧封板_204x2380x46_06",
    "2_中_侧封板_204x2380x46_07",
    "2_中_侧封板_204x2380x46_08",
    "2_中_侧板固定件_209x200x50_01",
    "2_中_板_235x25x235_02",
    "2_中_板_235x25x235_04",
}


def post(path, payload, timeout=300):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + path, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    bodies = post("/api/op", {"op": "list_bodies", "payload": {}})["bodies"]
    rows = []
    for b in bodies:
        dims = sorted([b["lx"], b["ly"], b["lz"]])
        d1, d2, d3 = dims
        bbox = d1 * d2 * d3 or 1.0
        fill = b["vol"] / bbox
        wall = d1 if fill >= 0.6 else max(0.3, 0.5 * d2 * (1.0 - (1.0 - fill) ** 0.5))
        rows.append({
            "name": b["name"], "cat": b["category"], "dims": dims,
            "t": d1, "mid": d2, "long": d3, "fill": fill, "wall": wall,
            "hollow": fill < 0.6,
            "aspect": d3 / d1 if d1 else 0,
            # 候选判据
            "S1_wall_ratio": 40.0 / wall if wall else 0,
            "S2_thinwall_area": (d2 * d3) / (wall * wall) if wall else 0,
            "S3_inplane_over_wall": max(d2, d3) / wall if wall else 0,
            "S4_hollow_aspect": (d3 / d1) * (40.0 / wall) if wall and d1 else 0,
            "S5_wall_over_t": wall / d1 if d1 else 0,
            "S6_shell_like": 1.0 if (d1 / d3) < 0.03 else 0.0,
        })

    fails = [r for r in rows if r["name"] in FAILED]
    print(u"失败体 %d / 全体 %d\n" % (len(fails), len(rows)))

    print(u"%-22s %-16s %7s %7s %7s %7s %7s %7s" % (
        u"判据", u"含义", u"失败体min", u"失败体中位", u"成功体90%",
        u"前8名含失败", u"前20含失败", u""))
    names = {
        "S1_wall_ratio": u"40/壁厚",
        "S2_thinwall_area": u"面内面积/壁厚²",
        "S3_inplane_over_wall": u"面内长/壁厚",
        "S4_hollow_aspect": u"(长/薄)×(40/壁厚)",
        "S5_wall_over_t": u"壁厚/最薄边",
        "S6_shell_like": u"薄片标志",
    }
    for key, cn in names.items():
        srt = sorted(rows, key=lambda d: -d[key])
        top8 = set(r["name"] for r in srt[:8]) & FAILED
        top20 = set(r["name"] for r in srt[:20]) & FAILED
        fv = sorted(r[key] for r in fails)
        ok = sorted(r[key] for r in rows if r["name"] not in FAILED)
        print(u"%-22s %-16s %7.2f %7.2f %7.2f %7d %7d" % (
            key, cn, fv[0], fv[len(fv) // 2], ok[int(len(ok) * 0.9)],
            len(top8), len(top20)))

    # 组合：只对"空心"体排序
    print(u"\n只对空心体（%d 个）看 S3（面内长/壁厚）：" % len([r for r in rows if r["hollow"]]))
    hol = sorted([r for r in rows if r["hollow"]], key=lambda d: -d["S3_inplane_over_wall"])
    f_pos = [i + 1 for i, r in enumerate(hol) if r["name"] in FAILED]
    print(u"  失败体在其中排名：%s（共 %d 个空心体）" % (sorted(f_pos), len(hol)))
    print(u"  前 10 名空心体：")
    for r in hol[:10]:
        mark = u"★失败" if r["name"] in FAILED else u"    "
        print(u"    %s %-40s 壁厚=%.1f 面内长/壁厚=%.0f 长薄比=%.0f" % (
            mark, r["name"][:40], r["wall"], r["S3_inplane_over_wall"], r["aspect"]))


if __name__ == "__main__":
    main()
