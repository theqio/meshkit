# -*- coding: utf-8 -*-
"""
sizing_advisor.py —— 按几何特征推荐网格尺寸（Python 3）

不再用固定值（30/20/40），而是按每个类别的实际几何算：

  1) 特征尺寸初值
     - 壳体（面体）      : 较小平面尺度 / 6        （保证面内有足够单元）
     - 薄板（实心薄片）  : 2.5 × 板厚             （厚度方向约 1~2 层）
     - 空心薄壁（型材）  : 4.0 × 壁厚             （壁厚由截面填充率反推）
     - 实体块           : 中间尺度 / 6
     并加平面约束：尺寸 ≤ 最窄平面尺度 / 4（避免出现"一格宽"的条带）

  2) 单元预算缩放
     用 bbox 面积代理估算单元数  N ≈ K × Σ(A_i / s_i²)
     再用当前模型的实际单元数反标定 K
     若 N > 预算，则整体放大尺寸（开方缩放），并夹在 [MinimumSize, ElementSize] 之间

输出每个类别：推荐尺寸 + 依据文字；以及整体估算单元数、缩放系数。
"""
from __future__ import annotations

import math

DEFAULT_BUDGET_ELEMENTS = 1_450_000     # 与当前 143 万单元同量级
DEFAULT_K = 1.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _area_proxy(item: dict) -> float:
    """该类别的网格面积代理（mm²）：优先用 bbox 表面积之和"""
    area = item.get("bbox_area") or 0.0
    if area > 0:
        return float(area)
    # 兜底：按体积^(2/3) 估
    vol = item.get("volume") or 0.0
    return 6.0 * (vol ** (2.0 / 3.0)) if vol > 0 else 0.0


def feature_size(item: dict) -> tuple[float, str]:
    """按几何特征给出初值（mm）与依据说明"""
    shells = item.get("shells") or 0
    thin = item.get("thin") or 0
    wall = item.get("wall_mm") or item.get("d_small") or 5.0
    d_mid = item.get("d_mid") or 100.0
    d_large = item.get("d_large") or 1000.0
    fill = item.get("fill_med")

    if shells:
        base = min(d_mid, d_large) / 8.0
        basis = "壳体：面内尺寸 ≈ 较小平面尺度/8（%.0f/8）" % min(d_mid, d_large)
    elif thin:
        base = 2.5 * wall
        basis = "薄板：≈2.5×板厚（%.1f×2.5）" % wall
    elif fill is not None and fill < 0.6:
        base = 4.0 * wall
        basis = "空心薄壁：≈4×壁厚（%.1f×4，壁厚由填充率 %.2f 反推）" % (wall, fill)
    else:
        base = d_mid / 6.0
        basis = "实体：≈中间尺度/6（%.0f/6）" % d_mid

    # 平面约束：至少 4 个单元跨过最窄平面方向
    inplane = d_mid if d_mid else d_large
    if inplane and base > inplane / 4.0:
        base = inplane / 4.0
        basis += "；受最窄平面约束 ≤%.1f" % (inplane / 4.0)
    return base, basis


def estimate_elements(cats: list[dict], sizes: dict[str, float], k: float) -> float:
    total = 0.0
    for c in cats:
        s = sizes.get(c["name"])
        if not s or s <= 0:
            continue
        area = _area_proxy(c)
        if area <= 0:
            continue
        total += area / (s * s)
    return k * total


def calibrate_k(cats: list[dict], current_sizes: dict[str, float],
                current_elements: float) -> float:
    """用当前模型的实际单元数反标定面积-单元系数 K"""
    if not current_elements or current_elements <= 0:
        return DEFAULT_K
    denom = estimate_elements(cats, current_sizes, 1.0)
    if denom <= 0:
        return DEFAULT_K
    k = current_elements / denom
    # 防止离谱值
    return _clamp(k, 0.05, 20.0)


def recommend(cats: list[dict], cfg: dict, budget_elements: float | None = None,
              current_elements: float = 0.0) -> dict:
    """返回 {items: [{name, size, basis, current}], est_elements, scale, k, budget}"""
    settings = (cfg or {}).get("settings") or {}
    floor = float(settings.get("MinimumSize") or 10.0)
    cap = float(settings.get("ElementSize") or 40.0)
    budget = float(budget_elements or (cfg or {}).get("budget_elements")
                   or DEFAULT_BUDGET_ELEMENTS)

    current = {}
    for c in cats:
        v = c.get("size_mm")
        if v:
            current[c["name"]] = float(v)

    k = calibrate_k(cats, current, current_elements) if current else DEFAULT_K

    # 1) 特征初值（纯几何，不考虑预算）
    base = {}
    basis = {}
    for c in cats:
        s, why = feature_size(c)
        base[c["name"]] = _clamp(s, floor, cap)
        basis[c["name"]] = why

    est_feature = estimate_elements(cats, base, k)

    # 2) 预算版：若特征尺寸估算超预算，按开方放大（最多放大 2.5 倍，避免过度放粗）
    sizes_budget = dict(base)
    scale = 1.0
    for _ in range(4):
        est = estimate_elements(cats, sizes_budget, k)
        if est <= 0 or est <= budget * 1.02:
            break
        f = math.sqrt(est / budget)
        if scale * f > 2.5:
            f = 2.5 / scale
        if f <= 1.001:
            break
        scale *= f
        for name in sizes_budget:
            sizes_budget[name] = _clamp(sizes_budget[name] * f, floor, cap)
    est_budget = estimate_elements(cats, sizes_budget, k)

    items = []
    for c in cats:
        name = c["name"]
        items.append({
            "name": name,
            "size": round(base[name], 1),
            "size_budget": round(sizes_budget[name], 1),
            "basis": basis[name],
            "current": current.get(name),
            "bodies": c.get("bodies"),
            "shells": c.get("shells"),
            "thin": c.get("thin"),
            "wall_mm": c.get("wall_mm"),
            "d_small": c.get("d_small"),
            "d_mid": c.get("d_mid"),
            "d_large": c.get("d_large"),
        })

    over = est_budget > budget * 1.05
    return {
        "ok": True,
        "items": items,
        "k": round(k, 4),
        "scale": round(scale, 3),
        "budget_elements": int(budget),
        "est_elements": int(est_budget),          # 预算版估算
        "est_feature": int(est_feature),          # 特征版估算
        "current_elements": int(current_elements or 0),
        "floor": floor,
        "cap": cap,
        "over_budget": over,
        "note": (u"特征尺寸估算 %.0f 万单元，超出预算；预算版已上调 ×%.2f（可能仍超）"
                 % (est_feature / 10000.0, scale)) if over else
                (u"特征尺寸即在预算内（估算 %.0f 万）" % (est_feature / 10000.0)),
    }
