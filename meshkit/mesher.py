# -*- coding: utf-8 -*-
"""网格构建与"现场快照"。

职责
----
1. ``apply_requirements`` : 把需求配置落成 Mechanical 设置（全局 + 局部控制）。
2. ``generate``           : 划网格，带"空转检测 + 清空重试"。
3. ``measure``            : 一次性采集评价所需的全部数据（单元/节点/指标/体/几何）。
4. ``deep_geometry``      : 需要时才做的碎边定位扫描（较慢）。
"""
from __future__ import division

import time
import traceback

from . import compat
from . import controls as controls_mod
from . import metrics as metrics_mod


# --------------------------------------------------------------------------
# 1) 应用需求
# --------------------------------------------------------------------------
def apply_requirements(session, cfg, log, body_rows=None):
    """把 cfg 写进 Mechanical。返回报告 dict。"""
    started = time.time()
    log.info(u"应用网格需求：全局 %s mm，局部控制 %d 项"
             % (compat.fmt_num(cfg["mesh"]["element_size_mm"]), len(cfg.get("controls") or [])))
    body_rows = body_rows if body_rows is not None else session.body_table()

    global_actions, global_failures = controls_mod.apply_global(session, cfg, log)
    for action in global_actions:
        log.debug(u"  全局 %s: %s -> %s %s"
                  % (action.get("attr"), compat.fmt_num(action.get("old")),
                     compat.fmt_num(action.get("new")),
                     u"" if not action.get("verified") else u"(已校验)"))
    for failure in global_failures:
        log.warn(u"  全局设置未生效: %s (%s)"
                 % (failure.get("target"), failure.get("error")))

    controls_report = controls_mod.apply_specs(session, cfg, body_rows, log)
    if controls_report["created"]:
        log.info(u"  新建控制项: %s" % u", ".join(controls_report["created"]))
    if controls_report["updated"]:
        log.info(u"  更新控制项: %s" % u", ".join(controls_report["updated"]))
    if controls_report["missing"]:
        log.warn(u"  缺失且未新建: %s" % u", ".join(controls_report["missing"]))
    if controls_report["unmatched"]:
        log.warn(u"  体名匹配为空: %s（请检查 bodies 通配符）"
                 % u", ".join(controls_report["unmatched"]))
    for failure in controls_report["failures"]:
        log.warn(u"  控制项设置失败: %s (%s)"
                 % (failure.get("target"), failure.get("error")))

    session.invalidate_caches()
    return {
        "global_actions": global_actions,
        "global_failures": global_failures,
        "controls": controls_report,
        "seconds": time.time() - started,
    }


# --------------------------------------------------------------------------
# 2) 划网格
# --------------------------------------------------------------------------
def generate(session, cfg, log, round_index=1):
    """划网格 + 空转重试。返回 {ok, attempts, error, elements, nodes, seconds}"""
    started = time.time()
    outcome = {"ok": False, "attempts": [], "error": None,
               "elements": 0, "nodes": 0, "seconds": 0.0}
    before_elements = session.element_count()
    before_nodes = session.node_count()
    log.info(u"开始划网格（第 %d 轮，当前 %s 单元 / %s 节点）"
             % (round_index, compat.fmt_num(before_elements), compat.fmt_num(before_nodes)))

    result = session.generate_mesh(clear_first=False)
    outcome["attempts"].append(_attempt_dict(result, u"直接生成"))
    log.info(u"  第 1 次: %s，耗时 %s，单元 %s / 节点 %s"
             % (u"成功" if result["ok"] else u"失败", compat.elapsed_text(result["seconds"]),
                compat.fmt_num(result["elements"]), compat.fmt_num(result["nodes"])))

    unchanged = (result["elements"] == before_elements and before_elements > 0)
    needs_retry = (not result["ok"]) or result["elements"] <= 0 or unchanged
    if needs_retry:
        reason = result["error"] or (
            u"单元数未变化（GenerateMesh 空转）" if unchanged else u"单元数为 0")
        log.warn(u"  触发清空重试，原因: %s" % compat.to_text(reason).strip().splitlines()[-1:])
        result2 = session.generate_mesh(clear_first=True)
        outcome["attempts"].append(_attempt_dict(result2, u"清空后重新生成"))
        log.info(u"  第 2 次: %s，耗时 %s，单元 %s / 节点 %s"
                 % (u"成功" if result2["ok"] else u"失败", compat.elapsed_text(result2["seconds"]),
                    compat.fmt_num(result2["elements"]), compat.fmt_num(result2["nodes"])))
        if result2["ok"] and result2["elements"] > 0:
            result = result2
        else:
            if not result["ok"]:
                outcome["error"] = result["error"]
            else:
                outcome["error"] = result2["error"] or u"重试后仍无单元"

    outcome["ok"] = bool(result["ok"]) and result["elements"] > 0
    outcome["elements"] = result["elements"]
    outcome["nodes"] = result["nodes"]
    outcome["state"] = result.get("state")
    if not outcome["ok"] and not outcome["error"]:
        outcome["error"] = u"网格生成未成功，且未获得 Mechanical 的报错文本"
    outcome["seconds"] = time.time() - started
    return outcome


def _attempt_dict(result, label):
    return {"label": label, "ok": result.get("ok"), "error": result.get("error"),
            "elements": result.get("elements"), "nodes": result.get("nodes"),
            "seconds": result.get("seconds")}


# --------------------------------------------------------------------------
# 3) 现场快照
# --------------------------------------------------------------------------
def measure(session, cfg, log, round_index=1, need_metrics=True):
    """采集一次完整评价数据。"""
    started = time.time()
    session.invalidate_caches()
    snapshot = {
        "round": round_index,
        "elements": session.element_count(),
        "nodes": session.node_count(),
        "state": session.mesh_state(),
        "budget": dict(cfg.get("budget") or {}),
        "error": None,
        "metrics": {},
        "limits": {},
        "thresholds": {},
        "rows": {},
        "tally": {},
        "score": {},
        "controls": [],
        "control_stats": {},
        "bodies": [],
        "unmeshed_bodies": [],
        "geometry": {},
        "seconds": {},
    }

    body_rows = session.body_table()
    snapshot["bodies"] = [{"name": row["name"], "leaf": row["leaf"], "geo_id": row["geo_id"],
                           "elements": row["elements"], "suppressed": row["suppressed"],
                           "thickness_mm": row["thickness_mm"]} for row in body_rows]
    # 只有"确实拿到了每体单元数"时才敢判"没划上网"，否则宁可不下结论
    per_body_known = any(row.get("elements") is not None for row in body_rows)
    snapshot["per_body_known"] = per_body_known
    if per_body_known:
        snapshot["unmeshed_bodies"] = [row["name"] for row in body_rows
                                       if not row["suppressed"]
                                       and row.get("elements") is not None
                                       and row["elements"] <= 0]
    else:
        snapshot["unmeshed_bodies"] = []
        log.warn(u"取不到每体单元数（ElementIdsPerRefId / MeshDataByName 都失败），"
                 u"本轮不判断『哪些体没划上网』")

    control_rows = controls_mod.list_controls(session)
    snapshot["controls"] = [{"name": row["name"], "category": row["category"],
                             "type": row["type"], "size_mm": row["size_mm"],
                             "method": row["method"], "suppressed": row["suppressed"],
                             "body_count": row["body_count"]} for row in control_rows]
    snapshot["control_stats"] = controls_mod.control_size_stats(control_rows, cfg)

    min_edge, min_edge_source = session.min_geometry_edge_mm()
    snapshot["geometry"] = {
        "min_edge_mm": min_edge,
        "min_edge_source": min_edge_source,
        "bbox_diag_mm": session.geometry_bounds(),
        "global_size_mm": compat.to_float(cfg["mesh"]["element_size_mm"]),
        "shell_bodies": [row["name"] for row in body_rows if (row["thickness_mm"] or 0) > 0],
    }

    timings = {}
    if need_metrics and snapshot["elements"] > 0:
        t0 = time.time()
        collected = metrics_mod.collect(session, log, cfg)
        timings["metrics"] = time.time() - t0
        snapshot["metrics"] = collected["metrics"]
        snapshot["metrics_missing"] = collected.get("missing", [])
        t0 = time.time()
        snapshot["limits"] = metrics_mod.read_quality_limits(session, log)
        timings["limits"] = time.time() - t0
        snapshot["thresholds"] = metrics_mod.effective_thresholds(cfg, snapshot["limits"])
        snapshot["rows"] = metrics_mod.judge(snapshot["metrics"], snapshot["thresholds"])
        snapshot["tally"] = metrics_mod.count_status(snapshot["rows"])
        snapshot["score"] = metrics_mod.score(snapshot["rows"], snapshot, cfg)
    else:
        snapshot["score"] = metrics_mod.score({}, snapshot, cfg)

    timings["total"] = time.time() - started
    snapshot["seconds"] = timings
    return snapshot


def deep_geometry(session, snapshot, cfg, log, limit=10, max_scan=20000):
    """扫几何小边，定位碎边来源体（只在需要时调用，可能较慢）。"""
    global_size = compat.to_float(cfg["mesh"]["element_size_mm"], 1.0) or 1.0
    min_edge = snapshot.get("geometry", {}).get("min_edge_mm")
    threshold = max(global_size * 0.02, 1e-6)
    if min_edge is not None and min_edge > threshold:
        snapshot["geometry"]["edge_scan"] = {"ok": True, "skipped": True,
                                            "reason": u"最小边长 %.4g mm 未小于阈值 %.4g mm"
                                                      % (min_edge, threshold)}
        return snapshot["geometry"]["edge_scan"]
    started = time.time()
    scan = session.scan_small_edges(threshold_mm=threshold, limit=limit, max_scan=max_scan)
    scan["threshold_mm"] = threshold
    scan["seconds"] = time.time() - started
    snapshot["geometry"]["edge_scan"] = scan
    if scan.get("hits"):
        log.info(u"几何碎边扫描：%d 条边 < %.4g mm，示例 %s"
                 % (len(scan["hits"]), threshold,
                    u"; ".join(u"%s(%.3g mm)" % (h.get("owner") or h.get("name"), h.get("length_mm"))
                               for h in scan["hits"][:3])))
    return scan


def summarize_counts(snapshot):
    return u"%s 单元 / %s 节点（%s）" % (
        compat.fmt_num(snapshot.get("elements")), compat.fmt_num(snapshot.get("nodes")),
        compat.to_text(snapshot.get("state")))
