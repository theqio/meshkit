# -*- coding: utf-8 -*-
"""
mesh_risk.py —— 网格风险预判（第一遍扫描就给结论与对策）

依据：本次 286 体模型里实际失败过的 8 个体，复盘结论：

  失败体 8 个 → 全部是「薄壁空心件」，壁厚 2~7mm
     · 6 个（侧封板×4、侧板固定件、板×2）把局部尺寸降到 10mm 后成功
       -> 原因：单元尺寸(20~40mm) 远大于壁厚，壁内放不下单元
       -> 对策：第一遍就按 2.5×壁厚 给局部尺寸，不必等失败
     · 1 个（纵梁 1752x105x185，壁厚≈2.2mm、长细比 17）
       降到 10/5/2mm 依然失败
       -> 原因：尺寸修不了，属"极薄壁长梁"
       -> 对策：改扫掠(沿长度)/六面体主导，或几何简化
     · 1 个（同类几何）随 A 类一起修好

注意：包围盒特征无法预测具体哪个体失败（8 个失败体在 286 个里排名 56~140），
所以本模块不承诺"预测"，而是**按几何条件提前给保守设置**，把 A 类失败消灭在第一遍。
"""
from __future__ import annotations

import math

# 壁厚系数：局部尺寸 = k × 壁厚
K_THINWALL = 2.5
# 极薄壁长梁：长细比达到该值且壁厚 < 3mm，尺寸无效，要换方法
SLENDER_ASPECT = 12.0
VERY_THIN_WALL = 3.0
# 局部尺寸上下限
SIZE_FLOOR = 2.0
SIZE_CEIL = 20.0


def _wall_and_fill(body: dict):
    dims = sorted([body.get("lx") or 0, body.get("ly") or 0, body.get("lz") or 0])
    d1, d2, d3 = dims
    bbox = d1 * d2 * d3 or 1.0
    fill = (body.get("vol") or 0) / bbox if bbox else 1.0
    fill = max(0.0, min(1.0, fill))
    if fill >= 0.6:
        return d1, fill, False            # 实心：壁厚=最薄边
    ratio = 1.0 - fill
    wall = 0.5 * d2 * (1.0 - math.sqrt(max(0.0, ratio)))
    wall = max(0.3, min(wall, d1))
    return wall, fill, True


def assess_categories(bodies: list, cat_sizes: dict, global_size: float = 40.0) -> list:
    """按"类别"聚合风险：用该类别当前实际尺寸 / 壁厚 判断

    cat_sizes: {类别: 当前尺寸(mm) 或 None}
    返回每个类别一条建议（按最薄壁定，保证整类都能划出来）
    """
    per = {}
    for b in bodies:
        dims = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
        wall, fill, hollow = _wall_and_fill(b)
        cat = b.get("category") or "未分类"
        d = per.setdefault(cat, {"walls": [], "aspects": [], "n": 0,
                                 "hollow_n": 0})
        d["n"] += 1
        if hollow:
            d["hollow_n"] += 1
            d["walls"].append(wall)
            d["aspects"].append((dims[2] / dims[0]) if dims[0] else 0)

    out = []
    for cat, d in per.items():
        if not d["walls"]:
            continue
        walls = sorted(d["walls"])
        w_min = walls[0]
        w_p20 = walls[max(0, int(len(walls) * 0.2) - 1)]
        cur = cat_sizes.get(cat)
        cur_eff = cur if cur else global_size
        ratio = cur_eff / w_min if w_min > 0 else 0
        sugg = max(SIZE_FLOOR, min(SIZE_CEIL, round(K_THINWALL * w_p20, 1)))
        level = "ok"
        cause = ""
        action = ""
        if ratio >= 3.0:
            level = "high"
            cause = u"最薄壁 %.1fmm，当前尺寸 %.0fmm 约 %.0f 倍壁厚" % (w_min, cur_eff, ratio)
            action = u"尺寸降到 %.1fmm 左右（2.5×壁厚）" % sugg
        elif ratio >= 1.8:
            level = "warn"
            cause = u"最薄壁 %.1fmm，当前尺寸 %.0fmm 约 %.1f 倍壁厚" % (w_min, cur_eff, ratio)
            action = u"建议 ≤ %.1fmm" % sugg
        out.append({"cat": cat, "bodies": d["n"], "hollow": d["hollow_n"],
                    "wall_min": round(w_min, 2), "wall_p20": round(w_p20, 2),
                    "cur_size": cur, "cur_eff": round(cur_eff, 1),
                    "ratio": round(ratio, 1), "suggest": sugg,
                    "level": level, "cause": cause, "action": action})
    out.sort(key=lambda x: (-(x["level"] == "high"), -x["ratio"]))
    return out


def assess(bodies: list, global_size: float = 40.0) -> dict:
    """返回每个体的风险等级与对策"""
    out = []
    for b in bodies:
        dims = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
        d1, d2, d3 = dims
        wall, fill, hollow = _wall_and_fill(b)
        aspect = (d3 / d1) if d1 else 0.0
        item = {"name": b.get("name"), "cat": b.get("category"),
                "dims": [round(x, 1) for x in dims],
                "fill": round(fill, 3), "wall": round(wall, 2),
                "aspect": round(aspect, 1), "hollow": hollow,
                "level": "ok", "cause": "", "action": "",
                "size": None}

        if not hollow:
            out.append(item)
            continue

        # A 类：薄壁空心，单元尺寸 > 2×壁厚 -> 壁内放不下单元
        risk_ratio = global_size / wall if wall > 0 else 0
        size_fix = max(SIZE_FLOOR, min(SIZE_CEIL, round(K_THINWALL * wall, 1)))

        # B 类：极薄壁 + 长细 -> 尺寸无效
        if wall < VERY_THIN_WALL and aspect >= SLENDER_ASPECT and d3 > 1000:
            item["level"] = "high"
            item["cause"] = (u"极薄壁长梁：壁厚 %.1fmm、长细比 %.0f；"
                             u"降尺寸无效（实测 10/5/2mm 均失败）" % (wall, aspect))
            item["action"] = u"改方法：沿长度扫掠或六面体主导；或加厚壁/几何简化"
            item["size"] = None
        elif risk_ratio >= 2.0:
            item["level"] = "high"
            item["cause"] = (u"薄壁空心：壁厚 %.1fmm，而单元尺寸 %.0fmm 约 %.1f 倍壁厚，"
                             u"壁内放不下单元" % (wall, global_size, risk_ratio))
            item["action"] = u"局部尺寸设为 %.1fmm（2.5×壁厚）" % size_fix
            item["size"] = size_fix
        elif risk_ratio >= 1.2:
            item["level"] = "warn"
            item["cause"] = (u"薄壁空心：单元尺寸 %.0fmm / 壁厚 %.1fmm = %.1f，接近极限"
                             % (global_size, wall, risk_ratio))
            item["action"] = u"建议局部尺寸 ≤ %.1fmm" % size_fix
            item["size"] = size_fix
        out.append(item)

    high = [x for x in out if x["level"] == "high"]
    warn = [x for x in out if x["level"] == "warn"]
    return {"total": len(out), "high": len(high), "warn": len(warn),
            "items": out,
            "summary": {
                "A_薄壁空心_尺寸可修": len([x for x in high
                                        if x["size"] is not None]),
                "B_极薄壁长梁_尺寸不可修": len([x for x in high
                                            if x["size"] is None]),
            }}