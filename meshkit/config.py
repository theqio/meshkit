# -*- coding: utf-8 -*-
"""需求配置：默认值 -> 预设 -> 用户文件 三层合并，并做结构与取值范围校验。

配置即"我的要求"：网格尺寸、局部控制、质量阈值、单元预算、纠正策略。
全部字段都可省略，缺省用内置默认值。
"""
from __future__ import division

import os
import copy

from . import compat

try:
    import json
except ImportError:  # pragma: no cover
    json = None

# --------------------------------------------------------------------------
# 内置默认 = presets/generic_default.json 的内容（避免运行期依赖文件缺失）
# --------------------------------------------------------------------------
DEFAULTS = {
    "schema_version": 1,
    "preset": "generic_default",
    "meta": {"model_label": "", "note": ""},
    "project": {"wbpj": ""},
    "run": {
        "dry_run": False,
        "auto_repair": True,
        "max_rounds": 4,
        "keep_best": True,
        "save_project": False,
        "console_verbose": True,
        "report_dir": "",
        "element_sampling": {"enabled": False, "max_elements": 20000},
    },
    "mesh": {
        "element_size_mm": 10.0,
        "use_adaptive_sizing": False,
        "resolution": 2,
        "defeature_tolerance_mm": 0.0,
        "automatic_mesh_based_defeaturing": True,
        "capture_curvature": True,
        "curvature_normal_angle_deg": 0.0,
        "capture_proximity": False,
        "growth_rate": 1.2,
        "transition_ratio": 0.272,
        "smoothing": "High",
        "smoothing_iterations": 10,
        "use_post_smoothing": True,
        "shape_checking": True,
        "topology_checking": True,
        "element_order": "ProgramControlled",
        "quad_mesh": True,
        "straight_sided_elements": False,
        "use_automatic_inflation": False,
        "physics_preference": "Mechanical",
    },
    "budget": {
        "basis": "elements",
        "target": 1500000,
        "hard_cap": 2000000,
        "size_exponent": 2.2,
    },
    "controls": [],
    "quality": {
        "use_mechanical_limits": True,
        "collect_info_metrics": False,
        "metrics": {},
    },
    "repair": {
        "ladder": [
            "smoothing", "zero_size_controls", "defeature",
            "unmeshed_bodies", "quad_shell", "proximity", "budget",
        ],
        "defeature_factor_ladder": [0.01, 0.03, 0.06],
        "budget_scale_max": 1.8,
        "budget_scale_min": 0.7,
        "proximity_element_growth": 1.15,
        "allow_element_order_change": False,
    },
    "advice": {"top_bodies": 10},
    # 策略约束：把你"不采用的做法"写死在配置里，工具就不会反复建议它
    "policy": {
        "shell_conversion_allowed": False,
        "shell_policy": "keep_all",
        "shell_patterns": ["波纹板*"],
        "note": "只有波纹板用壳（现有中面），其他结构保持实体",
    },
    # 薄板优先 Sweep：厚度方向分 2 层
    "sweep": {
        "enabled": True,
        "num_divisions": 2,
        "free_face_mesh_type": "AllTri",
        "auto_from_sampling": True,
        "min_avg_ratio": 8.0,
        "min_samples": 5,
        "bodies": [],
        "source_faces": {},
    },
}

# 质量指标元数据：direction = 越大越好(high) / 越小越好(low)；unit 仅用于报告
METRIC_META = {
    "ElementQuality":           {"direction": "high", "unit": ""},
    "Skewness":                 {"direction": "low",  "unit": ""},
    "JacobianRatio":            {"direction": "low",  "unit": ""},
    "JacobianRatioCornerNodes": {"direction": "high", "unit": ""},
    "JacobianRatioGaussPoints": {"direction": "high", "unit": ""},
    "AspectRatio":              {"direction": "low",  "unit": ""},
    "TetCollapse":              {"direction": "high", "unit": ""},
    "MaximumCornerAngle":       {"direction": "low",  "unit": "deg"},
    "MinTriAngle":              {"direction": "high", "unit": "deg"},
    "MaxTriAngle":              {"direction": "low",  "unit": "deg"},
    "WarpingFactor":            {"direction": "low",  "unit": ""},
    "ParallelDeviation":        {"direction": "low",  "unit": "deg"},
    "MinEdgeLength":            {"direction": "high", "unit": "mm"},
    "MaxEdgeLength":            {"direction": "low",  "unit": "mm"},
    "CharacteristicLength":     {"direction": "high", "unit": "mm"},
    "OrthogonalAngle":          {"direction": "high", "unit": ""},
    "WarpingAngle":             {"direction": "low",  "unit": "deg"},
}

# 报告里不做判定、只列数值的指标
INFO_ONLY_METRICS = ("MinEdgeLength", "MaxEdgeLength", "CharacteristicLength",
                     "OrthogonalAngle", "WarpingAngle")

SMOOTHING_NAMES = {"programcontrolled": 0, "programcontrolled ": 0,
                   "low": 1, "medium": 2, "high": 3}
SMOOTHING_BY_INDEX = {0: "ProgramControlled", 1: "Low", 2: "Medium", 3: "High"}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESET_DIR = os.path.join(ROOT, "presets")


# --------------------------------------------------------------------------
# 合并 / 读取
# --------------------------------------------------------------------------
def deep_merge(base, override):
    """override 覆盖 base，dict 递归合并，list 整体替换。"""
    if not isinstance(base, dict) or not isinstance(override, dict):
        return copy.deepcopy(override)
    out = copy.deepcopy(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _read_json(path):
    if json is None:
        raise RuntimeError("当前 Python 缺少 json 模块")
    text = compat.read_text(path)
    if text is None:
        raise IOError(u"无法读取: %s" % path)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise RuntimeError(u"配置根节点必须是 JSON 对象: %s" % path)
    return data


def load_preset(name, preset_dir=None):
    preset_dir = preset_dir or PRESET_DIR
    if not name:
        return {}
    path = os.path.join(preset_dir, name + ".json")
    if not os.path.isfile(path):
        raise IOError(u"找不到预设: %s" % path)
    return _read_json(path)


def load(path=None, preset_dir=None, overrides=None):
    """加载配置。

    path        : 用户 JSON（可省略；省略时只用内置默认+预设）
    preset_dir  : 预设目录
    overrides   : dict，代码级覆盖（入口脚本用它传 dry_run 等）
    """
    user = {}
    if path:
        if not os.path.isfile(path):
            raise IOError(u"找不到配置文件: %s" % path)
        user = _read_json(path)

    cfg = deep_merge(DEFAULTS, {})
    preset_name = ((overrides or {}).get("preset") or user.get("preset")
                   or DEFAULTS.get("preset"))
    if preset_name:
        merged_preset = deep_merge(cfg, load_preset(preset_name, preset_dir))
        cfg = merged_preset
    cfg = deep_merge(cfg, user)
    if overrides:
        cfg = deep_merge(cfg, overrides)

    cfg["_sources"] = {
        "preset": preset_name,
        "user_file": os.path.abspath(path) if path else None,
        "preset_dir": os.path.abspath(preset_dir or PRESET_DIR),
    }
    normalize(cfg)
    return cfg


# --------------------------------------------------------------------------
# 规整 / 校验
# --------------------------------------------------------------------------
def normalize(cfg):
    """就地修正可容忍的写法差异（大小写、字符串数字、尺寸单位）。"""
    mesh = cfg.setdefault("mesh", {})

    # 元素尺寸：接受 "36 mm" / 36 / {"value":36,"unit":"mm"}
    mesh["element_size_mm"] = _as_mm(mesh.get("element_size_mm"), 1.0)
    mesh["defeature_tolerance_mm"] = _as_mm(mesh.get("defeature_tolerance_mm"), 0.0)
    mesh["curvature_normal_angle_deg"] = compat.to_float(
        mesh.get("curvature_normal_angle_deg"), 0.0)

    smoothing = mesh.get("smoothing")
    if isinstance(smoothing, compat.integer_types) or isinstance(smoothing, float):
        mesh["smoothing"] = SMOOTHING_BY_INDEX.get(int(smoothing), "High")
    elif isinstance(smoothing, compat.string_types):
        key = compat.to_text(smoothing).strip().lower()
        mesh["smoothing"] = {"low": "Low", "medium": "Medium", "high": "High",
                             "programcontrolled": "ProgramControlled"}.get(key, "High")
    else:
        mesh["smoothing"] = "High"

    order = compat.to_text(mesh.get("element_order", "ProgramControlled")).strip()
    mesh["element_order"] = {"first": "First", "second": "Second",
                             "programcontrolled": "ProgramControlled",
                             "program controlled": "ProgramControlled",
                             "linear": "First", "quadratic": "Second"}.get(
        order.lower(), "ProgramControlled")

    budget = cfg.setdefault("budget", {})
    budget["basis"] = compat.to_text(budget.get("basis", "elements")).strip().lower()
    if budget["basis"] not in ("elements", "nodes"):
        budget["basis"] = "elements"
    budget["target"] = int(compat.to_float(budget.get("target"), 0) or 0)
    budget["hard_cap"] = int(compat.to_float(budget.get("hard_cap"), 0) or 0)
    if budget["hard_cap"] and budget["target"] > budget["hard_cap"]:
        budget["target"], budget["hard_cap"] = budget["hard_cap"], budget["target"]
    budget["size_exponent"] = compat.to_float(budget.get("size_exponent"), 2.2)

    # 控制项
    controls = []
    for index, raw in enumerate(cfg.get("controls") or []):
        spec = _normalize_control(raw, index)
        if spec is not None:
            controls.append(spec)
    cfg["controls"] = controls

    # 质量指标
    quality = cfg.setdefault("quality", {})
    metrics = {}
    for name, raw in (quality.get("metrics") or {}).items():
        meta = METRIC_META.get(name, {"direction": "low", "unit": ""})
        direction = compat.to_text((raw or {}).get("direction", meta["direction"])).lower()
        if direction not in ("high", "low"):
            direction = meta["direction"]
        warn = compat.to_float((raw or {}).get("warn"))
        error = compat.to_float((raw or {}).get("error"))
        metrics[name] = {"direction": direction, "warn": warn, "error": error,
                         "unit": meta.get("unit", "")}
    quality["metrics"] = metrics
    quality["use_mechanical_limits"] = bool(quality.get("use_mechanical_limits", True))
    quality["collect_info_metrics"] = bool(quality.get("collect_info_metrics", False))

    repair = cfg.setdefault("repair", {})
    ladder = repair.get("ladder")
    if not isinstance(ladder, list):
        repair["ladder"] = DEFAULTS["repair"]["ladder"]
    factors = repair.get("defeature_factor_ladder")
    if not isinstance(factors, list) or not factors:
        repair["defeature_factor_ladder"] = DEFAULTS["repair"]["defeature_factor_ladder"]
    repair["budget_scale_max"] = compat.to_float(repair.get("budget_scale_max"), 1.8)
    repair["budget_scale_min"] = compat.to_float(repair.get("budget_scale_min"), 0.7)
    repair["proximity_element_growth"] = compat.to_float(
        repair.get("proximity_element_growth"), 1.15)

    run = cfg.setdefault("run", {})
    run["max_rounds"] = max(1, int(compat.to_float(run.get("max_rounds"), 4) or 4))
    run["dry_run"] = bool(run.get("dry_run", False))
    run["auto_repair"] = bool(run.get("auto_repair", True))
    run["keep_best"] = bool(run.get("keep_best", True))
    if not run.get("report_dir"):
        run["report_dir"] = os.path.join(ROOT, "reports")
    cfg["advice"] = cfg.get("advice") or {"top_bodies": 10}
    policy = cfg.setdefault("policy", {})
    policy["shell_conversion_allowed"] = bool(policy.get("shell_conversion_allowed", False))
    shell_policy = compat.to_text(policy.get("shell_policy", "keep_all")).strip().lower()
    policy["shell_policy"] = shell_policy if shell_policy in ("keep_all", "patterns") else "keep_all"
    patterns = policy.get("shell_patterns")
    if not isinstance(patterns, list):
        policy["shell_patterns"] = list(DEFAULTS["policy"]["shell_patterns"])

    sweep = cfg.setdefault("sweep", {})
    sweep["enabled"] = bool(sweep.get("enabled", True))
    sweep["num_divisions"] = max(1, int(compat.to_float(sweep.get("num_divisions"), 2) or 2))
    sweep["free_face_mesh_type"] = compat.to_text(
        sweep.get("free_face_mesh_type", "AllTri")).strip() or "AllTri"
    sweep["auto_from_sampling"] = bool(sweep.get("auto_from_sampling", True))
    sweep["min_avg_ratio"] = compat.to_float(sweep.get("min_avg_ratio"), 8.0) or 8.0
    sweep["min_samples"] = int(compat.to_float(sweep.get("min_samples"), 5) or 5)
    if not isinstance(sweep.get("bodies"), list):
        sweep["bodies"] = []
    if not isinstance(sweep.get("source_faces"), dict):
        sweep["source_faces"] = {}
    return cfg


def _as_mm(value, default):
    """把尺寸写法统一成 mm 浮点。支持 36 / "36" / "36mm" / {"value":36,"unit":"cm"}"""
    if value is None:
        return float(default)
    if isinstance(value, dict):
        number = compat.to_float(value.get("value"), default)
        unit = compat.to_text(value.get("unit", "mm")).strip().lower()
    elif isinstance(value, compat.string_types):
        text = compat.to_text(value).strip().lower().replace(" ", "")
        unit = "mm"
        for candidate in ("mm", "cm", "m", "in", "um"):
            if text.endswith(candidate):
                unit = candidate
                text = text[:-len(candidate)]
                break
        number = compat.to_float(text, default)
    else:
        number = compat.to_float(value, default)
        unit = "mm"
    if number is None:
        number = float(default)
    scale = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "um": 0.001}.get(unit, 1.0)
    return float(number) * scale


CONTROL_TYPES = {
    "body_sizing": "body_sizing",
    "bodysizing": "body_sizing",
    "sizing": "body_sizing",
    "face_sizing": "face_sizing",
    "facesizing": "face_sizing",
    "automatic_method": "automatic_method",
    "automaticmethod": "automatic_method",
    "method": "automatic_method",
    "face_meshing": "face_meshing",
    "facemeshing": "face_meshing",
    "refinement": "refinement",
    "inflation": "inflation",
}

METHOD_ALIASES = {
    "automatic": "Automatic",
    "tetrahedrons": "Tetrahedrons",
    "tetrahedron": "Tetrahedrons",
    "hexdominant": "HexDominant",
    "hex_dominant": "HexDominant",
    "sweep": "Sweep",
    "alltrialltet": "AllTriAllTet",
    "all_tri_all_tet": "AllTriAllTet",
    "hexa": "Hexahedrons",
    "hexahedrons": "Hexahedrons",
    "quadrilateral": "Quadrilateral",
    "cartesian": "Cartesian",
    "multizone": "MultiZone",
    "layeredtet": "LayeredTet",
}

ALGORITHM_ALIASES = {
    "patchconforming": "PatchConforming",
    "patch_conforming": "PatchConforming",
    "patchindependent": "PatchIndependent",
    "patch_independent": "PatchIndependent",
}


def _normalize_control(raw, index):
    if not isinstance(raw, dict):
        return None
    ctype = CONTROL_TYPES.get(compat.to_text(raw.get("type", "")).strip().lower())
    if ctype is None:
        return None
    name = compat.to_text(raw.get("name") or u"control_%d" % (index + 1)).strip()
    spec = {
        "name": name,
        "type": ctype,
        "size_mm": None,
        "method": None,
        "algorithm": None,
        "bodies": list(raw.get("bodies") or []),
        "role": compat.to_text(raw.get("role", "")),
        "reuse_existing": bool(raw.get("reuse_existing", True)),
        "create_if_missing": bool(raw.get("create_if_missing", True)),
        "suppress": bool(raw.get("suppress", False)),
    }
    if raw.get("size_mm") is not None:
        spec["size_mm"] = _as_mm(raw.get("size_mm"), 0.0)
    if raw.get("method"):
        key = compat.to_text(raw["method"]).strip().lower()
        spec["method"] = METHOD_ALIASES.get(key, compat.to_text(raw["method"]).strip())
    if raw.get("algorithm"):
        key = compat.to_text(raw["algorithm"]).strip().lower()
        spec["algorithm"] = ALGORITHM_ALIASES.get(key, compat.to_text(raw["algorithm"]).strip())
    return spec


def validate(cfg):
    """返回问题列表（unicode）。空列表 = 没问题。"""
    problems = []
    mesh = cfg.get("mesh", {})
    size = compat.to_float(mesh.get("element_size_mm"), 0)
    if not size or size <= 0:
        problems.append(u"mesh.element_size_mm 必须是正数（当前 %s）" % compat.fmt_num(size))

    budget = cfg.get("budget", {})
    target = int(budget.get("target") or 0)
    cap = int(budget.get("hard_cap") or 0)
    if target <= 0:
        problems.append(u"budget.target 必须 > 0（当前 %s）" % target)
    if cap and cap < target * 0.5:
        problems.append(u"budget.hard_cap(%d) 远小于 target(%d)，预算配置可能写错" % (cap, target))

    names = {}
    for spec in cfg.get("controls") or []:
        key = (spec["name"], spec["type"])
        if key in names:
            problems.append(u"控制项重复: %s（类型 %s）" % key)
        names[key] = True
        if spec["type"] == "body_sizing":
            if spec["size_mm"] is None or spec["size_mm"] <= 0:
                problems.append(u"控制项 '%s' 的 size_mm 应 > 0（<=0 会被 Mechanical 当作无效控制）"
                                % spec["name"])
        if (spec["type"] == "automatic_method" and not spec["method"]
                and spec.get("create_if_missing", True)):
            problems.append(u"控制项 '%s' 是 method 类型但没写 method（新建时需要）" % spec["name"])

    for name, metric in (cfg.get("quality", {}).get("metrics") or {}).items():
        warn, error = metric.get("warn"), metric.get("error")
        if warn is None or error is None:
            continue
        high = metric.get("direction") == "high"
        if high and not (error < warn):
            problems.append(u"质量阈值 '%s' 方向为 high（越大越好），应满足 error < warn（当前 error=%s, warn=%s）"
                            % (name, error, warn))
        if not high and not (warn < error):
            problems.append(u"质量阈值 '%s' 方向为 low（越小越好），应满足 warn < error（当前 warn=%s, error=%s）"
                            % (name, warn, error))

    if not cfg.get("controls"):
        problems.append(u"未定义任何局部网格控制（controls 为空）——只靠全局尺寸也能划，"
                        u"但承力件/薄板区域通常需要局部尺寸")
    ladder = cfg.get("repair", {}).get("ladder") or []
    known = set(["smoothing", "zero_size_controls", "defeature", "unmeshed_bodies",
                 "quad_shell", "proximity", "budget"])
    for step in ladder:
        if step not in known:
            problems.append(u"repair.ladder 未知步骤: %s（可用: %s）"
                            % (step, ", ".join(sorted(known))))
    return problems


def metric_config(cfg):
    """合并 METRIC_META 与用户阈值，返回 {name: {direction,warn,error,unit}}"""
    out = {}
    user = cfg.get("quality", {}).get("metrics") or {}
    for name, meta in METRIC_META.items():
        entry = {"direction": meta["direction"], "unit": meta.get("unit", ""),
                 "warn": None, "error": None}
        if name in user:
            entry["direction"] = user[name].get("direction") or meta["direction"]
            entry["warn"] = user[name].get("warn")
            entry["error"] = user[name].get("error")
        out[name] = entry
    return out


def metric_names_to_collect(cfg):
    """这次真正要读的指标清单。

    每读一个指标，Mechanical 都要重算一次云图，百万级单元时很贵，所以：
      * 只读**配了阈值的**指标（默认 12 个）；
      * 纯参考指标（MinEdgeLength 等）默认不读，`quality.collect_info_metrics=true` 才读。
    """
    quality = cfg.get("quality") or {}
    names = []
    for name in (quality.get("metrics") or {}):
        if name in METRIC_META and name not in names:
            names.append(name)
    if not names:
        # 用户没配任何阈值 -> 读一套通用的核心指标
        names = ["ElementQuality", "Skewness", "JacobianRatio", "JacobianRatioCornerNodes",
                 "JacobianRatioGaussPoints", "AspectRatio", "TetCollapse",
                 "MaximumCornerAngle", "MinTriAngle", "MaxTriAngle"]
    if quality.get("collect_info_metrics"):
        for name in INFO_ONLY_METRICS:
            if name not in names:
                names.append(name)
    order = ["ElementQuality", "Skewness", "JacobianRatio", "JacobianRatioCornerNodes",
             "JacobianRatioGaussPoints", "AspectRatio", "TetCollapse",
             "MaximumCornerAngle", "MinTriAngle", "MaxTriAngle", "WarpingFactor",
             "ParallelDeviation"]
    head = [n for n in order if n in names]
    tail = [n for n in names if n not in head]
    return head + sorted(tail)


def summarize(cfg):
    """人类可读的配置摘要（写进报告"生效需求"一节）。"""
    mesh = cfg["mesh"]
    budget = cfg["budget"]
    rows = [
        (u"全局单元尺寸", u"%s mm" % compat.fmt_num(mesh["element_size_mm"])),
        (u"Use Adaptive Sizing", compat.to_text(mesh["use_adaptive_sizing"])),
        (u"Resolution", compat.to_text(mesh["resolution"])),
        (u"Defeature Tolerance", u"%s mm" % compat.fmt_num(mesh["defeature_tolerance_mm"])),
        (u"Automatic Mesh Based Defeaturing", compat.to_text(mesh["automatic_mesh_based_defeaturing"])),
        (u"Capture Curvature", compat.to_text(mesh["capture_curvature"])),
        (u"Curvature Normal Angle", u"%s deg" % compat.fmt_num(mesh["curvature_normal_angle_deg"])),
        (u"Capture Proximity", compat.to_text(mesh["capture_proximity"])),
        (u"Growth Rate", compat.fmt_num(mesh["growth_rate"])),
        (u"Transition Ratio", compat.fmt_num(mesh["transition_ratio"])),
        (u"Smoothing", u"%s（迭代 %s）" % (mesh["smoothing"], compat.fmt_num(mesh["smoothing_iterations"]))),
        (u"Element Order", compat.to_text(mesh["element_order"])),
        (u"单元预算", u"%s，目标 %s，上限 %s" % (
            budget["basis"], compat.fmt_num(budget["target"]), compat.fmt_num(budget["hard_cap"]))),
        (u"局部控制项", u"%d 个" % len(cfg["controls"])),
        (u"自动纠正", u"%s（最多 %d 轮）" % (
            u"开" if cfg["run"]["auto_repair"] else u"关", cfg["run"]["max_rounds"])),
    ]
    return rows
