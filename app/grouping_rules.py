# -*- coding: utf-8 -*-
"""
grouping_rules.py —— 用户分组逻辑的规则引擎

从用户实际分组中"学"出三级逻辑，并能应用到新模型：

  第一级 相同尺寸形状
      尺寸(容差 2%)一致且实心/空心一致 -> 同一组
      （用户：垫板 114 个 60x8x200 全在一组）

  第二级 相同构成形式（壳单元）
      面体（壳）按"面内尺度/朝向/位置"归组
      （用户：波纹板外壁、内隔断波纹板 各自成组）

  第三级 结构作用相同
      把语义类型映射到"结构角色"，同角色 + 同区域 + 同朝向 -> 同一组
      （用户：顶部支撑梁=顶部支撑梁+结构件；上下框架=上/下纵梁+上/下横梁）

用法：
    learn(profile)            -> 规则对象
    predict(bodies, rules)    -> {体名: 组名}
"""
from __future__ import annotations

import collections
import json
import os

TOL = 0.02           # 尺寸相同容差

# 语义类型 -> 结构角色（从用户分组里归纳，可扩展）
ROLE_MAP = {
    # 竖向承力
    "立柱": "竖向支撑", "角柱": "竖向支撑",
    # 水平梁系
    "横梁": "水平梁", "纵梁": "水平梁", "上梁": "水平梁", "连接结构": "水平梁",
    "底部横梁": "水平梁", "顶部支撑梁": "顶部梁系",
    # 框架
    "上下框架": "框架", "两侧隔断支撑架": "框架",
    # 面状件
    "板": "板件", "大三角板": "板件", "小三角支撑": "板件",
    "垫板": "板件", "薄板": "板件", "底板": "板件",
    "上_薄垫板": "板件", "下_薄垫板": "板件",
    "中间隔断板": "隔断板",
    "侧封板": "封板",
    "窗框架": "接口框", "百叶窗框": "接口框", "电缆口框": "接口框",
    "波纹板": "壳体板", "波纹墙": "壳体板", "隔板": "壳体板",
    "内隔断波纹板": "壳体板", "波纹板外壳": "壳体板",
    "角件": "角件",
}


def _role(base: str) -> str:
    b = (base or "").strip()
    if b in ROLE_MAP:
        return ROLE_MAP[b]
    for key, role in ROLE_MAP.items():
        if key and key in b:
            return role
    return b or "其它"


def features(body: dict, ymin: float, ymax: float) -> dict:
    dims = sorted([body.get("lx") or 0, body.get("ly") or 0, body.get("lz") or 0])
    d1, d2, d3 = dims
    bbox = (d1 * d2 * d3) or 1.0
    fill = (body.get("vol") or 0) / bbox if bbox else 0.0
    span = (ymax - ymin) or 1.0
    z = ((body.get("cy") or 0) - ymin) / span
    axes = sorted([("X", body.get("lx") or 0), ("Y", body.get("ly") or 0),
                   ("Z", body.get("lz") or 0)], key=lambda t: -t[1])
    return {"name": body.get("name"), "base": body.get("category") or "",
            "shell": bool(body.get("shell")),
            "zone": "下" if z < 0.34 else ("中" if z < 0.62 else "上"),
            "z": round(z, 3), "dims": tuple(round(x, 1) for x in dims),
            "r1": round(d1 / d3, 4) if d3 else 0.0,
            "r2": round(d2 / d3, 4) if d3 else 0.0,
            "fill": round(fill, 4), "orient": axes[0][0],
            "hollow": fill < 0.5}


def _same_size(a: tuple, b: tuple) -> bool:
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if max(x, y) <= 0:
            continue
        if abs(x - y) / max(x, y) > TOL:
            return False
    return True


def learn_from_labels(bodies: list, label: dict) -> dict:
    """直接从"体名 -> 组名"的标注里学规则（用户的真实分组）"""
    ys = [b.get("cy") or 0 for b in bodies] or [0]
    ymin, ymax = min(ys), max(ys)
    F = {b["name"]: features(b, ymin, ymax) for b in bodies}

    grouped = collections.defaultdict(list)
    for nm, g in (label or {}).items():
        if nm in F:
            grouped[g].append(F[nm])

    rules = {"groups": {}, "size_index": [], "role_index": {}}
    for g, fs in grouped.items():
        if not fs:
            continue
        size_clusters = collections.Counter(f["dims"] for f in fs)
        bases = collections.Counter(f["base"] for f in fs)
        zones = collections.Counter(f["zone"] for f in fs)
        shells = [f["shell"] for f in fs]
        roles = collections.Counter(_role(f["base"]) for f in fs)
        role = roles.most_common(1)[0][0]
        rules["groups"][g] = {
            "role": role,
            "zones": dict(zones),
            "sizes": [s for s, _ in size_clusters.items()],
            "shell": (shells.count(True) > shells.count(False)),
            "count": len(fs),
        }
        for s in size_clusters:
            rules["size_index"].append((s, g))
        for base, cnt in bases.items():
            rules["role_index"].setdefault(_role(base), collections.Counter())[g] += cnt

    # 同角色多组时，记住每组占比最高的区域
    for role in list(rules["role_index"]):
        rules["role_index"][role] = rules["role_index"][role].most_common(1)[0][0]
    return rules


def evaluate(bodies: list, label: dict, rules: dict) -> dict:
    """回测：预测 vs 标注"""
    pred = predict(bodies, rules)
    right, wrong, detail = 0, 0, []
    for nm, want in (label or {}).items():
        got = pred.get(nm)
        if got == want:
            right += 1
        else:
            wrong += 1
            if len(detail) < 12:
                detail.append((nm, want, got))
    return {"right": right, "wrong": wrong,
            "accuracy": round(100.0 * right / max(1, right + wrong), 2),
            "errors": detail}


def save_rules(rules: dict, path: str) -> None:
    def _ser(grp):
        out = {}
        for k, v in grp.items():
            out[k] = [list(map(float, s)) for s in v] if k == "sizes" else v
        return out

    data = {"groups": {g: _ser(grp) for g, grp in rules["groups"].items()},
            "size_index": [[list(map(float, s)), g] for s, g in rules["size_index"]],
            "role_index": rules["role_index"]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
def predict(bodies: list, rules: dict) -> dict:
    """返回 {体名: 组名}"""
    ys = [b.get("cy") or 0 for b in bodies] or [0]
    ymin, ymax = min(ys), max(ys)
    out = {}
    for b in bodies:
        f = features(b, ymin, ymax)
        name = f["name"]
        chosen = None
        # 第一级：尺寸形状完全一致
        for size, g in rules["size_index"]:
            if _same_size(f["dims"], size):
                grp = rules["groups"][g]
                if grp.get("shell") is None or grp["shell"] == f["shell"]:
                    chosen = g
                    break
        # 第二/三级：结构角色（+ 区域）
        if chosen is None:
            role = _role(f["base"])
            g = rules["role_index"].get(role)
            if g:
                # 同角色多组时，按区域挑
                cands = [(n, v) for n, v in rules["groups"].items()
                         if v["role"] == role]
                zone_hit = [n for n, v in cands
                            if f["zone"] in (v.get("zones") or {})]
                chosen = (zone_hit[0] if zone_hit
                          else (cands[0][0] if cands else g))
        out[name] = chosen
    return out
