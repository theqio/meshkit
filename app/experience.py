# -*- coding: utf-8 -*-
"""
experience.py —— 经验库：记录"什么样的问题 + 做了什么 + 效果如何"，下次给建议

核心思想（可积累、可验证）：
  1. 每次网格化前后，对比失败体集合：
       失败 -> 成功  = 这次改动有效（记为 fixed）
       一直失败      = 无效（记为 no_effect）
  2. 把"体"的几何特征（壁厚/长细比/填充率/面数）+ 当时采取的动作用一个案例存下来
  3. 新模型里遇到特征相近的体，用这些案例给出建议，并按成功率排序

存储：app/experience.json
"""
from __future__ import annotations

import json
import os
import time

EXP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "experience.json")


def _load() -> dict:
    if not os.path.isfile(EXP_PATH):
        return {"cases": [], "version": 1}
    try:
        with open(EXP_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("cases", [])
        return d
    except Exception:
        return {"cases": [], "version": 1}


def _save(d: dict) -> None:
    # 只保留最近 3000 条，避免无限增长
    cases = d.get("cases") or []
    if len(cases) > 3000:
        cases = sorted(cases, key=lambda c: c.get("ts") or "")[-3000:]
    d["cases"] = cases
    with open(EXP_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- 特征
def features(body: dict) -> dict | None:
    dims = sorted([body.get("lx") or 0, body.get("ly") or 0, body.get("lz") or 0])
    if dims[0] <= 0:
        return None
    d1, d2, d3 = dims
    bbox = d1 * d2 * d3 or 1.0
    fill = max(0.0, min(1.0, (body.get("vol") or 0) / bbox))
    if fill >= 0.6:
        wall = d1
    else:
        wall = 0.5 * d2 * (1.0 - max(0.0, 1.0 - fill) ** 0.5)
        wall = max(0.3, min(wall, d1))
    return {"wall": round(wall, 2), "fill": round(fill, 3),
            "aspect": round((d3 / d1) if d1 else 0, 1),
            "hollow": fill < 0.6,
            "dims": [round(x, 1) for x in dims]}


def _same_shape(a: dict, b: dict) -> float:
    """相似度 0~1（越大越像）"""
    try:
        if bool(a.get("hollow")) != bool(b.get("hollow")):
            return 0.0
        # 壁厚按对数比
        wa, wb = max(a["wall"], 0.1), max(b["wall"], 0.1)
        wr = min(wa, wb) / max(wa, wb)
        # 长细比
        aa, ab = max(a["aspect"], 1), max(b["aspect"], 1)
        ar = min(aa, ab) / max(aa, ab)
        # 填充率差
        fr = 1.0 - min(1.0, abs(a["fill"] - b["fill"]) / 0.6)
        return round(0.5 * wr + 0.3 * ar + 0.2 * fr, 3)
    except Exception:
        return 0.0


# ---------------------------------------------------------------- 记录
def case_key(feat: dict, problem: str, action: dict) -> str:
    """去重键：几何按粗粒度分箱 + 动作 + 问题类型
       相近的几何（壁厚差 <15%、长细比同档）视为同一种情况
    """
    def _bin(v, step):
        try:
            return int(round(float(v) / step))
        except Exception:
            return 0
    wall_b = _bin(feat.get("wall"), 0.5)          # 0.5mm 一档
    asp_b = _bin(min(feat.get("aspect") or 0, 200), 5)  # 长细比 5 一档
    return "|".join([
        str(wall_b), str(asp_b),
        "H" if feat.get("hollow") else "S",
        (problem or "").lower(),
        json.dumps(action or {}, sort_keys=True, ensure_ascii=False),
    ])


def add_case(feat: dict, problem: str, action: dict, outcome: str,
             body: str = "", group: str = "", model: str = "",
             note: str = "") -> dict:
    """记录一条经验；相同情况只累加计数，不新增重复记录
       与已有记录结论冲突时（之前无效、这次有效），以新结论为准并累加
    """
    d = _load()
    key = case_key(feat, problem, action)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for c in d["cases"]:
        if c.get("key") == key:
            c["count"] = int(c.get("count") or 1) + 1
            c["last_ts"] = now
            if c.get("outcome") != outcome:
                c["outcome"] = outcome        # 新证据覆盖旧结论
                c["note"] = note or c.get("note", "")
            if body:
                c["body"] = body
            if group:
                c["group"] = group
            _save(d)
            return c
    case = {"key": key, "ts": now, "last_ts": now,
            "count": 1, "body": body, "group": group, "model": model,
            "problem": problem, "action": action,
            "outcome": outcome, "note": note,
            "features": feat}
    d["cases"].append(case)
    _save(d)
    return case


def record_batch(pairs: list, action: dict, model: str = "") -> int:
    """pairs: [{"body":..,"group":..,"problem":..,"outcome":..,"features":..}]"""
    n = 0
    for p in pairs:
        if not p.get("features"):
            continue
        add_case(p["features"], p.get("problem") or "mesh_failed", action,
                 p.get("outcome") or "fixed", p.get("body", ""),
                 p.get("group", ""), model, p.get("note", ""))
        n += 1
    return n


# ---------------------------------------------------------------- 匹配
def advise(feat: dict, limit: int = 5, min_sim: float = 0.72) -> list:
    """给定体特征，返回历史建议（按成功率排序）"""
    d = _load()
    buckets = {}
    for c in d.get("cases") or []:
        f = c.get("features") or {}
        sim = _same_shape(feat, f)
        if sim < min_sim:
            continue
        key = json.dumps(c.get("action") or {}, sort_keys=True, ensure_ascii=False)
        b = buckets.setdefault(key, {"action": c.get("action"),
                                     "fixed": 0, "no_effect": 0,
                                     "sim_sum": 0.0, "examples": []})
        _n = int(c.get("count") or 1)
        if c.get("outcome") == "fixed":
            b["fixed"] += _n
        else:
            b["no_effect"] += _n
        b["sim_sum"] += sim
        if len(b["examples"]) < 3:
            b["examples"].append({"body": c.get("body"),
                                  "group": c.get("group"),
                                  "model": c.get("model"),
                                  "outcome": c.get("outcome"),
                                  "sim": sim})
    out = []
    for b in buckets.values():
        total = b["fixed"] + b["no_effect"]
        rate = (b["fixed"] / total) if total else 0.0
        out.append({"action": b["action"],
                    "fixed": b["fixed"], "total": total,
                    "success_rate": round(rate, 2),
                    "avg_sim": round(b["sim_sum"] / max(1, total), 3),
                    "examples": b["examples"]})
    out.sort(key=lambda x: (-x["success_rate"], -x["total"], -x["avg_sim"]))
    return out[:limit]


def stats() -> dict:
    d = _load()
    cases = d.get("cases") or []
    by_problem, by_action = {}, {}
    fixed = no_effect = total = 0
    for c in cases:
        n = int(c.get("count") or 1)
        total += n
        p = c.get("problem") or "?"
        by_problem[p] = by_problem.get(p, 0) + n
        a = json.dumps(c.get("action") or {}, sort_keys=True, ensure_ascii=False)
        by_action[a] = by_action.get(a, 0) + n
        if c.get("outcome") == "fixed":
            fixed += n
        else:
            no_effect += n
    return {"total": total, "patterns": len(cases),
            "fixed": fixed, "no_effect": no_effect,
            "by_problem": by_problem,
            "top_actions": sorted(by_action.items(), key=lambda kv: -kv[1])[:8],
            "last": cases[-1].get("last_ts") if cases else None}
