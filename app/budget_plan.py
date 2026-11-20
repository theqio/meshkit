# -*- coding: utf-8 -*-
"""
budget_plan.py —— 预算优先的"方法 + 尺寸"规划

核心认识（本次教训）：
   单元数不是“能划出来就行”，而是要在网格化之前就控制住，
   否则求解时间/收敛都会崩。

  纯四面体 + 2.5×壁厚 的约束下，这个模型最小也要 ~3.4M 单元。
  要降到 1~1.5M，必须对这些件换方法：

    薄板（厚度≤8mm、面积大）  -> Sweep，2 层穿过厚度，面内可 30~40mm
    细长薄壁件（长细比≥8）    -> Sweep 沿长度，截面 8~16 个单元
    紧凑块体                  -> 四面体，3×壁厚

  估算公式：
    四面体: n ≈ V / s³
    扫掠  : n ≈ 面内单元数 × 层数（板）/ 长度方向段数 × 截面单元数（细长）
"""
from __future__ import annotations

import math

K_TET = 5.6784          # 四面体标定系数（由实测 3.44M 反推）
PLATE_THICK = 8.0       # 厚度小于此值视为薄板
SLENDER = 8.0           # 长细比大于此值视为细长件


def classify(b):
    """返回 (类型, 尺寸[薄向, 中向, 长向])"""
    dims = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
    d1, d2, d3 = dims
    if d1 <= 0:
        return "tetra", dims
    bbox = d1 * d2 * d3 or 1.0
    fill = max(0.0, min(1.0, (b.get("vol") or 0) / bbox))
    aspect = d3 / d1
    # 薄板：厚度 ≤ 8mm，且另两个方向至少一个是厚度的 4 倍以上（薄片状）
    if d1 <= PLATE_THICK and (d2 >= 4.0 * d1) and (d3 >= 4.0 * d1):
        return "plate", dims
    if aspect >= SLENDER and fill < 0.6:
        return "slender", dims
    return "tetra", dims


def est_elements(b, kind, s):
    """按类型与面内尺寸 s 估算单元数"""
    dims = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
    d1, d2, d3 = dims
    vol = b.get("vol") or 0.0
    if kind == "plate":
        layers = max(2, int(math.ceil(d1 / max(s, 1e-6))))
        layers = min(layers, 3)
        return (d2 * d3) / (s * s) * layers
    if kind == "slender":
        # 截面周长 / (s/2) 个单元绕一圈，至少 8
        perim = 2.0 * (d1 + d2)
        around = max(8.0, perim / max(s * 0.5, 1e-6))
        along = d3 / max(s, 1e-6)
        return around * along * 0.5      # 0.5：矩形扫掠单元比四面体省
    return vol / (s ** 3) * K_TET


def plan(groups: dict, target: float, s_min: float = 8.0, s_max: float = 45.0) -> dict:
    """groups: {组名: [bodies]}；返回每组的方法/尺寸/估算单元"""
    items = []
    for g, mem in groups.items():
        kinds = {}
        for b in mem:
            k, dims = classify(b)
            kinds[k] = kinds.get(k, 0) + 1
        kind = max(kinds.items(), key=lambda kv: kv[1])[0]
        items.append({"group": g, "bodies": len(mem), "kind": kind, "members": mem})

    # 每个组的"尺寸上限"：四面体件受 3×壁厚约束；扫掠件不受
    for it in items:
        if it["kind"] == "tetra":
            walls = []
            for b in it["members"]:
                dims = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
                bbox = dims[0] * dims[1] * dims[2] or 1.0
                fill = max(0.0, min(1.0, (b.get("vol") or 0) / bbox))
                walls.append(dims[0] if fill >= 0.6 else
                             max(0.3, 0.5 * dims[1] * (1.0 - math.sqrt(max(0.0, 1.0 - fill)))))
            it["cap"] = max(6.0, 3.0 * min(walls))
        elif it["kind"] == "plate":
            # 薄板扫掠：面内尺寸可以大（厚度方向用 2 层），面内上限 45
            it["cap"] = 45.0
        else:
            # 细长件扫掠：面内尺寸不能超过截面最小边的一半
            secs = []
            for b in it["members"]:
                dims = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
                secs.append(min(dims[0], dims[1]) / 2.0)
            it["cap"] = max(6.0, min(45.0, min(secs)))

    def total_at(s):
        tot, per = 0.0, {}
        for it in items:
            s_eff = min(s, it["cap"])
            e = sum(est_elements(b, it["kind"], s_eff) for b in it["members"])
            per[it["group"]] = e
            tot += e
        return tot, per

    lo, hi = s_min, s_max
    tot, per = total_at(s_max)
    if tot > target:
        # 即使最大尺寸也超预算 -> 用最大尺寸并如实报告
        chosen = s_max
    else:
        tot, per = total_at(s_min)
        if tot <= target:
            chosen = s_min
        else:
            for _ in range(40):
                mid = 0.5 * (lo + hi)
                t2, _p = total_at(mid)
                if t2 <= target:
                    lo = mid
                else:
                    hi = mid
            chosen = lo
    tot, per = total_at(chosen)

    detail = []
    for it in items:
        s_eff = min(chosen, it["cap"])
        detail.append({"group": it["group"], "bodies": it["bodies"],
                       "kind": it["kind"], "size": round(s_eff, 1),
                       "method": ("Sweep" if it["kind"] in ("plate", "slender")
                                  else "Automatic"),
                       "elements": int(per[it["group"]]),
                       "share": 0.0})
    for d in detail:
        d["share"] = round(100.0 * d["elements"] / max(1, tot), 1)
    detail.sort(key=lambda d: -d["elements"])
    return {"ok": True, "target": target, "size": round(chosen, 1),
            "elements": int(tot), "reached": tot <= target * 1.03,
            "groups": detail}
