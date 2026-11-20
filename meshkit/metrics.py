# -*- coding: utf-8 -*-
"""网格质量指标采集与判定。

三件事
------
1. ``collect``   : 逐个切换 Mesh Metric，读回 Min/Max/Avg/Std（读完还原原设置）。
2. ``limits``    : 读 Mechanical 自己配置的 warning/error 限值（按索引传参）。
3. ``judge/score``: 按阈值判定 PASS/WARN/FAIL，并给出可比较的综合得分。
4. ``sample_elements``: 可选单元级抽样（默认关闭），用于把问题定位到具体体。
"""
from __future__ import division

import math
import time

from . import compat
from . import config as config_mod

STATUS_PASS = u"PASS"
STATUS_WARN = u"WARN"
STATUS_FAIL = u"FAIL"
STATUS_SKIP = u"SKIP"


# --------------------------------------------------------------------------
# 指标统计
# --------------------------------------------------------------------------
def collect(session, log, cfg, names=None):
    """采集质量指标的统计量。返回 dict。

    names: 只采这些指标（None = 按配置决定，见 config.metric_names_to_collect）。
    """
    metric_cfg = config_mod.metric_config(cfg)
    wanted = list(names) if names else config_mod.metric_names_to_collect(cfg)
    original = session.mesh_metric_current()
    result = {"metrics": {}, "missing": [], "original": original,
              "restored": False, "seconds": 0.0, "requested": len(wanted)}
    started = time.time()

    for name in wanted:
        if name not in metric_cfg:
            result["missing"].append(name)
            continue
        if session.metric_enum(name) is None:
            result["missing"].append(name)
            continue
        if not session.set_mesh_metric(name):
            result["missing"].append(name)
            continue
        entry = _read_stats(session)
        entry["unit"] = metric_cfg[name].get("unit", "")
        result["metrics"][name] = entry
        log.debug(u"指标 %-26s min=%s max=%s avg=%s"
                  % (name, compat.fmt_num(entry.get("min")),
                     compat.fmt_num(entry.get("max")), compat.fmt_num(entry.get("avg"))))

    # 还原显示设置
    try:
        if original in (None, u"None", u""):
            session.set_mesh_metric(None)
            wanted = None
        else:
            session.set_mesh_metric(original)
            wanted = original
        current = session.mesh_metric_current()
        if wanted is None:
            result["restored"] = current in (None, u"None", u"")
        else:
            result["restored"] = current == wanted
    except Exception:
        pass
    result["seconds"] = time.time() - started
    if not result["metrics"]:
        log.warn(u"没有取到任何质量指标（MeshMetricType 不可用或网格为空）")
    return result


def _read_stats(session):
    entry = {"min": None, "max": None, "avg": None, "std": None}
    for key, attrs in (("min", ("Minimum", "Min")),
                       ("max", ("Maximum", "Max")),
                       ("avg", ("Average", "Avg", "Mean")),
                       ("std", ("StandardDeviation", "StdDev"))):
        for attr in attrs:
            value = session_float(compat.safe_get(session.mesh, attr))
            if value is not None:
                entry[key] = value
                break
    return entry


def session_float(value):
    if value is None:
        return None
    return compat.to_float(value)


def read_quality_limits(session, log):
    """读 Mechanical 的网格质量限值表。

    2022 R1 的 ``GetVolumeMeshQualityWarningLimit`` 需要 1 个索引参数，
    因此按 ``NumberOfMeshQualityMetrics`` 循环。
    """
    out = {"count": 0, "items": [], "error": None}
    count = int(compat.to_float(compat.safe_get(session.mesh, "NumberOfMeshQualityMetrics"), 0) or 0)
    out["count"] = count
    if count <= 0:
        out["error"] = u"NumberOfMeshQualityMetrics 不可用"
        return out
    for index in range(count):
        item = {"index": index, "name": None, "warn": None, "error": None,
                "active": None}
        for attr, key in (("GetVolumeMeshQualityName", "name"),
                          ("GetVolumeMeshQualityWarningLimit", "warn"),
                          ("GetVolumeMeshQualityErrorLimit", "error"),
                          ("GetVolumeMeshQuality", "active")):
            getter = compat.safe_get(session.mesh, attr)
            if not callable(getter):
                continue
            try:
                value = getter(index)
            except Exception:
                continue
            item[key] = compat.to_float(value) if key in ("warn", "error") else compat.to_text(value)
        if item["name"]:
            out["items"].append(item)
    log.debug(u"读取 Mechanical 质量限值 %d 项: %s"
              % (len(out["items"]),
                 u", ".join(u"%s[%s/%s]" % (i["name"], compat.fmt_num(i["warn"]), compat.fmt_num(i["error"]))
                            for i in out["items"][:6])))
    return out


# --------------------------------------------------------------------------
# 判定
# --------------------------------------------------------------------------
def _norm_key(name):
    """把 'Maximum Corner Angle' / 'Jacobian Ratio (MAPDL)' 归一成可比较的键。"""
    text = compat.to_text(name).lower()
    # 先去掉括号里的限定语，如 "(MAPDL)"
    cleaned = []
    depth = 0
    for ch in text:
        if ch in "([{":
            depth += 1
            continue
        if ch in ")]}":
            depth = max(0, depth - 1)
            continue
        if depth == 0:
            cleaned.append(ch)
    return u"".join(ch for ch in u"".join(cleaned) if ch.isalnum())


def effective_thresholds(cfg, limits):
    """把用户阈值与 Mechanical 限值合并；用户显式给值优先。

    Mechanical 的限值若读出来是 0/0（= 该版本没配置），视为"未配置"，不参与。
    """
    metric_cfg = config_mod.metric_config(cfg)
    by_name = {}
    for item in (limits or {}).get("items", []):
        key = _norm_key(item.get("name"))
        if key and key not in by_name:
            by_name[key] = item
    out = {}
    for name, entry in metric_cfg.items():
        warn, error = entry.get("warn"), entry.get("error")
        source = u"config"
        if cfg.get("quality", {}).get("use_mechanical_limits", True):
            mech = by_name.get(_norm_key(name))
            if mech is not None:
                mech_warn, mech_error = mech.get("warn"), mech.get("error")
                configured = not (mech_warn in (0, 0.0) and mech_error in (0, 0.0))
                if configured:
                    if warn is None and mech_warn is not None:
                        warn, source = mech_warn, u"Mechanical"
                    if error is None and mech_error is not None:
                        error, source = mech_error, u"Mechanical"
        out[name] = {"direction": entry["direction"], "warn": warn, "error": error,
                     "unit": entry.get("unit", ""), "source": source}
    return out


def judge(metrics, thresholds):
    """逐指标判定，返回 {name: {...}}。"""
    rows = {}
    for name, th in thresholds.items():
        stats = metrics.get(name) or {}
        direction = th.get("direction", "low")
        warn, error = th.get("warn"), th.get("error")
        status = STATUS_SKIP
        worst_value = None
        worst_kind = None
        severity_value = None

        if stats:
            if direction == "high":
                # 越大越好 -> 看最小值
                candidates = [(stats.get("min"), "min")]
            else:
                candidates = [(stats.get("max"), "max")]
            for value, kind in candidates:
                if value is None:
                    continue
                worst_value, worst_kind = value, kind
                break
        if worst_value is not None and warn is not None and error is not None:
            if direction == "high":
                status = STATUS_FAIL if worst_value <= error else (
                    STATUS_WARN if worst_value <= warn else STATUS_PASS)
            else:
                status = STATUS_FAIL if worst_value >= error else (
                    STATUS_WARN if worst_value >= warn else STATUS_PASS)
            severity_value = worst_value
        elif worst_value is not None:
            status = STATUS_PASS  # 没阈值 -> 不判错，报告里只列数值

        rows[name] = {
            "direction": direction, "warn": warn, "error": error,
            "status": status, "worst": worst_value, "worst_kind": worst_kind,
            "min": stats.get("min"), "max": stats.get("max"),
            "avg": stats.get("avg"), "std": stats.get("std"),
            "unit": th.get("unit", ""), "source": th.get("source"),
            "info_only": name in config_mod.INFO_ONLY_METRICS,
        }
        if rows[name]["info_only"]:
            rows[name]["status"] = STATUS_SKIP
    return rows


def count_status(rows):
    tally = {STATUS_PASS: 0, STATUS_WARN: 0, STATUS_FAIL: 0, STATUS_SKIP: 0}
    for row in rows.values():
        tally[row.get("status", STATUS_SKIP)] = tally.get(row.get("status", STATUS_SKIP), 0) + 1
    return tally


# --------------------------------------------------------------------------
# 综合得分
# --------------------------------------------------------------------------
def _exceedance(value, warn, error, direction):
    """把越界程度映射到 0..1：等于 warn -> 0，等于 error -> 1（可超过 1）。"""
    if value is None or warn is None or error is None:
        return 0.0
    try:
        warn = float(warn)
        error = float(error)
        value = float(value)
    except Exception:
        return 0.0
    span = abs(error - warn)
    if span <= 0:
        return 0.0
    if direction == "high":
        if value >= warn:
            return 0.0
        return min(3.0, (warn - value) / span)
    if value <= warn:
        return 0.0
    return min(3.0, (value - warn) / span)


def score(rows, snapshot, cfg):
    """越低越好的惩罚值 + 0..100 的展示得分。

    * 每个 WARN 指标按越界程度罚 1 分，FAIL 至少罚 3 分；
    * 单元数超出 hard_cap 罚 6 分，超出 target 按比例罚 0~2 分；
    * 完全没有网格 / 有体没划上网 单独重罚。
    """
    penalty = 0.0
    details = []
    for name, row in rows.items():
        if row.get("info_only"):
            continue
        status = row.get("status")
        if status not in (STATUS_WARN, STATUS_FAIL):
            continue
        severity = _exceedance(row.get("worst"), row.get("warn"), row.get("error"),
                               row.get("direction", "low"))
        weight = 3.0 if status == STATUS_FAIL else 1.0
        penalty += weight * (1.0 + severity)
        details.append(u"%s=%s(%s)" % (name, row.get("status"), compat.fmt_num(row.get("worst"))))

    budget = cfg.get("budget", {})
    basis = budget.get("basis", "elements")
    target = float(budget.get("target") or 0)
    cap = float(budget.get("hard_cap") or 0)
    count = float(snapshot.get(basis) or 0)
    if count <= 0:
        penalty += 10.0
        details.append(u"网格为空")
    elif cap and count > cap:
        penalty += 6.0 + 6.0 * (count - cap) / max(cap, 1.0)
        details.append(u"%s 超上限 %.2f 倍" % (basis, count / cap))
    elif target and count > target:
        penalty += 2.0 * (count - target) / max(target * 0.5, 1.0)
        details.append(u"%s 超目标 %.0f%%" % (basis, 100.0 * (count - target) / target))

    unmeshed = snapshot.get("unmeshed_bodies") or []
    if unmeshed:
        penalty += 2.0 + 0.5 * min(len(unmeshed), 10)
        details.append(u"%d 个体无网格" % len(unmeshed))

    if snapshot.get("error"):
        penalty += 5.0
        details.append(u"划网格过程报错")

    # 判定级别只看"硬伤"：FAIL 级指标 / 超上限 / 无网格 / 划网格报错；
    # 只有 warning 时一律算 WARN，避免"分数低但没硬伤"被误判成不达标。
    hard_fail = bool(
        any(row.get("status") == STATUS_FAIL for row in rows.values())
        or not count
        or (cap and count > cap)
        or unmeshed
        or snapshot.get("error"))
    if hard_fail:
        status = STATUS_FAIL
    elif penalty > 0.5:
        status = STATUS_WARN
    else:
        status = STATUS_PASS

    display = 100.0 / (1.0 + penalty)
    return {"penalty": penalty, "display": round(display, 1), "details": details,
            "status": status}


# --------------------------------------------------------------------------
# 可选：单元级抽样
# --------------------------------------------------------------------------
def sample_elements(session, log, cfg, max_elements=20000):
    """抽样计算单元"最长边/最短边"比，把最差的体挑出来。

    取数有三条路，逐条尝试（谁通用谁）：
      1. ``MeshData.GetElementEdges(elemId)`` -> 每条边的 Length（最直接）
      2. ``element.NodeIds`` / ``element.Nodes`` -> 节点坐标 -> 自己算边长
      3. ``MeshData.NodeIdsFromElementIds([elemId])`` -> 节点坐标 -> 自己算边长

    说明：这不是 Mechanical 的 AspectRatio（算法不同），只用于**相对排序**定位问题体。
    """
    out = {"ok": False, "error": None, "sampled": 0, "bodies": [], "seconds": 0.0,
           "method": None, "stride": 0}
    started = time.time()
    data = session.mesh_data()
    if data is None:
        out["error"] = u"MeshData 不可用"
        return out
    total = int(compat.to_float(compat.safe_get(data, "ElementCount"), 0) or 0)
    if total <= 0:
        out["error"] = u"单元数为 0"
        return out

    stride = max(1, int(total / max(1, max_elements)))
    out["stride"] = stride

    # 体名映射：把每个体的所有可用 id 都登记上
    id_to_name = {}
    body_rows = session.body_table()
    for row in body_rows:
        for candidate in (row.get("ids") or []):
            id_to_name[int(candidate)] = row["name"]

    worst_by_body = {}   # 体名 -> (max_ratio, element_id, topo)
    per_body_stats = {}  # 体名 -> {"count","sum","max"}
    unmatched = {}       # topoRefId -> (ratio, element_id)
    sampled = 0
    method_counts = {}
    buckets = {"gt5": 0, "gt10": 0, "gt20": 0, "gt40": 0}

    element_getter = compat.safe_get(data, "ElementById")
    if not callable(element_getter):
        out["error"] = u"MeshData 上没有 ElementById"
        return out

    for element_id in range(1, total + 1, stride):
        try:
            elem = element_getter(element_id)
        except Exception:
            continue
        lengths, method = _element_edge_lengths(data, elem, element_id)
        if not lengths:
            continue
        method_counts[method] = method_counts.get(method, 0) + 1
        shortest = min(lengths)
        if shortest <= 0:
            continue
        sampled += 1
        ratio = max(lengths) / shortest
        if ratio > 5:
            buckets["gt5"] += 1
        if ratio > 10:
            buckets["gt10"] += 1
        if ratio > 20:
            buckets["gt20"] += 1
        if ratio > 40:
            buckets["gt40"] += 1
        name, topo = _element_owner(elem, id_to_name)
        if name:
            best = worst_by_body.get(name)
            if best is None or ratio > best[0]:
                worst_by_body[name] = (ratio, element_id, topo)
            stats = per_body_stats.get(name)
            if stats is None:
                per_body_stats[name] = {"count": 1, "sum": ratio, "max": ratio}
            else:
                stats["count"] += 1
                stats["sum"] += ratio
                if ratio > stats["max"]:
                    stats["max"] = ratio
        elif topo is not None:
            best = unmatched.get(topo)
            if best is None or ratio > best[0]:
                unmatched[topo] = (ratio, element_id)

    rows = []
    for name, (ratio, element_id, topo) in worst_by_body.items():
        stats = per_body_stats.get(name) or {"count": 1, "sum": ratio, "max": ratio}
        rows.append({"geo_id": topo, "name": name, "edge_ratio": ratio,
                     "element_id": element_id, "sample_count": stats["count"],
                     "avg_ratio": stats["sum"] / max(stats["count"], 1)})
    for topo, (ratio, element_id) in unmatched.items():
        rows.append({"geo_id": topo, "name": u"<未匹配体> 面id=%s" % topo,
                     "edge_ratio": ratio, "element_id": element_id,
                     "sample_count": 1, "avg_ratio": ratio})
    rows.sort(key=lambda r: -r["edge_ratio"])

    # 用抽样比例外推总体：>10 / >20 / >40 各约多少单元
    distribution = []
    if sampled > 0:
        for key, label in (("gt10", u"边比 > 10"), ("gt20", u"边比 > 20"), ("gt40", u"边比 > 40")):
            share = buckets[key] / float(sampled)
            distribution.append({"label": label, "samples": buckets[key],
                                 "share": share, "estimate": int(round(share * total))})

    # 薄壁候选：平均边比高 + 样本够多 => 整片都扁，典型的"薄板用四面体"特征
    thin = [row for row in rows
            if row.get("sample_count", 0) >= 5 and row.get("avg_ratio", 0) >= 8.0]
    thin.sort(key=lambda r: -r.get("avg_ratio", 0))

    out.update({"ok": sampled > 0, "sampled": sampled, "bodies": rows[:50],
                "thin_candidates": thin[:40],
                "seconds": time.time() - started, "method": _dominant(method_counts),
                "matched_bodies": len(worst_by_body), "unmatched_faces": len(unmatched),
                "buckets": buckets, "distribution": distribution,
                "total_elements": total})
    if sampled == 0:
        out["error"] = u"抽样到 0 个可用单元（GetElementEdges / NodeIds / NodeIdsFromElementIds 都不可用）"
    return out


def _element_owner(elem, id_to_name):
    """返回 (体名 或 None, TopoRefId 或 None)。优先用 element.GetBody()。"""
    getter = compat.safe_get(elem, "GetBody")
    if callable(getter):
        try:
            value = getter()
        except Exception:
            value = None
        if value is not None:
            if isinstance(value, compat.integer_types):
                name = id_to_name.get(int(value))
                if name:
                    return name, None
            else:
                name = compat.safe_get(value, "Name")
                if name:
                    return compat.to_text(name), None
                inner = compat.safe_get(value, "InternalObject")
                number = compat.safe_get(inner, "Id") if inner is not None else None
                if isinstance(number, compat.integer_types) and int(number) in id_to_name:
                    return id_to_name[int(number)], None
    topo = compat.safe_get(elem, "TopoRefId")
    if isinstance(topo, compat.integer_types):
        return None, int(topo)
    return None, None


def _dominant(counts):
    if not counts:
        return None
    return sorted(counts.items(), key=lambda kv: -kv[1])[0][0]


def _element_edge_lengths(data, elem, element_id):
    """返回 (边长列表, 取数方式)。"""
    # 路径 1：MeshData.GetElementEdges
    getter = compat.safe_get(data, "GetElementEdges")
    if callable(getter):
        try:
            edges = getter(int(element_id))
        except Exception:
            edges = None
        lengths = []
        if edges is not None:
            try:
                for edge in edges:
                    length = compat.to_float(compat.safe_get(edge, "Length"))
                    if length and length > 0:
                        lengths.append(length)
            except Exception:
                lengths = []
        if len(lengths) >= 6:
            return lengths, u"GetElementEdges"

    # 路径 2/3：节点 id -> 坐标
    node_ids = _element_node_ids(data, elem, element_id)
    coords = _node_coords(data, node_ids)
    lengths = _edge_lengths_from_coords(coords)
    if len(lengths) >= 6:
        return lengths, u"NodeIds+NodeXYZ"
    return [], None


def _element_node_ids(data, elem, element_id):
    # 2a) 单元对象自带
    for attr in ("NodeIds", "Nodes", "NodeIdList"):
        value = compat.safe_get(elem, attr)
        if value is None:
            continue
        ids = []
        try:
            for item in value:
                if isinstance(item, compat.integer_types):
                    ids.append(int(item))
                else:
                    node_id = compat.safe_get(item, "Id")
                    if node_id is not None:
                        ids.append(int(node_id))
        except Exception:
            continue
        if ids:
            return ids
    # 2b) MeshData.NodeIdsFromElementIds([elemId])
    getter = compat.safe_get(data, "NodeIdsFromElementIds")
    if callable(getter):
        try:
            value = getter([int(element_id)])
        except Exception:
            value = None
        if value is not None:
            ids = []
            try:
                for item in value:
                    if isinstance(item, compat.integer_types):
                        ids.append(int(item))
            except Exception:
                ids = []
            if ids:
                return ids
    return []


def _node_coords(data, node_ids):
    getter = compat.safe_get(data, "NodeXYZById")
    by_id = compat.safe_get(data, "NodeById")
    coords = []
    for node_id in node_ids:
        point = None
        if callable(getter):
            try:
                point = getter(int(node_id))
            except Exception:
                point = None
        if point is None and callable(by_id):
            try:
                node = by_id(int(node_id))
                point = node
            except Exception:
                point = None
        if point is None:
            continue
        triple = _xyz(point)
        if triple is not None:
            coords.append(triple)
    return coords


def _xyz(point):
    try:
        return (float(point.X), float(point.Y), float(point.Z))
    except Exception:
        pass
    try:
        return (float(point[0]), float(point[1]), float(point[2]))
    except Exception:
        pass
    for names in (("Item1", "Item2", "Item3"), ("x", "y", "z")):
        try:
            return (float(getattr(point, names[0])), float(getattr(point, names[1])),
                    float(getattr(point, names[2])))
        except Exception:
            continue
    return None


def _edge_lengths_from_coords(coords):
    """角点间边长（二阶单元只用前 8 个角点）。"""
    used = coords[:8]
    lengths = []
    for i in range(len(used)):
        for j in range(i + 1, len(used)):
            dx = used[i][0] - used[j][0]
            dy = used[i][1] - used[j][1]
            dz = used[i][2] - used[j][2]
            length = math.sqrt(dx * dx + dy * dy + dz * dz)
            if length > 0:
                lengths.append(length)
    return lengths


def _element_owner_id(elem, ref_map):
    """（保留给旧调用）从单元对象上取参考 id；取不到返回 None。"""
    for attr in ("RefId", "ReferenceId", "BodyId", "GeoId"):
        value = compat.safe_get(elem, attr)
        if isinstance(value, compat.integer_types):
            return int(value)
    return None
