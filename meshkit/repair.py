# -*- coding: utf-8 -*-
"""自动纠正层：**只改网格设置**（尺寸/方法/平滑/曲率/defeature/近距捕捉）。

设计原则
--------
* 每个动作独立、幂等、写后校验；
* 一步一阶梯（一轮只推进一步），保证"改了什么 → 结果怎么变"可归因；
* 动作全部可回滚（engine 持有本轮之前的快照）；
* 自动做不到的事（改几何、改模型）**绝不偷偷做**，交给 advisor 出人工清单。
"""
from __future__ import division

import traceback

from . import compat
from . import controls as controls_mod
from . import diagnose as diagnose_mod

ACTION_ORDER = [
    "A_CLEAR_REMESH",
    "A_SMOOTHING_HIGH",
    "A_CURVATURE_ON",
    "A_FIX_ZERO_SIZE",
    "A_FIX_CONTROL_SCOPE",
    "A_DEFEATURE_TOGGLE",
    "A_MESH_UNMESHED",
    "A_SHELL_ALLTRI",
    "A_PROXIMITY_ON",
    "A_SCALE_BUDGET",
    "A_ELEMENT_ORDER_LINEAR",
]

ACTION_TITLES = {
    "A_CLEAR_REMESH": u"清空网格后重新生成",
    "A_SMOOTHING_HIGH": u"平滑提到 High + 迭代 10 + 后平滑",
    "A_CURVATURE_ON": u"开启曲率捕捉",
    "A_FIX_ZERO_SIZE": u"修复 0 尺寸控制项",
    "A_FIX_CONTROL_SCOPE": u"重新指定空作用域控制项",
    "A_DEFEATURE_TOGGLE": u"提高 Defeature Tolerance 并开启自动 defeature",
    "A_MESH_UNMESHED": u"为未划上网的体补尺寸/方法",
    "A_SHELL_ALLTRI": u"壳面改用三角形优先",
    "A_PROXIMITY_ON": u"开启 Capture Proximity",
    "A_SCALE_BUDGET": u"按 size^exp 放缩整体尺寸以回到预算内",
    "A_ELEMENT_ORDER_LINEAR": u"单元阶次降为一阶（改变求解精度，默认禁用）",
}


def plan(findings, snapshot, cfg, attempts=None, attempted_actions=None):
    """挑出下一轮要执行的阶梯步骤。

    ``attempts``  : {阶梯步骤: 已执行次数}；同一步默认只走一次，
                    但 "budget"（尺寸放缩）允许迭代多次，因为一次放缩不一定回到上限内。
    ``attempted_actions`` : 已执行过的动作 id，避免原地打转。

    返回 None 或 {"step":..., "actions":[...], "reason":...}
    """
    attempts = dict(attempts or {})
    attempted_actions = set(attempted_actions or [])
    repeat_limit = {"budget": 3}
    ladder = (cfg.get("repair") or {}).get("ladder") or ["smoothing"]
    triggered = diagnose_mod.steps_of(findings)

    for step in ladder:
        limit = repeat_limit.get(step, 1)
        if attempts.get(step, 0) >= limit:
            continue
        if step not in triggered:
            continue
        actions = []
        for finding in findings:
            if step not in (finding.get("steps") or []):
                continue
            for action in finding.get("actions") or []:
                if action in attempted_actions and step != "budget":
                    continue
                if _is_available(action, snapshot, cfg) and action not in actions:
                    actions.append(action)
        if actions:
            actions.sort(key=lambda a: ACTION_ORDER.index(a) if a in ACTION_ORDER else 99)
            return {"step": step, "actions": actions,
                    "reason": u"诊断到 %s 类问题（第 %d 次）"
                              % (step, attempts.get(step, 0) + 1)}
    return None


def _is_available(action, snapshot, cfg):
    if action == "A_ELEMENT_ORDER_LINEAR":
        return bool((cfg.get("repair") or {}).get("allow_element_order_change", False))
    if action == "A_SCALE_BUDGET":
        return int(snapshot.get("elements") or 0) > 0
    if action == "A_MESH_UNMESHED":
        return int(snapshot.get("elements") or 0) > 0 and bool(snapshot.get("unmeshed_bodies"))
    if action == "A_FIX_ZERO_SIZE":
        return any(c.get("type") in ("body_sizing", "face_sizing")
                   and c.get("size_mm") is not None and c["size_mm"] <= 0
                   for c in (snapshot.get("controls") or []))
    if action == "A_FIX_CONTROL_SCOPE":
        return any(c.get("body_count", 0) == 0 and not c.get("suppressed")
                   for c in (snapshot.get("controls") or []))
    return True


def apply(plan_dict, session, cfg, snapshot, log, ctx=None):
    """执行计划。返回 {"applied":[...], "results":[...], "notes":[...], "next_clear":bool}"""
    ctx = ctx if ctx is not None else {}
    ctx.setdefault("used_defeature_factors", [])
    outcome = {"applied": [], "results": [], "notes": [], "next_clear": False}
    if not plan_dict:
        return outcome

    body_rows = session.body_table()
    for action in plan_dict.get("actions") or []:
        handler = ACTIONS.get(action)
        if handler is None:
            outcome["notes"].append(u"未知动作 %s，跳过" % action)
            continue
        try:
            result = handler(session, cfg, snapshot, log, ctx, body_rows)
        except Exception:
            result = {"ok": False, "notes": [
                compat.to_text(traceback.format_exc()).strip().splitlines()[-1]]}
        if result.get("ok"):
            outcome["applied"].append(action)
            outcome["results"].append({"action": action,
                                       "title": ACTION_TITLES.get(action, action),
                                       "notes": result.get("notes") or []})
            for note in result.get("notes") or []:
                log.info(u"  纠正[%s] %s" % (action, note))
        elif result.get("skip"):
            message = u"%s 跳过: %s" % (ACTION_TITLES.get(action, action),
                                        u"; ".join(result.get("notes") or []))
            if message not in outcome["notes"]:
                outcome["notes"].append(message)
            log.info(u"  纠正[%s] 跳过: %s" % (action, u"; ".join(result.get("notes") or [])))
        else:
            outcome["notes"].append(u"%s 未能执行: %s"
                                    % (action, u"; ".join(result.get("notes") or [u"未知原因"])))
            log.warn(u"  纠正[%s] 失败: %s" % (action, u"; ".join(result.get("notes") or [])))
        if result.get("next_clear"):
            outcome["next_clear"] = True
    session.invalidate_caches()
    return outcome


# --------------------------------------------------------------------------
# 各动作实现
# --------------------------------------------------------------------------
def _act_clear_remesh(session, cfg, snapshot, log, ctx, body_rows):
    return {"ok": True, "next_clear": True, "notes": [u"下一轮将先 ClearGeneratedData 再划"]}


def _act_smoothing(session, cfg, snapshot, log, ctx, body_rows):
    notes = []
    ok = False
    current_smoothing = compat.to_text(compat.safe_get(session.mesh, "Smoothing")).strip().lower()
    current_iterations = int(compat.to_float(
        compat.safe_get(session.mesh, "SmoothingIterations"), 0) or 0)
    post = compat.safe_get(session.mesh, "UsePostSmoothing")
    already = (current_smoothing in ("high", "2")
               and (current_iterations == 0 or current_iterations >= 10)
               and (post is None or bool(post)))
    if already:
        return {"ok": False, "skip": True,
                "notes": [u"已是 Smoothing=High / 迭代 %d，无需重复设置" % current_iterations]}

    result = session.set_enum_member(session.mesh, "Smoothing", "High",
                                     int_table={"low": 0, "medium": 1, "high": 2},
                                     label=u"全局 Smoothing")
    if result.get("ok"):
        ok = True
        notes.append(u"Smoothing %s -> High" % compat.to_text(result.get("old")))
    else:
        notes.append(u"Smoothing 设置失败: %s" % result.get("error"))

    iterations = int(compat.to_float(compat.safe_get(session.mesh, "SmoothingIterations"), 0) or 0)
    if iterations and iterations < 10:
        result = session.set_number(session.mesh, "SmoothingIterations", 10,
                                    label=u"全局 SmoothingIterations")
        if result.get("ok"):
            ok = True
            notes.append(u"SmoothingIterations %s -> 10" % compat.fmt_num(iterations))

    if compat.safe_get(session.mesh, "UsePostSmoothing") is not None:
        result = session.set_bool(session.mesh, "UsePostSmoothing", True,
                                  label=u"全局 UsePostSmoothing")
        if result.get("ok"):
            ok = True
            notes.append(u"UsePostSmoothing -> True")
    return {"ok": ok, "notes": notes}


def _act_curvature(session, cfg, snapshot, log, ctx, body_rows):
    result = session.set_bool(session.mesh, "CaptureCurvature", True,
                              label=u"全局 CaptureCurvature")
    if result.get("ok"):
        return {"ok": True, "notes": [u"CaptureCurvature %s -> True"
                                      % compat.to_text(result.get("old"))]}
    return {"ok": False, "notes": [u"CaptureCurvature 设置失败: %s" % result.get("error")]}


def _act_zero_size(session, cfg, snapshot, log, ctx, body_rows):
    """把 0 尺寸控制项改成"同类型正尺寸的中位数"，夹在全局尺寸的 0.25~1.5 倍之间。"""
    control_rows = controls_mod.list_controls(session)
    sizing_rows = [row for row in control_rows if row.get("type") in ("body_sizing", "face_sizing")]
    positive = [row["size_mm"] for row in sizing_rows
                if row.get("size_mm") is not None and row["size_mm"] > 0]
    global_size = compat.to_float((cfg.get("mesh") or {}).get("element_size_mm"), 0) or 10.0
    if positive:
        positive.sort()
        middle = len(positive) // 2
        base = positive[middle] if len(positive) % 2 else 0.5 * (positive[middle - 1] + positive[middle])
    else:
        base = global_size
    desired = max(min(base, global_size * 1.5), global_size * 0.25, 0.01)

    notes = []
    ok = False
    for row in sizing_rows:
        size = row.get("size_mm")
        if size is None or size > 0:
            continue
        result = session.set_length_mm(row["object"], "ElementSize", desired,
                                       label=u"控制项 '%s'.ElementSize" % row["name"])
        if result.get("ok") and result.get("verified"):
            ok = True
            notes.append(u"'%s' 0 -> %.3g mm" % (row["name"], desired))
        else:
            notes.append(u"'%s' 设置失败: %s" % (row["name"], result.get("error")))
    if not ok and not notes:
        notes.append(u"没有找到 0 尺寸控制项")
    return {"ok": ok, "notes": notes}


def _act_fix_scope(session, cfg, snapshot, log, ctx, body_rows):
    specs = dict((s["name"], s) for s in (cfg.get("controls") or []))
    notes = []
    ok = False
    for row in controls_mod.list_controls(session):
        if row.get("body_count", 0) > 0 or row.get("suppressed"):
            continue
        spec = specs.get(row["name"])
        if not spec or not spec.get("bodies"):
            notes.append(u"'%s' 作用域为空，但配置里没有对应的 bodies 通配符" % row["name"])
            continue
        matched = controls_mod.match_bodies(spec["bodies"], body_rows)
        if not matched:
            notes.append(u"'%s' 的 bodies=[%s] 没匹配到体"
                         % (row["name"], u", ".join(spec["bodies"])))
            continue
        location = controls_mod.set_location(session, row["object"], [m[0] for m in matched], log)
        if location["ok"]:
            ok = True
            notes.append(u"'%s' 重新指定 %d 个体（%s）"
                         % (row["name"], location["count"], location["strategy"]))
        else:
            notes.append(u"'%s' 作用域指定失败: %s" % (row["name"], location["error"]))
    return {"ok": ok, "notes": notes}


def _act_defeature(session, cfg, snapshot, log, ctx, body_rows):
    global_size = compat.to_float((cfg.get("mesh") or {}).get("element_size_mm"), 0) or 10.0
    ladder = (cfg.get("repair") or {}).get("defeature_factor_ladder") or [0.01, 0.03, 0.06]
    used = ctx.setdefault("used_defeature_factors", [])
    factor = None
    for candidate in ladder:
        if float(candidate) not in used:
            factor = float(candidate)
            break
    if factor is None:
        return {"ok": False, "skip": True,
                "notes": [u"defeature 阶梯已用尽（%s）" % used]}

    desired = max(global_size * factor, 1e-4)
    notes = []
    ok = False
    result = session.set_length_mm(session.mesh, "DefeatureTolerance", desired,
                                   label=u"全局 DefeatureTolerance")
    if result.get("ok") and result.get("verified"):
        ok = True
        used.append(factor)
        notes.append(u"DefeatureTolerance %.4g -> %.3g mm（全局尺寸的 %.1f%%）"
                     % (compat.to_float(result.get("old"), 0) or 0, desired, factor * 100.0))
    else:
        notes.append(u"DefeatureTolerance 设置失败: %s" % result.get("error"))

    result = session.set_bool(session.mesh, "AutomaticMeshBasedDefeaturing", True,
                              label=u"全局 AutomaticMeshBasedDefeaturing")
    if result.get("ok"):
        ok = True
        notes.append(u"AutomaticMeshBasedDefeaturing -> True")
    return {"ok": ok, "notes": notes}


def _act_mesh_unmeshed(session, cfg, snapshot, log, ctx, body_rows):
    unmeshed = set(snapshot.get("unmeshed_bodies") or [])
    if not unmeshed:
        return {"ok": False, "notes": [u"没有未划上网的体"]}
    targets = [row for row in body_rows if row["name"] in unmeshed]
    if not targets:
        return {"ok": False, "notes": [u"未划上网的体已不在几何里（几何可能已更新）"]}

    global_size = compat.to_float((cfg.get("mesh") or {}).get("element_size_mm"), 0) or 10.0
    size = max(global_size * 0.5, 0.05)
    notes = []
    ok = False
    for row in targets[:40]:  # 防止一次性建上百个控制项
        name = u"自动补网 - %s" % row["leaf"][:40]
        spec = {"name": name, "type": "body_sizing", "size_mm": size, "method": None,
                "algorithm": None, "bodies": [row["name"]], "role": "auto_repair",
                "reuse_existing": True, "create_if_missing": True, "suppress": False}
        report = {"actions": [], "created": [], "updated": [], "missing": [],
                  "unmatched": [], "failures": []}
        existing = controls_mod.find_control(session, name)
        if existing is not None:
            controls_mod._update_control(session, cfg, spec, existing, body_rows, report, log)
        else:
            controls_mod._create_control(session, cfg, spec, body_rows, report, log)
        if report["failures"]:
            notes.append(u"'%s' 补控制失败: %s"
                         % (row["name"], u"; ".join(compat.to_text(f.get("error"))
                                                    for f in report["failures"][:2])))
        else:
            ok = True
            notes.append(u"'%s' 补 Body Sizing=%.3g mm" % (row["name"], size))

    # 再给这些体加 AllTriAllTet，绕开四边形/扫掠网格器
    if ok and len(targets) <= 20:
        name = u"自动补网方法"
        spec = {"name": name, "type": "automatic_method", "size_mm": None,
                "method": "AllTriAllTet", "algorithm": None,
                "bodies": [row["name"] for row in targets], "role": "auto_repair",
                "reuse_existing": True, "create_if_missing": True, "suppress": False}
        report = {"actions": [], "created": [], "updated": [], "missing": [],
                  "unmatched": [], "failures": []}
        existing = controls_mod.find_control(session, name)
        if existing is not None:
            controls_mod._update_control(session, cfg, spec, existing, body_rows, report, log)
        else:
            controls_mod._create_control(session, cfg, spec, body_rows, report, log)
        if report["failures"]:
            notes.append(u"补方法控制失败: %s"
                         % u"; ".join(compat.to_text(f.get("error")) for f in report["failures"][:2]))
        else:
            notes.append(u"%d 个体加 AllTriAllTet 方法" % len(targets))
    return {"ok": ok, "notes": notes}


def _act_shell_alltri(session, cfg, snapshot, log, ctx, body_rows):
    notes = []
    ok = False
    if compat.safe_get(session.mesh, "QuadMesh") is not None:
        result = session.set_bool(session.mesh, "QuadMesh", False, label=u"全局 QuadMesh")
        if result.get("ok"):
            ok = True
            notes.append(u"QuadMesh %s -> False（壳面改用三角形优先）"
                         % compat.to_text(result.get("old")))
        else:
            notes.append(u"QuadMesh 设置失败: %s" % result.get("error"))

    shells = [row for row in body_rows if (row.get("thickness_mm") or 0) > 0]
    if shells and len(shells) <= 12:
        spec = {"name": u"壳面三角形", "type": "automatic_method", "size_mm": None,
                "method": "AllTriAllTet", "algorithm": None,
                "bodies": [row["name"] for row in shells], "role": "shell_fix",
                "reuse_existing": True, "create_if_missing": True, "suppress": False}
        report = {"actions": [], "created": [], "updated": [], "missing": [],
                  "unmatched": [], "failures": []}
        existing = controls_mod.find_control(session, spec["name"])
        if existing is not None:
            controls_mod._update_control(session, cfg, spec, existing, body_rows, report, log)
        else:
            controls_mod._create_control(session, cfg, spec, body_rows, report, log)
        if report["failures"]:
            notes.append(u"壳面方法控制失败: %s"
                         % u"; ".join(compat.to_text(f.get("error")) for f in report["failures"][:2]))
        else:
            ok = True
            notes.append(u"%d 个壳体加 AllTriAllTet" % len(shells))
    return {"ok": ok, "notes": notes}


def _act_proximity(session, cfg, snapshot, log, ctx, body_rows):
    budget = cfg.get("budget") or {}
    basis = budget.get("basis", "elements")
    cap = int(budget.get("hard_cap") or 0)
    count = int(snapshot.get(basis) or 0)
    growth = compat.to_float((cfg.get("repair") or {}).get("proximity_element_growth"), 1.15) or 1.15
    if cap and count and count * growth > cap:
        return {"ok": False, "skip": True,
                "notes": [u"预测单元数 %.3g × %.2f = %.3g 会超上限 %.3g，放弃开启近距捕捉"
                          % (count, growth, count * growth, cap)]}
    result = session.set_bool(session.mesh, "CaptureProximity", True,
                              label=u"全局 CaptureProximity")
    if result.get("ok"):
        return {"ok": True, "notes": [u"CaptureProximity %s -> True（预计单元数 +%.0f%%）"
                                      % (compat.to_text(result.get("old")), (growth - 1) * 100)]}
    return {"ok": False, "notes": [u"CaptureProximity 设置失败: %s" % result.get("error")]}


def _act_scale_budget(session, cfg, snapshot, log, ctx, body_rows):
    budget = cfg.get("budget") or {}
    basis = budget.get("basis", "elements")
    target = int(budget.get("target") or 0)
    cap = int(budget.get("hard_cap") or 0)
    count = int(snapshot.get(basis) or 0)
    exponent = max(compat.to_float(budget.get("size_exponent"), 2.2) or 2.2, 0.5)
    if count <= 0:
        return {"ok": False, "skip": True, "notes": [u"当前没有网格，无法按预算放缩"]}
    goal = cap if (cap and count > cap) else target
    if not goal or count <= goal:
        return {"ok": False, "skip": True,
                "notes": [u"当前 %s=%s 未超预算目标，无需放缩"
                          % (basis, compat.fmt_num(count))]}

    factors = cfg.get("repair") or {}
    factor = (count / float(goal)) ** (1.0 / exponent)
    factor = min(max(factor, 1.02), compat.to_float(factors.get("budget_scale_max"), 1.8) or 1.8)

    notes = []
    ok = False
    global_size = compat.to_float((cfg.get("mesh") or {}).get("element_size_mm"), 0) or 0
    if global_size > 0:
        result = session.set_length_mm(session.mesh, "ElementSize", global_size * factor,
                                       label=u"全局 ElementSize")
        if result.get("ok") and result.get("verified"):
            ok = True
            notes.append(u"全局尺寸 %.3g -> %.3g mm" % (global_size, global_size * factor))
    for row in controls_mod.list_controls(session):
        size = row.get("size_mm")
        if size is None or size <= 0:
            continue
        result = session.set_length_mm(row["object"], "ElementSize", size * factor,
                                       label=u"控制项 '%s'.ElementSize" % row["name"])
        if result.get("ok") and result.get("verified"):
            ok = True
    if ok:
        notes.append(u"按 (%.3g/%.3g)^(1/%.2f) = ×%.3f 放缩全部尺寸"
                     % (count, goal, exponent, factor))
    else:
        notes.append(u"尺寸放缩未能生效")
    return {"ok": ok, "notes": notes}


def _act_element_order(session, cfg, snapshot, log, ctx, body_rows):
    if not (cfg.get("repair") or {}).get("allow_element_order_change", False):
        return {"ok": False, "skip": True,
                "notes": [u"allow_element_order_change=false，跳过"]}
    result = session.set_enum_member(session.mesh, "ElementOrder", "First",
                                     int_table={"programcontrolled": 0, "first": 1, "second": 2},
                                     label=u"全局 ElementOrder")
    if result.get("ok"):
        return {"ok": True, "notes": [u"ElementOrder %s -> First（精度会降低，需复核结果）"
                                      % compat.to_text(result.get("old"))]}
    return {"ok": False, "notes": [u"ElementOrder 设置失败: %s" % result.get("error")]}


ACTIONS = {
    "A_CLEAR_REMESH": _act_clear_remesh,
    "A_SMOOTHING_HIGH": _act_smoothing,
    "A_CURVATURE_ON": _act_curvature,
    "A_FIX_ZERO_SIZE": _act_zero_size,
    "A_FIX_CONTROL_SCOPE": _act_fix_scope,
    "A_DEFEATURE_TOGGLE": _act_defeature,
    "A_MESH_UNMESHED": _act_mesh_unmeshed,
    "A_SHELL_ALLTRI": _act_shell_alltri,
    "A_PROXIMITY_ON": _act_proximity,
    "A_SCALE_BUDGET": _act_scale_budget,
    "A_ELEMENT_ORDER_LINEAR": _act_element_order,
}
