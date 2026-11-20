# -*- coding: utf-8 -*-
"""
model_memory.py —— 模型记忆（按几何特征识别零件类别）

为什么不用名字：换个尺寸/结构的相近模型，名字会变，
但"形状比例 + 相对高度 + 填充率"基本不变。

每个体的特征向量（都与绝对尺寸无关）：
    r1   = 最薄边 / 最长边        （薄片≈0、方块≈1）
    r2   = 中间边 / 最长边        （细长杆小、板大）
    fill = 体积 / 包围盒体积      （实心≈1、空心型材小）
    shell= 是否面体
    z    = (质心Y - Ymin) / (Ymax - Ymin)   （相对高度 0~1）

类别指纹 = 该类所有体的特征样本（去重，最多 8 个）。
匹配 = 每个体到各类别样本的最小加权距离，取最近且 < 阈值者。
"""
from __future__ import annotations

import json
import math
import os
import time

MEM_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_memory.json")

MAX_SAMPLES = 8
DIST_THRESHOLD = 0.55

W_R1 = 1.2
W_R2 = 1.0
W_FILL = 1.0
W_Z = 1.1
W_SHELL = 0.9


# ============================================================================
#  特征
# ============================================================================
def body_features(body: dict, ymin: float, ymax: float) -> dict | None:
    lx = float(body.get("lx") or 0)
    ly = float(body.get("ly") or 0)
    lz = float(body.get("lz") or 0)
    dims = sorted([d for d in (lx, ly, lz) if d > 0])
    if len(dims) != 3 or dims[2] <= 0:
        return None
    d1, d2, d3 = dims
    bbox = d1 * d2 * d3
    vol = float(body.get("vol") or 0)
    fill = max(0.0, min(1.0, vol / bbox)) if bbox > 0 else 0.0
    span = (ymax - ymin) or 1.0
    z = (float(body.get("cy") or 0) - ymin) / span
    return {"r1": d1 / d3, "r2": d2 / d3, "fill": fill,
            "shell": bool(body.get("shell")), "z": max(0.0, min(1.0, z))}


def _dist(a: dict, b: dict) -> float:
    d = 0.0
    # 比例用对数差，避免小比例被压制
    d += W_R1 * abs(math.log10(max(a["r1"], 1e-4)) - math.log10(max(b["r1"], 1e-4)))
    d += W_R2 * abs(math.log10(max(a["r2"], 1e-4)) - math.log10(max(b["r2"], 1e-4)))
    d += W_FILL * abs(a["fill"] - b["fill"])
    d += W_Z * abs(a["z"] - b["z"])
    if bool(a["shell"]) != bool(b["shell"]):
        d += W_SHELL
    return d


def _round_sig(f: dict) -> tuple:
    return (round(math.log10(max(f["r1"], 1e-4)), 2),
            round(math.log10(max(f["r2"], 1e-4)), 2),
            round(f["fill"], 2), bool(f["shell"]), round(f["z"], 2))


# ============================================================================
#  构建 / 保存 / 读取
# ============================================================================
def build_profile(bodies: list, categories: list, name: str) -> dict:
    """用当前模型构建一份记忆"""
    ys = [float(b.get("cy") or 0) for b in bodies] or [0.0]
    ymin, ymax = min(ys), max(ys)

    feats = {}
    for b in bodies:
        f = body_features(b, ymin, ymax)
        if f:
            feats[b["name"]] = f

    # 类别 -> 该类的体名
    by_cat = {}
    for b in bodies:
        cat = b.get("category") or "未分类"
        by_cat.setdefault(cat, []).append(b["name"])

    cats = []
    for c in categories:
        cname = c.get("name")
        members = by_cat.get(cname) or []
        samples, seen = [], set()
        for nm in members:
            f = feats.get(nm)
            if not f:
                continue
            key = _round_sig(f)
            if key in seen:
                continue
            seen.add(key)
            samples.append({"r1": round(f["r1"], 5), "r2": round(f["r2"], 5),
                            "fill": round(f["fill"], 4), "shell": f["shell"],
                            "z": round(f["z"], 4)})
            if len(samples) >= MAX_SAMPLES:
                break
        cats.append({"name": cname, "size": c.get("size_mm"),
                     "method": c.get("method") or "Automatic",
                     "count": len(members), "samples": samples})
    return {"name": name,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "body_count": len(bodies),
            "y_range": [round(ymin, 1), round(ymax, 1)],
            "categories": cats}


_CTRL_PREFIXES = ("SIZE_FIX_", "SIZE_", "METHOD_", "SWEEP_", "SHELL_")


def category_of_control(name: str) -> str:
    """SIZE_横梁 / METHOD_横梁 / SWEEP_板_实体  ->  横梁 / 横梁 / 板"""
    n = name or ""
    for p in _CTRL_PREFIXES:
        if n.startswith(p):
            n = n[len(p):]
            break
    if n.endswith("_实体"):
        n = n[:-3]
    return n or "未分类"


def build_profile_from_plan(bodies: list, controls: list, name: str) -> dict:
    """用"用户在 Mechanical 里实际的规划"构建记忆。

    controls 来自代理的 read_plan：[{name, size_mm, method, bodies:[体名]}]
    同类名的 SIZE_/METHOD_/SWEEP_ 会合并成一个类别。
    """
    ys = [float(b.get("cy") or 0) for b in bodies] or [0.0]
    ymin, ymax = min(ys), max(ys)
    feats = {}
    for b in bodies:
        f = body_features(b, ymin, ymax)
        if f:
            feats[b["name"]] = f

    merged = {}
    for c in controls or []:
        cat = category_of_control(c.get("name") or "")
        if not cat or cat == "未分类":
            continue
        item = merged.setdefault(cat, {"name": cat, "size": None,
                                       "method": "Automatic", "bodies": set()})
        if item["size"] is None and c.get("size_mm"):
            item["size"] = c["size_mm"]
        m = (c.get("method") or "").strip()
        if m and m != "Automatic" and item["method"] == "Automatic":
            item["method"] = m
        for nm in (c.get("bodies") or []):
            item["bodies"].add(nm)

    cats = []
    for cat, item in merged.items():
        samples, seen = [], set()
        for nm in item["bodies"]:
            f = feats.get(nm)
            if not f:
                continue
            key = _round_sig(f)
            if key in seen:
                continue
            seen.add(key)
            samples.append({"r1": round(f["r1"], 5), "r2": round(f["r2"], 5),
                            "fill": round(f["fill"], 4), "shell": f["shell"],
                            "z": round(f["z"], 4)})
            if len(samples) >= MAX_SAMPLES:
                break
        cats.append({"name": cat, "size": item["size"],
                     "method": item["method"],
                     "count": len(item["bodies"]), "samples": samples})

    all_names = set(b["name"] for b in bodies)
    assigned = set()
    for cat, item in merged.items():
        assigned |= item["bodies"]
    return {"name": name,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "body_count": len(bodies),
            "y_range": [round(ymin, 1), round(ymax, 1)],
            "source": "user_plan",
            "uncovered": sorted(all_names - assigned),
            "categories": cats}


def load_all() -> dict:
    if not os.path.isfile(MEM_PATH):
        return {"profiles": []}
    try:
        with open(MEM_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "profiles" not in data:
            data = {"profiles": []}
        return data
    except Exception:
        return {"profiles": []}


def save_all(data: dict) -> None:
    with open(MEM_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def save_profile(profile: dict) -> dict:
    data = load_all()
    profs = data.get("profiles") or []
    kept = [p for p in profs if p.get("name") != profile["name"]]
    kept.append(profile)
    data["profiles"] = kept
    save_all(data)
    return profile


def delete_profile(name: str) -> bool:
    data = load_all()
    profs = data.get("profiles") or []
    kept = [p for p in profs if p.get("name") != name]
    if len(kept) == len(profs):
        return False
    data["profiles"] = kept
    save_all(data)
    return True


def get_profile(name: str) -> dict | None:
    for p in (load_all().get("profiles") or []):
        if p.get("name") == name:
            return p
    return None


def list_profiles() -> list:
    out = []
    for p in (load_all().get("profiles") or []):
        out.append({"name": p.get("name"), "created": p.get("created"),
                    "body_count": p.get("body_count"),
                    "categories": len(p.get("categories") or [])})
    return out


# ============================================================================
#  匹配
# ============================================================================
def plan_for(bodies: list, profile: dict) -> dict:
    """把当前模型的体匹配到记忆里的类别，返回 {类别: [体名]} 与未匹配清单"""
    cats = profile.get("categories") or []
    if not cats:
        return {"ok": False, "error": "记忆里没有类别"}

    ys = [float(b.get("cy") or 0) for b in bodies] or [0.0]
    ymin, ymax = min(ys), max(ys)

    assign = {}
    unmatched = []
    details = []
    for b in bodies:
        f = body_features(b, ymin, ymax)
        if not f:
            unmatched.append(b["name"])
            continue
        best, best_d = None, 1e9
        for c in cats:
            for s in (c.get("samples") or []):
                d = _dist(f, s)
                if d < best_d:
                    best_d, best = d, c["name"]
        if best is not None and best_d <= DIST_THRESHOLD:
            assign.setdefault(best, []).append(b["name"])
            details.append({"body": b["name"], "category": best,
                            "distance": round(best_d, 3)})
        else:
            unmatched.append(b["name"])

    plan = []
    for c in cats:
        names = assign.get(c["name"]) or []
        if not names:
            continue
        plan.append({"category": c["name"],
                     "size": c.get("size"),
                     "method": c.get("method") or "Automatic",
                     "bodies": names})
    return {"ok": True, "plan": plan, "unmatched": unmatched,
            "matched_bodies": sum(len(p["bodies"]) for p in plan),
            "total_bodies": len(bodies), "details": details[:200]}
