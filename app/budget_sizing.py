# -*- coding: utf-8 -*-
"""
budget_sizing.py —— 预算优先的尺寸规划

原则（用户要求）：
  1. 先定总单元/节点预算（保证后续求解可行），再反推尺寸
  2. 尺寸不能小于"薄壁可行下限"（≈2.5×壁厚），否则划不出来
  3. 预算不够时，不是硬压尺寸，而是告诉用户：
     - 当前几何的最小可行单元数是多少
     - 哪些组占单元最多（建议改扫掠/六面体，或放宽）

单元数估算： elements ≈ Σ volume / size³ × K
K 由实测标定（同一模型：344 万单元对应各组尺寸已知）
"""
from __future__ import annotations

import math

# 标定系数：由 3,443,976 单元的实测反推（见 calibrate()）
K_DEFAULT = 1.0
# 薄壁可行下限倍数
FEASIBLE_RATIO = 2.5
# 尺寸上限
SIZE_CAP = 40.0
# 实际可用最小尺寸（再小单元数会爆炸，且无必要）
FLOOR_MIN = 6.0


def _wall_fill(b):
    dims = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
    d1, d2, d3 = dims
    bbox = d1 * d2 * d3 or 1.0
    fill = max(0.0, min(1.0, (b.get("vol") or 0) / bbox))
    if fill >= 0.6:
        return d1, fill
    wall = 0.5 * d2 * (1.0 - math.sqrt(max(0.0, 1.0 - fill)))
    return max(0.3, min(wall, d1)), fill


def estimate(groups_sizes: dict, bodies_by_group: dict, k: float = K_DEFAULT) -> float:
    """按组尺寸估算总单元数"""
    total = 0.0
    for g, size in groups_sizes.items():
        if not size or size <= 0:
            continue
        vol = sum(b.get("vol") or 0 for b in bodies_by_group.get(g, []))
        total += vol / (size ** 3) * k
    return total


def calibrate(bodies_by_group: dict, groups_sizes: dict, actual_elements: float) -> float:
    raw = estimate(groups_sizes, bodies_by_group, k=1.0)
    return (actual_elements / raw) if raw > 0 else 1.0


def plan(memory_plan: list, bodies_by_group: dict, target_elements: float,
         k: float = K_DEFAULT, feasible_ratio: float = FEASIBLE_RATIO) -> dict:
    """返回 {组: 尺寸} 以及预演信息

    memory_plan: [{"category":..., "bodies":[...]}] 只用来知道组名
    """
    info = []
    for p in memory_plan:
        g = p["category"]
        members = bodies_by_group.get(g) or []
        if not members:
            continue
        walls = sorted(_wall_fill(b)[0] for b in members)
        wall_med = walls[len(walls) // 2]
        wall_min = walls[0]
        dims = [sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0]) for b in members]
        dmin_med = sorted(d[0] for d in dims)[len(dims) // 2]
        # 可行下限：壁厚决定（薄壁空心） vs 最薄边决定（实心/薄板）
        floor_by_wall = feasible_ratio * wall_min
        floor_by_dim = feasible_ratio * dmin_med
        floor = min(floor_by_wall, floor_by_dim)
        floor = max(FLOOR_MIN, floor)
        vol = sum(b.get("vol") or 0 for b in members)
        info.append({"group": g, "bodies": len(members), "wall_min": round(wall_min, 2),
                     "wall_med": round(wall_med, 2), "dmin_med": round(dmin_med, 1),
                     "floor": round(floor, 1), "vol": vol})

    # 1) 全部按下限 -> 最小可行单元数
    sizes = {x["group"]: x["floor"] for x in info}
    min_elems = estimate(sizes, bodies_by_group, k)

    # 2) 用统一放大系数逼近目标
    def total_at(scale):
        s = {x["group"]: min(SIZE_CAP, max(x["floor"], x["floor"] * scale))
             for x in info}
        return estimate(s, bodies_by_group, k), s

    best_scale, best_sizes, best_elems = 1.0, sizes, min_elems
    if min_elems <= target_elements:
        lo, hi = 1.0, 32.0
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            e, s = total_at(mid)
            if e <= target_elements:
                lo, best_scale, best_sizes, best_elems = mid, mid, s, e
            else:
                hi = mid
    else:
        best_sizes = sizes
        best_elems = min_elems

    # 3) 各组贡献排序（谁最耗单元）
    contrib = []
    for x in info:
        s = best_sizes[x["group"]]
        e = x["vol"] / (s ** 3) * k
        contrib.append({"group": x["group"], "bodies": x["bodies"],
                        "size": round(s, 1), "floor": x["floor"],
                        "wall_min": x["wall_min"],
                        "elements": int(e),
                        "share": 0.0})
    tot = sum(c["elements"] for c in contrib) or 1
    for c in contrib:
        c["share"] = round(100.0 * c["elements"] / tot, 1)
    contrib.sort(key=lambda d: -d["elements"])

    return {"ok": True, "target": target_elements, "k": round(k, 4),
            "min_feasible_elements": int(min_elems),
            "planned_elements": int(best_elems),
            "scale": round(best_scale, 3),
            "reached": best_elems <= target_elements * 1.02,
            "sizes": {g: round(s, 1) for g, s in best_sizes.items()},
            "groups": contrib}
