# -*- coding: utf-8 -*-
"""网格设置层：全局设置 + 局部控制项的读/建/改，以及**状态快照与回滚**。

这一层是"自动纠正"能安全工作的基础：
所有改动前先 ``capture_state``，出现更差结果时 ``apply_state`` 精确还原。
"""
from __future__ import division

import fnmatch
import traceback

from . import compat
from . import session as session_mod

# 全局设置映射表:  config 键 -> (Mechanical 属性名, 类型, 附加信息)
GLOBAL_SPECS = [
    ("element_size_mm", "ElementSize", "mm", None),
    ("use_adaptive_sizing", "UseAdaptiveSizing", "bool", None),
    ("resolution", "Resolution", "number", None),
    ("defeature_tolerance_mm", "DefeatureTolerance", "mm", None),
    ("automatic_mesh_based_defeaturing", "AutomaticMeshBasedDefeaturing", "bool", None),
    ("capture_curvature", "CaptureCurvature", "bool", None),
    ("curvature_normal_angle_deg", "CurvatureNormalAngle", "number", None),
    ("capture_proximity", "CaptureProximity", "bool", None),
    ("growth_rate", "GrowthRate", "number", None),
    ("transition_ratio", "TransitionRatio", "number", None),
    ("smoothing", "Smoothing", "enum", session_mod.SMOOTHING_INT),
    ("smoothing_iterations", "SmoothingIterations", "number", None),
    ("use_post_smoothing", "UsePostSmoothing", "bool", None),
    ("shape_checking", "ShapeChecking", "bool", None),
    ("topology_checking", "TopologyChecking", "bool", None),
    ("element_order", "ElementOrder", "enum", session_mod.ELEMENT_ORDER_INT),
    ("quad_mesh", "QuadMesh", "bool", None),
    ("straight_sided_elements", "StraightSidedElements", "bool", None),
    ("use_automatic_inflation", "UseAutomaticInflation", "bool", None),
    ("physics_preference", "PhysicsPreference", "readonly", None),
]

GLOBAL_BY_ATTR = dict((attr, (key, kind, table)) for key, attr, kind, table in GLOBAL_SPECS)

# 只读回、不回写的项（快照里有意义，回写会改物理行为/不允许）
NO_ROLLBACK_ATTRS = ("PhysicsPreference",)

CONTROL_ADDERS = {
    "body_sizing": "AddSizing",
    "face_sizing": "AddSizing",
    "automatic_method": "AddAutomaticMethod",
    "face_meshing": "AddFaceMeshing",
    "refinement": "AddRefinement",
    "inflation": "AddInflation",
}


# --------------------------------------------------------------------------
# 全局设置
# --------------------------------------------------------------------------
def read_global(session):
    """读回全部全局设置 {attr: 可读值}。

    长度统一成 mm 浮点、布尔成文本、数值成浮点，保证快照能原样写回。
    """
    out = {}
    for key, attr, kind, _table in GLOBAL_SPECS:
        value = compat.safe_get(session.mesh, attr, compat._UNSET)
        if value is compat._UNSET:
            continue
        if kind == "mm":
            out[attr] = session_mod.quantity_mm(value)
        elif kind in ("number", "deg"):
            out[attr] = compat.to_float(value)
        else:
            out[attr] = compat.to_text(value)
    return out


def apply_global(session, cfg, log, keys=None):
    """把 cfg['mesh'] 写进 Mechanical。返回 (actions, failures)。

    规则：
      * 只在**与当前值不同**时才写，减少无谓刷新；
      * Curvature Normal Angle = 0 视为"程序控制"，不写（写 0 可能非法）；
      * 写入后逐项读回校验，校验失败的进 failures。
    """
    mesh_cfg = cfg.get("mesh", {})
    actions = []
    failures = []
    for key, attr, kind, table in GLOBAL_SPECS:
        if keys is not None and attr not in keys:
            continue
        if key not in mesh_cfg:
            continue
        desired = mesh_cfg.get(key)
        if desired is None:
            continue
        if kind == "readonly":
            continue  # 只读项（如 Physics Preference）只读取、不回写
        if attr == "CurvatureNormalAngle" and float(compat.to_float(desired, 0) or 0) == 0.0:
            continue  # 0 = 程序控制，不写
        current = compat.safe_get(session.mesh, attr, compat._UNSET)
        if current is compat._UNSET:
            failures.append({"target": u"全局: %s" % attr, "ok": False, "skipped": True,
                             "error": u"该版本没有属性 %s" % attr})
            continue
        if _same_value(session, current, desired, kind, table):
            continue
        if kind == "mm":
            result = session.set_length_mm(session.mesh, attr, compat.to_float(desired, 0),
                                           label=u"全局: %s" % attr)
        elif kind == "bool":
            result = session.set_bool(session.mesh, attr, desired, label=u"全局: %s" % attr)
        elif kind == "enum":
            result = session.set_enum_member(session.mesh, attr, compat.to_text(desired),
                                             int_table=table, label=u"全局: %s" % attr)
        else:
            result = session.set_number(session.mesh, attr, compat.to_float(desired, 0),
                                        label=u"全局: %s" % attr)
        result["desired"] = desired
        actions.append(result)
        if not result.get("ok") or not result.get("verified"):
            failures.append(result)
    return actions, failures


def _same_value(session, current, desired, kind, table=None):
    """当前值 vs 期望值 是否等价（避免无谓写入）。"""
    try:
        if kind == "mm":
            current_mm = session_mod.quantity_mm(current)
            if current_mm is None:
                return False
            return abs(current_mm - float(desired)) <= max(1e-6, abs(float(desired)) * 1e-6)
        if kind == "bool":
            return compat.to_text(current).lower() in (
                u"true" if desired else u"false", u"1" if desired else u"0")
        if kind == "enum":
            text = compat.to_text(current).strip().lower()
            want = compat.to_text(desired).strip().lower()
            if text == want:
                return True
            if want == "programcontrolled" and text in ("programcontrolled", "none", "0"):
                return True
            if table:
                number = table.get(want)
                if number is not None:
                    current_number = compat.to_float(text)
                    if current_number is not None and int(current_number) == int(number):
                        return True
            return False
        current_float = compat.to_float(current)
        return current_float is not None and abs(current_float - float(desired)) <= 1e-9
    except Exception:
        return False


# --------------------------------------------------------------------------
# 局部控制项
# --------------------------------------------------------------------------
def list_controls(session):
    """读出 mesh.Children 里的全部网格控制项。"""
    rows = []
    children = compat.safe_get(session.mesh, "Children")
    if children is None:
        return rows
    for child in children:
        rows.append(describe_control(session, child))
    return rows


def describe_control(session, control):
    row = {
        "object": control,
        "name": compat.to_text(compat.safe_get(control, "Name", u"<unnamed>")),
        "category": compat.to_text(compat.safe_get(control, "DataModelObjectCategory", u"")),
        "type": _guess_type(control),
        "size_mm": session_mod.quantity_mm(compat.safe_get(control, "ElementSize")),
        "method": compat.to_text(compat.safe_get(control, "Method", u"")),
        "algorithm": compat.to_text(compat.safe_get(control, "Algorithm", u"")),
        "suppressed": bool(compat.safe_get(control, "Suppressed", False)),
        "body_count": _location_count(session, control),
        "behavior": compat.to_text(compat.safe_get(control, "Behavior", u"")),
    }
    return row


def _guess_type(control):
    category = compat.to_text(compat.safe_get(control, "DataModelObjectCategory", u"")).lower()
    if "sizing" in category:
        return "face_sizing" if "face" in category else "body_sizing"
    if "method" in category:
        return "automatic_method"
    if "facemeshing" in category or "face meshing" in category:
        return "face_meshing"
    if "refinement" in category:
        return "refinement"
    if "inflation" in category:
        return "inflation"
    return category or u"unknown"


def _location_count(session, control):
    location = compat.safe_get(control, "Location")
    if location is None:
        return 0
    return len(session.selection_ids(location))


def find_control(session, name, controls=None, kind=None):
    """按名字找控制项。

    真实模型里会有**同名但不同类型**的控制项（例如两个都叫 `大三角`，一个是 Sizing、
    一个是 AutomaticMethod），因此优先匹配"名字 + 类型"都对得上的那一个。
    """
    controls = controls if controls is not None else list_controls(session)
    wanted = compat.to_text(name).strip()
    lowered = wanted.lower()
    if kind:
        for row in controls:
            if row["name"].strip().lower() == lowered and _type_matches(kind, row.get("type")):
                return row
    for row in controls:
        if row["name"] == wanted:
            return row
    for row in controls:
        if row["name"].strip().lower() == lowered:
            return row
    return None


def _type_matches(wanted_kind, row_type):
    if wanted_kind == row_type:
        return True
    # body_sizing / face_sizing 在 Mechanical 里都显示为 "Sizing"
    if wanted_kind in ("body_sizing", "face_sizing") and row_type in ("body_sizing", "face_sizing"):
        return True
    return False


# --------------------------------------------------------------------------
# 体匹配
# --------------------------------------------------------------------------
def match_bodies(patterns, body_rows):
    """glob 匹配体名（同时匹配全名与叶子名）。返回 [(body_row, pattern)]。"""
    matched = []
    seen = set()
    for pattern in patterns or []:
        pattern = compat.to_text(pattern).strip()
        if not pattern:
            continue
        for row in body_rows:
            key = row.get("geo_id") or row["name"]
            if key in seen:
                continue
            if fnmatch.fnmatch(row["name"], pattern) or fnmatch.fnmatch(row["leaf"], pattern):
                matched.append((row, pattern))
                seen.add(key)
    return matched


def set_location(session, control, body_rows, log):
    """把控制项作用域设为给定体。多策略 + 校验。"""
    result = {"ok": False, "strategy": None, "ids": [], "error": None, "count": 0}
    geo_ids = [row["geo_id"] for row in body_rows if row.get("geo_id") is not None]
    if geo_ids and len(geo_ids) == len(body_rows):
        selection = session.make_selection(geo_ids)
        if selection is not None:
            try:
                control.Location = selection
                ids = session.selection_ids(compat.safe_get(control, "Location"))
                if len(ids) == len(geo_ids):
                    result.update({"ok": True, "strategy": "Ids", "ids": ids, "count": len(ids)})
                    return result
            except Exception:
                result["error"] = compat.to_text(traceback.format_exc()).strip().splitlines()[-1]

    # 退化策略：用当前选择集（ClearSelection + AddSelection）
    try:
        manager = compat.safe_get(session.extapi, "SelectionManager")
        if manager is not None:
            manager.ClearSelection()
            for row in body_rows:
                manager.AddSelection(row["object"])
            current = compat.safe_get(manager, "CurrentSelection")
            if current is not None:
                control.Location = current
                ids = session.selection_ids(compat.safe_get(control, "Location"))
                if len(ids) == len(body_rows):
                    result.update({"ok": True, "strategy": "CurrentSelection", "ids": ids,
                                   "count": len(ids)})
                    return result
                result["error"] = u"当前选择集策略只匹配到 %d/%d 个体" % (len(ids), len(body_rows))
    except Exception:
        result["error"] = compat.to_text(traceback.format_exc()).strip().splitlines()[-1]

    if not geo_ids:
        result["error"] = result["error"] or u"取不到体的几何参考 id"
    log.warn(u"控制项 '%s' 作用域设置失败: %s"
             % (compat.to_text(compat.safe_get(control, "Name", u"?")), result["error"]))
    return result


# --------------------------------------------------------------------------
# 应用需求（更新已有 / 缺失时新建）
# --------------------------------------------------------------------------
def apply_specs(session, cfg, body_rows, log):
    """按 cfg['controls'] 配置网格控制项。

    返回 {"actions":[...], "created":[...], "updated":[...], "missing":[...],
          "unmatched":[...]}
    """
    report = {"actions": [], "created": [], "updated": [], "missing": [],
              "unmatched": [], "failures": []}
    existing = list_controls(session)
    claimed = set()   # 已被某条 spec 认领的控制项，避免两条 spec 抢同一个对象
    for spec in cfg.get("controls") or []:
        name = spec["name"]
        available = [row for row in existing if id(row["object"]) not in claimed]
        row = None
        if spec.get("reuse_existing", True):
            row = find_control(session, name, available, kind=spec.get("type"))
        if row is not None:
            claimed.add(id(row["object"]))
            _update_control(session, cfg, spec, row, body_rows, report, log)
            continue
        if not spec.get("create_if_missing", True):
            report["missing"].append(name)
            log.warn(u"控制项 '%s' 不存在，且配置了 create_if_missing=false，跳过" % name)
            continue
        _create_control(session, cfg, spec, body_rows, report, log)
    return report


def _update_control(session, cfg, spec, row, body_rows, report, log):
    control = row["object"]
    name = spec["name"]
    changed = False

    if spec.get("size_mm") is not None:
        current = row.get("size_mm")
        desired = float(spec["size_mm"])
        if current is None or abs(current - desired) > max(1e-6, desired * 1e-6):
            result = session.set_length_mm(control, "ElementSize", desired,
                                           label=u"控制项 '%s'.ElementSize" % name)
            result["desired"] = desired
            report["actions"].append(result)
            if not (result.get("ok") and result.get("verified")):
                report["failures"].append(result)
            else:
                changed = True

    if spec.get("method"):
        result = session.set_enum_member(control, "Method", spec["method"],
                                        label=u"控制项 '%s'.Method" % name)
        result["desired"] = spec["method"]
        if result.get("skipped"):
            report["failures"].append(result)
        elif result.get("ok") and result.get("verified"):
            report["actions"].append(result)
            changed = True
        elif compat.to_text(row.get("method")).strip().lower() != compat.to_text(spec["method"]).strip().lower():
            report["failures"].append(result)

    if spec.get("algorithm"):
        result = session.set_enum_member(control, "Algorithm", spec["algorithm"],
                                        label=u"控制项 '%s'.Algorithm" % name)
        result["desired"] = spec["algorithm"]
        if result.get("ok") and result.get("verified"):
            report["actions"].append(result)
            changed = True
        elif not result.get("skipped"):
            report["failures"].append(result)

    if spec.get("suppress"):
        result = session.set_bool(control, "Suppressed", True,
                                  label=u"控制项 '%s'.Suppressed" % name)
        if result.get("ok"):
            report["actions"].append(result)
            changed = True

    # 作用域缺失（0 个体）时补一次 —— 这是"控制项看似存在但完全没生效"的常见原因
    if row.get("body_count", 0) == 0 and spec.get("bodies"):
        matched = match_bodies(spec["bodies"], body_rows)
        if matched:
            location = set_location(session, control, [m[0] for m in matched], log)
            if location["ok"]:
                changed = True
                report["actions"].append({"target": u"控制项 '%s'.Location" % name,
                                          "ok": True, "new": location["count"],
                                          "verified": True, "note": location["strategy"]})
                log.info(u"控制项 '%s' 原本作用域为空，已重新指定 %d 个体"
                         % (name, location["count"]))
            else:
                report["failures"].append({"target": u"控制项 '%s'.Location" % name,
                                           "ok": False, "error": location["error"]})
        else:
            report["unmatched"].append(name)

    if changed:
        report["updated"].append(name)


def _create_control(session, cfg, spec, body_rows, report, log):
    name = spec["name"]
    ctype = spec["type"]
    adder_name = CONTROL_ADDERS.get(ctype)
    adder = compat.safe_get(session.mesh, adder_name) if adder_name else None
    if not callable(adder):
        message = u"当前版本没有 %s()，无法新建控制项 '%s'" % (adder_name, name)
        log.warn(message)
        report["failures"].append({"target": u"新建控制项 '%s'" % name, "ok": False,
                                   "error": message})
        return
    try:
        control = adder()
    except Exception:
        message = compat.to_text(traceback.format_exc()).strip().splitlines()[-1]
        log.warn(u"新建控制项 '%s' 失败: %s" % (name, message))
        report["failures"].append({"target": u"新建控制项 '%s'" % name, "ok": False,
                                   "error": message})
        return

    try:
        control.Name = name
    except Exception:
        pass

    matched = match_bodies(spec.get("bodies") or [], body_rows)
    if not matched:
        report["unmatched"].append(name)
        log.warn(u"控制项 '%s' 没有匹配到任何体（bodies=[%s]），已建立但作用域为空"
                 % (name, u", ".join(spec.get("bodies") or [])))
    else:
        location = set_location(session, control, [m[0] for m in matched], log)
        if not location["ok"]:
            report["failures"].append({"target": u"新建控制项 '%s'.Location" % name,
                                       "ok": False, "error": location["error"]})

    if spec.get("size_mm") is not None:
        result = session.set_length_mm(control, "ElementSize", float(spec["size_mm"]),
                                       label=u"新建控制项 '%s'.ElementSize" % name)
        result["desired"] = spec["size_mm"]
        report["actions"].append(result)
        if not (result.get("ok") and result.get("verified")):
            report["failures"].append(result)

    if spec.get("method"):
        result = session.set_enum_member(control, "Method", spec["method"],
                                        label=u"新建控制项 '%s'.Method" % name)
        result["desired"] = spec["method"]
        report["actions"].append(result)
        if not (result.get("ok") and result.get("verified")):
            report["failures"].append(result)

    report["created"].append(name)
    log.info(u"新建控制项 '%s'（%s，匹配 %d 个体）" % (name, ctype, len(matched)))


def repair_control_sizes(session, cfg, controls, body_rows, log, sizes_mm=None):
    """批量改控制项尺寸（用于 0 尺寸修复、预算缩放）。sizes_mm: {name: mm}"""
    results = []
    for row in controls:
        name = row["name"]
        desired = None
        if sizes_mm and name in sizes_mm:
            desired = float(sizes_mm[name])
        elif sizes_mm is None and (row.get("size_mm") or 0) <= 0:
            desired = None
        if desired is None:
            continue
        result = session.set_length_mm(row["object"], "ElementSize", desired,
                                       label=u"控制项 '%s'.ElementSize" % name)
        result["desired"] = desired
        result["control"] = name
        results.append(result)
    return results


# --------------------------------------------------------------------------
# 状态快照 / 回滚
# --------------------------------------------------------------------------
def capture_state(session, cfg=None):
    """完整捕获"网格设置状态"：全局属性 + 每个控制项的尺寸/方法/抑制。"""
    controls = list_controls(session)
    return {
        "global": read_global(session),
        "controls": dict((row["name"], {
            "size_mm": row.get("size_mm"),
            "method": row.get("method"),
            "algorithm": row.get("algorithm"),
            "suppressed": row.get("suppressed"),
            "body_count": row.get("body_count"),
        }) for row in controls),
        "control_order": [row["name"] for row in controls],
    }


def apply_state(session, state, log, only_global=False):
    """把状态写回 Mechanical（用于回滚到最佳一轮）。返回 (changed, failures)。"""
    changed = []
    failures = []
    for attr, value in (state.get("global") or {}).items():
        if attr in NO_ROLLBACK_ATTRS:
            continue
        spec = GLOBAL_BY_ATTR.get(attr)
        if spec is None:
            continue
        _key, kind, table = spec
        current = compat.safe_get(session.mesh, attr, compat._UNSET)
        if current is compat._UNSET or value is None:
            continue
        if kind == "mm":
            current_value = session_mod.quantity_mm(current)
            if current_value is not None and abs(current_value - float(value)) <= abs(float(value)) * 1e-9:
                continue
            result = session.set_length_mm(session.mesh, attr, float(value), label=u"回滚: %s" % attr)
        elif kind == "bool":
            if compat.to_text(current).lower() == compat.to_text(value).lower():
                continue
            result = session.set_bool(session.mesh, attr, compat.to_text(value).lower() in (u"true", u"1"),
                                      label=u"回滚: %s" % attr)
        elif kind == "enum":
            if compat.to_text(current).strip().lower() == compat.to_text(value).strip().lower():
                continue
            result = session.set_enum_member(session.mesh, attr, compat.to_text(value),
                                             int_table=table, label=u"回滚: %s" % attr)
        else:
            current_float = compat.to_float(current)
            target_float = compat.to_float(value)
            if target_float is None:
                continue
            if current_float is not None and abs(current_float - target_float) <= 1e-9:
                continue
            result = session.set_number(session.mesh, attr, target_float,
                                        label=u"回滚: %s" % attr)
        if result.get("ok") and result.get("verified"):
            changed.append(result)
        else:
            failures.append(result)

    if only_global:
        return changed, failures

    controls = list_controls(session)
    by_name = dict((row["name"], row) for row in controls)
    for name, want in (state.get("controls") or {}).items():
        row = by_name.get(name)
        if row is None:
            continue
        if want.get("size_mm") is not None and row.get("size_mm") is not None:
            if abs(row["size_mm"] - float(want["size_mm"])) > abs(float(want["size_mm"])) * 1e-9:
                result = session.set_length_mm(row["object"], "ElementSize",
                                               float(want["size_mm"]),
                                               label=u"回滚: '%s'" % name)
                if result.get("ok") and result.get("verified"):
                    changed.append(result)
                else:
                    failures.append(result)
        if want.get("suppressed") is not None and bool(row.get("suppressed")) != bool(want["suppressed"]):
            result = session.set_bool(row["object"], "Suppressed", bool(want["suppressed"]),
                                      label=u"回滚: '%s'.Suppressed" % name)
            if result.get("ok"):
                changed.append(result)
            else:
                failures.append(result)
    return changed, failures


def set_property_internal(control, api_name, value):
    """用 ACT 的 Property 对象写值（2022 R1 实测唯一可用的写入通道）。

    实测：``control.Method = ...`` 会报 "parameterized and is read-only"，
    但 ``control.PropertyByAPIName("Method").InternalValue = ...`` 可用。
    """
    getter = compat.safe_get(control, "PropertyByAPIName")
    if not callable(getter):
        return {"ok": False, "api": api_name, "error": u"没有 PropertyByAPIName"}
    try:
        prop = getter(api_name)
    except Exception:
        return {"ok": False, "api": api_name,
                "error": compat.to_text(traceback.format_exc()).strip().splitlines()[-1]}
    if prop is None:
        return {"ok": False, "api": api_name, "error": u"属性对象为空"}
    readonly = compat.safe_get(prop, "ReadOnly")
    before = compat.safe_get(prop, "InternalValue")
    try:
        prop.InternalValue = value
    except Exception:
        return {"ok": False, "api": api_name, "readonly": readonly,
                "error": compat.to_text(traceback.format_exc()).strip().splitlines()[-1]}
    after = compat.safe_get(prop, "InternalValue")
    return {"ok": True, "api": api_name, "readonly": readonly,
            "old": before, "new": after}


def create_method_control(session, body_row, method_name=u"Sweep", num_divisions=None,
                          name=None, log=None):
    """给一个体建"方法"控制项（2022 R1 实测可行的完整流程）。

    实测顺序（顺序错了会失败）：
      1. ``body.GetGeoBody()`` 拿几何体 -> ``.Id`` 才是控制项 id；
      2. ``CreateSelectionInfo(GeometryEntities)`` + ``Ids=[geo.Id]``；
      3. 建控制项后**先设 Location**（作用域为空时 Method 是只读的）；
      4. 再写 ``Method`` / ``SweepNumberDivisions``（走 Property.InternalValue）。

    返回 dict：{ok, control, scope, method, divisions, notes}
    """
    log = log or compat.Logger(verbose=False)
    notes = []
    mesh = session.mesh
    result = {"ok": False, "control": None, "scope": 0, "method": None,
              "divisions": None, "notes": notes}

    getter = compat.safe_get(body_row["object"], "GetGeoBody")
    if not callable(getter):
        notes.append(u"体上没有 GetGeoBody()")
        return result
    try:
        geo = getter()
    except Exception:
        notes.append(u"GetGeoBody() 失败: %s"
                     % compat.to_text(traceback.format_exc()).strip().splitlines()[-1])
        return result
    geo_id = compat.safe_get(geo, "Id")
    if geo_id is None:
        notes.append(u"GetGeoBody().Id 为空")
        return result

    manager = compat.safe_get(session.extapi, "SelectionManager")
    selection = compat.safe_get(manager, "CreateSelectionInfo")
    if not callable(selection):
        notes.append(u"SelectionManager.CreateSelectionInfo 不可用")
        return result
    module = compat.safe_get(session, "_probe_selection_module")
    enum_module = module() if callable(module) else None
    type_enum = compat.safe_get(compat.safe_get(enum_module, "SelectionTypeEnum"),
                                "GeometryEntities")
    if type_enum is None:
        notes.append(u"拿不到 SelectionTypeEnum.GeometryEntities")
        return result
    try:
        info = selection(type_enum)
        info.Ids = [int(geo_id)]
    except Exception:
        notes.append(u"构造 SelectionInfo 失败: %s"
                     % compat.to_text(traceback.format_exc()).strip().splitlines()[-1])
        return result

    adder = compat.safe_get(mesh, "AddAutomaticMethod")
    if not callable(adder):
        notes.append(u"没有 AddAutomaticMethod()")
        return result

    ctrl_name = name or (u"方法控制 - " + compat.to_text(body_row.get("name")))
    try:
        if manager is not None:
            manager.ClearSelection()
    except Exception:
        pass
    try:
        control = adder()
        control.Name = ctrl_name
    except Exception:
        notes.append(u"建控制项失败: %s"
                     % compat.to_text(traceback.format_exc()).strip().splitlines()[-1])
        return result
    result["control"] = control

    try:
        control.Location = info
    except Exception:
        notes.append(u"设 Location 失败: %s"
                     % compat.to_text(traceback.format_exc()).strip().splitlines()[-1])
        return result
    result["scope"] = len(session.selection_ids(compat.safe_get(control, "Location")))
    if result["scope"] <= 0:
        notes.append(u"作用域仍为空（geo id = %s）" % geo_id)
        return result

    method_enum = None
    try:
        from Ansys.Mechanical.DataModel.Enums import MethodType
        method_enum = compat.safe_get(MethodType, method_name)
    except Exception:
        method_enum = None
    if method_enum is None:
        notes.append(u"MethodType.%s 不存在" % method_name)
    else:
        written = set_property_internal(control, u"Method", method_enum)
        if not written["ok"]:
            notes.append(u"写 Method 失败: %s" % written.get("error"))
        result["method"] = compat.to_text(compat.safe_get(control, "Method"))

    if num_divisions is not None:
        written = set_property_internal(control, u"SweepNumberDivisions", int(num_divisions))
        if not written["ok"]:
            notes.append(u"写 SweepNumberDivisions 失败: %s" % written.get("error"))
        result["divisions"] = compat.to_float(compat.safe_get(control, "SweepNumberDivisions"))

    result["ok"] = bool(result["scope"] > 0
                        and result["method"]
                        and u"sweep" in compat.to_text(result["method"]).lower()
                        and (num_divisions is None
                             or result["divisions"] == int(num_divisions)))
    for note in notes:
        log.warn(u"  [方法控制] %s" % note)
    return result


def control_size_stats(controls, cfg):
    """控制项尺寸统计，供诊断/修复用（只统计真正带尺寸的 sizing 类控制）。"""
    sizing = [row for row in controls if row.get("type") in ("body_sizing", "face_sizing")]
    sizes = [row["size_mm"] for row in sizing if (row.get("size_mm") or 0) > 0]
    zero = [row for row in sizing if row.get("size_mm") is not None and row["size_mm"] <= 0]
    no_scope = [row for row in controls if row.get("body_count", 0) == 0]
    sizes.sort()
    median = None
    if sizes:
        middle = len(sizes) // 2
        median = sizes[middle] if len(sizes) % 2 else 0.5 * (sizes[middle - 1] + sizes[middle])
    return {
        "count": len(controls),
        "sizing_count": len(sizing),
        "with_size": len(sizes),
        "min": sizes[0] if sizes else None,
        "max": sizes[-1] if sizes else None,
        "median": median,
        "zero_size": zero,
        "no_scope": no_scope,
        "global_size_mm": compat.to_float(cfg.get("mesh", {}).get("element_size_mm")),
    }
