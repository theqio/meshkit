# -*- coding: utf-8 -*-
"""geo_recognize.py —— 纯几何识别零部件（不看名字）

规则顺序：
  规则3 朝向判板：X 最薄且 YZ 大 → 中隔断薄板；Y 最薄且 XZ 大 → 底板
  规则1 装配体：相互接触的连通分量，含已知上支撑梁尺寸且在上层 → 上支撑梁
  规则2 上梁：与纵梁接触 → 再扩散到与这些上梁接触的；剩余同类 → 横梁
  规则4 侧固定件：尺寸 50×200×209 且接触壳体
  尺寸表：其它按 尺寸签名+填充率 命中
  兜底：其他

支持人工覆盖（overrides.json）：{体名: 部位名}
"""
from __future__ import annotations

import json
import math
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(HERE, "part_rules.json")
OVERRIDE_PATH = os.path.join(HERE, "group_overrides.json")
CUSTOM_PATH = os.path.join(HERE, "custom_groups.json")
UNDO_PATH = os.path.join(HERE, "group_undo.json")

TOL = 3.0  # 接触容差 mm


def _load_json(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(p, data):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def _dims(b):
    return sorted([round(b.get("lx") or 0), round(b.get("ly") or 0),
                   round(b.get("lz") or 0)])


def _sig(b):
    d = _dims(b)
    return "%dx%dx%d" % (d[0], d[1], d[2])


# ---- 尺寸表最近邻匹配（带容差）----
_SIG_CACHE = {}


def _parse_sig(sig):
    try:
        parts = sig.split("x")
        if len(parts) != 3:
            return None
        return (float(parts[0]), float(parts[1]), float(parts[2]))
    except Exception:
        return None


def _match_size(dims, table, axis_tol=0.20, total_tol=0.15):
    """dims 已排序；table: [(sig_dims, label)]
       返回 (label, cost) 或 (None, None)
    """
    best = (None, None)
    for sig_dims, lab in table:
        if sig_dims is None:
            continue
        cost = 0.0
        ok = True
        for x, y in zip(dims, sig_dims):
            if y <= 0:
                ok = False
                break
            c = abs(x - y) / y
            if c > axis_tol:
                ok = False
                break
            if c > cost:
                cost = c
        if not ok or cost > total_tol:
            continue
        if best[1] is None or cost < best[1]:
            best = (lab, cost)
    return best


def _box(b):
    cx, cy, cz = b.get("cx") or 0, b.get("cy") or 0, b.get("cz") or 0
    hx = (b.get("lx") or 0) / 2.0
    hy = (b.get("ly") or 0) / 2.0
    hz = (b.get("lz") or 0) / 2.0
    return (cx - hx, cx + hx, cy - hy, cy + hy, cz - hz, cz + hz)


def _gap(a, b):
    gx = max(0.0, max(a[0], b[0]) - min(a[1], b[1]))
    gy = max(0.0, max(a[2], b[2]) - min(a[3], b[3]))
    gz = max(0.0, max(a[4], b[4]) - min(a[5], b[5]))
    return math.sqrt(gx * gx + gy * gy + gz * gz)


def recognize(bodies, extra=None):
    """bodies: list_bodies 返回的体列表 → {部位: [体名]} + 置信度信息
       extra: {类别: [尺寸签名]} 额外规则（来自勾选的知识包）
    """
    # 被抑制的体也要纳入识别（否则会显示不全），只是标记出来
    bodies = list(bodies or [])
    names = [b.get("name") for b in bodies]
    dmap = {b.get("name"): b for b in bodies}
    box = {b.get("name"): _box(b) for b in bodies}

    # ---- 接触图 ----
    adj = {n: set() for n in names}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            if _gap(box[a], box[b]) <= TOL:
                adj[a].add(b)
                adj[b].add(a)

    def component(seed, pool=None):
        seen = {seed}
        stack = [seed]
        while stack:
            cur = stack.pop()
            for nb in adj[cur]:
                if nb in seen:
                    continue
                if pool is not None and nb not in pool:
                    continue
                seen.add(nb)
                stack.append(nb)
        return seen

    rules = _load_json(RULES_PATH, {}).get("rules", {})
    sig_table = []
    for lab, info in rules.items():
        for s in (info.get("sizes") or []):
            sd = _parse_sig(s.get("sig") or "")
            if sd:
                sig_table.append((sd, lab))

    # 叠加知识包规则（勾选的常用配置里的分组几何特征）
    if extra:
        for lab, sigs in (extra or {}).items():
            for s in sigs or []:
                sd = _parse_sig(s)
                if sd:
                    sig_table.append((sd, lab))

    pred = {}

    # ---- 规则3 朝向 ----
    for b in bodies:
        n = b["name"]
        lx, ly = b.get("lx") or 0, b.get("ly") or 0
        lz = b.get("lz") or 0
        if 0 < lx < 12 and ly > 400 and lz > 400:
            pred[n] = "中隔断薄板"
        elif 0 < ly < 12 and lx > 400 and lz > 400:
            pred[n] = "底板"

    # ---- 规则1 上支撑梁（连通装配体）----
    support_sizes = {"50x50x100", "55x80x150", "58x100x100",
                     "3x48x98", "10x100x200", "50x100x2594"}
    seeds = [n for n in names
             if _sig(dmap[n]) in support_sizes and (dmap[n].get("cy") or 0) > 6664]
    support = set()
    for s in seeds:
        comp = component(s)
        if len(comp) >= 5 and all((dmap[c].get("cy") or 0) > 6000 for c in comp):
            support |= comp
    for n in support:
        pred[n] = "上支撑梁"

    # ---- 规则2 上梁 / 横梁 ----
    beam_sizes = {"100x150x2194", "100x200x2114"}
    long_sizes = {"100x150x11868", "100x200x11836"}
    longs = [n for n in names if _sig(dmap[n]) in long_sizes]
    beams = set(n for n in names if _sig(dmap[n]) in beam_sizes)
    pool = beams | set(longs)
    upper = set()
    for n in longs:
        for nb in adj[n]:
            if nb in beams:
                upper |= component(nb, pool=pool)
    for n in (upper & beams):
        if n not in pred:
            pred[n] = "上梁"
    for n in beams:
        if n not in pred:
            pred[n] = "横梁"

    # ---- 规则4 侧固定件 ----
    wave = set(n for n in names if dmap[n].get("shell"))
    for n in names:
        if n in pred:
            continue
        if _sig(dmap[n]) == "50x200x209":
            if any(nb in wave for nb in adj[n]):
                pred[n] = "侧固定件"

    # ---- 储罐：三向都大 + 接近立方体 + 空心 ----
    for n in names:
        if n in pred:
            continue
        b = dmap[n]
        d = _dims(b)
        if d[0] < 1500:
            continue
        ratio = d[2] / max(1.0, d[0])
        if ratio > 2.6:
            continue
        bbox = max(1.0, d[0] * d[1] * d[2])
        fill = (b.get("vol") or 0) / bbox
        if 0.05 <= fill <= 0.55:
            pred[n] = "储罐"

    # ---- 尺寸表（最近邻 + 容差）----
    for n in names:
        if n in pred:
            continue
        d = _dims(dmap[n])
        if d[0] <= 0:
            continue
        lab, cost = _match_size(d, sig_table)
        if lab:
            pred[n] = lab

    # ---- 兜底 ----
    for n in names:
        pred.setdefault(n, "其他")

    # ---- 人工覆盖 ----
    ov = _load_json(OVERRIDE_PATH, {})
    for n, lab in ov.items():
        if n in pred:
            pred[n] = lab

    groups = {}
    for n, lab in pred.items():
        groups.setdefault(lab, []).append(n)
    # 自定义分组（可能为空，也要显示）
    for g in _load_json(CUSTOM_PATH, []):
        groups.setdefault(g, [])
    return {"ok": True, "total": len(names), "groups": groups,
            "group_count": len(groups)}


def set_override(body, to_group):
    ov = _load_json(OVERRIDE_PATH, {})
    if to_group:
        ov[body] = to_group
    else:
        ov.pop(body, None)
    _save_json(OVERRIDE_PATH, ov)
    return ov


def clear_overrides():
    _save_json(OVERRIDE_PATH, {})
    return {}


def rename_group(old, new, groups):
    """把某一组的体全部改成新组名（写入覆盖表）"""
    if not old or not new:
        return {"ok": False, "error": "组名不能为空"}
    ov = _load_json(OVERRIDE_PATH, {})
    members = (groups or {}).get(old) or []
    for n in members:
        ov[n] = new
    _save_json(OVERRIDE_PATH, ov)
    # 自定义分组表里也改名
    cust = _load_json(CUSTOM_PATH, [])
    cust = [new if x == old else x for x in cust]
    if new not in cust:
        cust.append(new)
    _save_json(CUSTOM_PATH, cust)
    return {"ok": True, "moved": len(members)}


def add_group(name):
    if not name:
        return {"ok": False, "error": "组名不能为空"}
    cust = _load_json(CUSTOM_PATH, [])
    if name not in cust:
        cust.append(name)
    _save_json(CUSTOM_PATH, cust)
    return {"ok": True, "groups": cust}


def delete_group(name, groups):
    """删除分组：其成员归到「其他」，并移出自定义分组表"""
    if not name:
        return {"ok": False, "error": "组名不能为空"}
    ov = _load_json(OVERRIDE_PATH, {})
    members = (groups or {}).get(name) or []
    for n in members:
        ov[n] = "其他"
    _save_json(OVERRIDE_PATH, ov)
    cust = _load_json(CUSTOM_PATH, [])
    was_custom = name in cust
    cust = [x for x in cust if x != name]
    _save_json(CUSTOM_PATH, cust)
    # 记录撤销信息（最多保留 20 次）
    undo = _load_json(UNDO_PATH, [])
    undo.append({"name": name, "bodies": members, "was_custom": was_custom,
                 "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
    undo = undo[-20:]
    _save_json(UNDO_PATH, undo)
    return {"ok": True, "moved": len(members), "to": "其他",
            "undo_count": len(undo)}


def restore_last():
    """还原最近一次删除的分组"""
    undo = _load_json(UNDO_PATH, [])
    if not undo:
        return {"ok": False, "error": "没有可还原的删除记录"}
    last = undo.pop()
    _save_json(UNDO_PATH, undo)
    name = last.get("name")
    bodies = last.get("bodies") or []
    ov = _load_json(OVERRIDE_PATH, {})
    for n in bodies:
        ov[n] = name          # 重新归回原组
    _save_json(OVERRIDE_PATH, ov)
    if last.get("was_custom"):
        cust = _load_json(CUSTOM_PATH, [])
        if name not in cust:
            cust.append(name)
        _save_json(CUSTOM_PATH, cust)
    return {"ok": True, "restored": name, "count": len(bodies),
            "remaining_undo": len(undo)}


def remap_overrides(mapping):
    """体名变化后，同步更新覆盖表的键（old->new）"""
    if not mapping:
        return {"ok": True, "moved": 0}
    ov = _load_json(OVERRIDE_PATH, {})
    new_ov = {}
    moved = 0
    for k, lab in ov.items():
        if k in mapping:
            new_ov[mapping[k]] = lab
            moved += 1
        else:
            new_ov.setdefault(k, lab)
    _save_json(OVERRIDE_PATH, new_ov)
    return {"ok": True, "moved": moved}


def prune_overrides(current_names):
    """清掉覆盖表里指向已不存在体名的条目"""
    ov = _load_json(OVERRIDE_PATH, {})
    stale = [k for k in ov if k not in current_names]
    for k in stale:
        ov.pop(k)
    if stale:
        _save_json(OVERRIDE_PATH, ov)
    return {"kept": len(ov), "dropped": len(stale)}

