# -*- coding: utf-8 -*-
"""诊断层：把"数据"翻译成"问题 + 根因 + 可执行动作"。

产出 Finding
------------
::

    {"code": "ZERO_SIZE_CONTROL", "severity": "error", "title": "...",
     "evidence": ["..."], "cause": "...", "actions": ["A_FIX_ZERO_SIZE"],
     "advice": ["..."], "scope": "mesh"}

``actions`` 是 repair 里注册的动作 id（自动执行）；
``advice`` 是给人看的建议（自动做不到、或改几何/改模型才治本）。
"""
from __future__ import division

import fnmatch
import math

from . import compat
from . import metrics as metrics_mod

SEV_ERROR = u"error"
SEV_WARN = u"warning"
SEV_INFO = u"info"

SEV_ORDER = {SEV_ERROR: 0, SEV_WARN: 1, SEV_INFO: 2}

# 指标 -> (问题标题, 默认动作, 根因说明, 人工建议)
_METRIC_RULES = {
    "ElementQuality": (
        u"单元质量 (Element Quality) 触底",
        ["A_SMOOTHING_HIGH"],
        u"多来自几何退化（碎边/尖角）或局部尺寸突变，二阶四面体在薄壁处易塌缩",
        [u"用 SpaceClaim 的 Repair/Defeature 清掉 1e-3 mm 级碎边",
         u"薄壁件优先 Sweep：厚度方向 2 层（Sweep Num Divisions = 2）",
         u"不能用 Sweep 的用 Hex-dominant，或把局部尺寸降到 ≈壁厚"]),
    "Skewness": (
        u"单元畸变 (Skewness) 超限",
        ["A_SMOOTHING_HIGH"],
        u"过渡过急（Growth Rate 偏大）或曲面/小间隙处采样不足",
        [u"把 Growth Rate 降到 1.1~1.2", u"开启 Capture Proximity 或局部 Proximity Sizing"]),
    "JacobianRatio": (
        u"雅可比比 (Jacobian Ratio) 超限",
        ["A_SMOOTHING_HIGH"],
        u"单元严重扭曲：常见于 0 尺寸控制项导致该体未设尺寸、或尖锐薄片",
        [u"检查该体是否有 Body Sizing 且尺寸为 0", u"拆掉/修补细长薄片再划"]),
    "JacobianRatioCornerNodes": (
        u"雅可比比（角节点）超限",
        ["A_SMOOTHING_HIGH"],
        u"角节点处单元退化",
        [u"细化该区域或改进几何质量"]),
    "JacobianRatioGaussPoints": (
        u"雅可比比（高斯点）超限",
        ["A_SMOOTHING_HIGH"],
        u"高斯点处单元退化",
        [u"细化该区域或改进几何质量"]),
    "AspectRatio": (
        u"长宽比 (Aspect Ratio) 过大",
        ["A_SMOOTHING_HIGH"],
        u"薄壁/细长结构的固有特征，或边界层与体积网格过渡过急",
        [u"薄壁实体优先 Sweep：厚度方向 2 层（Sweep Num Divisions = 2）",
         u"不能用 Sweep 的体用 Hex-dominant",
         u"兜底才把该体局部尺寸降到 ≈壁厚（单元数会明显上升）"]),
    "TetCollapse": (
        u"四面体塌缩 (TetCollapse) 超限",
        ["A_SMOOTHING_HIGH"],
        u"四面体退化，通常与碎边/针状面同源",
        [u"几何 defeature", u"提高局部尺寸避免过度细化"]),
    "MaximumCornerAngle": (
        u"最大角 (Maximum Corner Angle) 过大",
        ["A_SMOOTHING_HIGH"],
        u"薄片三角形，几乎是几何缺陷的直接映射",
        [u"修复薄片/台阶面", u"对这些面用 All Triangles 面网格"]),
    "MinTriAngle": (
        u"最小三角角 (Min Tri Angle) 过小",
        ["A_SHELL_ALLTRI"],
        u"面网格出现针状三角形：四边形网格器失败后退化",
        [u"该面改用 All Triangles", u"修补中面碎面/台阶"]),
    "MaxTriAngle": (
        u"最大三角角 (Max Tri Angle) 过大",
        ["A_SHELL_ALLTRI"],
        u"面网格三角形过钝，多与四边形网格器失败同源",
        [u"该面改用 All Triangles"]),
    "WarpingFactor": (
        u"翘曲因子 (Warping Factor) 超限",
        ["A_SMOOTHING_HIGH"],
        u"四边形面网格翘曲，壳/中面几何不平整",
        [u"检查中面重建质量"]),
    "ParallelDeviation": (
        u"平行偏差 (Parallel Deviation) 过大",
        ["A_SMOOTHING_HIGH"],
        u"四边形单元对边不平行，面网格质量差",
        [u"改用 All Triangles 或修补面"]),
}


def diagnose(snapshot, cfg, log=None):
    """对一次快照做全面诊断，返回 findings 列表（按严重度排序）。"""
    findings = []
    body_names = [b["name"] for b in (snapshot.get("bodies") or [])]

    findings.extend(_rule_mesh_failure(snapshot, cfg))
    findings.extend(_rule_unmeshed_bodies(snapshot, cfg))
    findings.extend(_rule_controls(snapshot, cfg))
    findings.extend(_rule_budget(snapshot, cfg))
    findings.extend(_rule_geometry(snapshot, cfg))
    findings.extend(_rule_metrics(snapshot, cfg, body_names))
    findings.extend(_rule_mesh_settings(snapshot, cfg))

    findings.sort(key=lambda f: (SEV_ORDER.get(f.get("severity"), 3), f.get("code", u"")))

    # 策略过滤：用户不采纳"改壳"，就别再给这条建议
    if not (cfg.get("policy") or {}).get("shell_conversion_allowed", False):
        for finding in findings:
            finding["advice"] = [line for line in (finding.get("advice") or [])
                                 if u"改壳" not in line and u"壳单元" not in line]

    if log is not None:
        for finding in findings:
            level = {SEV_ERROR: log.error, SEV_WARN: log.warn, SEV_INFO: log.info}[
                finding.get("severity", SEV_INFO)]
            level(u"诊断[%s] %s" % (finding["code"], finding["title"]))
    return findings


# --------------------------------------------------------------------------
# 规则实现
# --------------------------------------------------------------------------
def _rule_mesh_failure(snapshot, cfg):
    out = []
    error = snapshot.get("error")
    if not error:
        return out
    text = compat.to_text(error)
    evidence = [u"Mechanical 返回: %s" % line for line in text.strip().splitlines()[-6:]]
    owners = _names_in_text(text, [b["name"] for b in (snapshot.get("bodies") or [])])
    if owners:
        evidence.append(u"报错文本中出现的对象: %s" % u", ".join(owners[:8]))
    out.append({
        "code": "MESH_FAILED",
        "severity": SEV_ERROR,
        "title": u"网格生成失败/未生成",
        "evidence": evidence,
        "cause": u"网格器在某个体/面上失败，常见原因：几何自交、极小碎面、"
                 u"该体未设有效尺寸、壳面四边形网格器失败",
        "actions": ["A_CLEAR_REMESH", "A_MESH_UNMESHED"],
        "steps": ["unmeshed_bodies"],
        "advice": [u"若仅个别体失败：对该体单独设 Body Sizing 并改用 AllTriAllTet",
                   u"若壳面失败：把该面改为 All Triangles，或先在 SpaceClaim 修补碎面",
                   u"若几何自交：必须回几何修复，网格层无解"],
        "scope": "mesh",
    })
    return out


def _rule_unmeshed_bodies(snapshot, cfg):
    out = []
    unmeshed = snapshot.get("unmeshed_bodies") or []
    if not unmeshed or snapshot.get("elements", 0) <= 0:
        # 完全没网格时由 MESH_FAILED 统一处理
        return out
    suppressed = set(b["name"] for b in (snapshot.get("bodies") or []) if b.get("suppressed"))
    real = [n for n in unmeshed if n not in suppressed]
    if not real:
        return out
    out.append({
        "code": "BODY_UNMESHED",
        "severity": SEV_ERROR,
        "title": u"%d 个体没有划上网格" % len(real),
        "evidence": [u"未划上网的体(前 12): %s" % u", ".join(real[:12])],
        "cause": u"体尺寸缺失/父级 defeature 过大把体吃掉了/该体几何不可网格化",
        "actions": ["A_MESH_UNMESHED"],
        "steps": ["unmeshed_bodies"],
        "advice": [u"逐个检查这些体的 Body Sizing 是否真的生效（作用域是否为空）",
                   u"必要时把这些体单独分出来，用更小尺寸 + Patch Conforming 四面体重划"],
        "scope": "mesh",
    })
    return out


def _rule_controls(snapshot, cfg):
    out = []
    controls = snapshot.get("controls") or []
    stats = snapshot.get("control_stats") or {}
    sizing_controls = [c for c in controls if c.get("type") in ("body_sizing", "face_sizing")]

    zero = [c for c in sizing_controls if c.get("size_mm") is not None and c["size_mm"] <= 0]
    if zero:
        out.append({
            "code": "ZERO_SIZE_CONTROL",
            "severity": SEV_ERROR,
            "title": u"%d 个控制项的元素尺寸为 0（无效控制）" % len(zero),
            "evidence": [u"%s（%s，作用 %s 个体）"
                         % (c["name"], c.get("category") or c.get("type"), c.get("body_count"))
                         for c in zero[:8]],
            "cause": u"Element Size = 0 在 Mechanical 里等于『程序控制』，该控制项等于不存在；"
                     u"被它覆盖的体实际只能吃全局尺寸，常直接导致雅可比/畸变报警",
            "actions": ["A_FIX_ZERO_SIZE"],
            "steps": ["zero_size_controls"],
            "advice": [u"给这些控制项填一个真实尺寸（推荐：同 role 控制项的中位数，或全局尺寸的 0.5~0.7）"],
            "scope": "mesh",
        })

    no_scope = [c for c in controls if c.get("body_count", 0) == 0 and not c.get("suppressed")]
    if no_scope and controls:
        out.append({
            "code": "CONTROL_NO_SCOPE",
            "severity": SEV_WARN,
            "title": u"%d 个控制项作用域为空" % len(no_scope),
            "evidence": [u"%s（%s）" % (c["name"], c.get("category") or c.get("type"))
                         for c in no_scope[:8]],
            "cause": u"几何更新后体被替换/重命名，控制项失去引用；或本来就建了空控制",
            "actions": ["A_FIX_CONTROL_SCOPE"],
            "steps": ["zero_size_controls"],
            "advice": [u"在配置的 bodies 通配符里补上正确的体名，脚本会自动重新指定作用域"],
            "scope": "mesh",
        })

    # 极端尺寸跳变：同一装配内 1:20 以上的尺寸跳变容易出畸变单元
    sizes = [c["size_mm"] for c in sizing_controls if (c.get("size_mm") or 0) > 0]
    if len(sizes) >= 3:
        smallest, largest = min(sizes), max(sizes)
        if largest / max(smallest, 1e-9) >= 20.0:
            out.append({
                "code": "SIZE_JUMP",
                "severity": SEV_WARN,
                "title": u"局部尺寸跳变 %.0f:1（%.2g mm ~ %.2g mm）" % (largest / smallest, smallest, largest),
                "evidence": [u"最小控制尺寸 %.3g mm，最大 %.3g mm" % (smallest, largest)],
                "cause": u"相邻体尺寸差异过大时，界面过渡区靠 Growth Rate 摊销，容易生成畸变单元",
                "actions": [],
                "steps": [],
                "advice": [u"在跳变处加中间尺寸过渡（相差 > 3 倍就补一级）",
                           u"或把 Growth Rate 降到 1.1"],
                "scope": "mesh",
            })
    return out


def _rule_budget(snapshot, cfg):
    out = []
    budget = snapshot.get("budget") or {}
    basis = budget.get("basis", "elements")
    target = int(budget.get("target") or 0)
    cap = int(budget.get("hard_cap") or 0)
    count = int(snapshot.get(basis) or 0)
    if count <= 0 or not target:
        return out
    if cap and count > cap:
        out.append({
            "code": "BUDGET_EXCEEDED",
            "severity": SEV_ERROR,
            "title": u"%s 超硬上限：%s > %s" % (_basis_name(basis), compat.fmt_num(count),
                                            compat.fmt_num(cap)),
            "evidence": [u"超出 %.1f%%" % (100.0 * (count - cap) / cap),
                         u"按 size^%.2f 关系，整体放大 %.2f 倍可回到上限"
                         % (compat.to_float(budget.get("size_exponent"), 2.2),
                            (count / float(cap)) ** (1.0 / max(compat.to_float(budget.get("size_exponent"), 2.2), 0.5)))],
            "cause": u"局部尺寸过密（常见：某个 Body Sizing 远小于全局尺寸且作用面积大）",
            "actions": ["A_SCALE_BUDGET"],
            "steps": ["budget"],
            "advice": [u"确认最细的 1~2 个控制项是否必要", u"无需求解精度时优先放大体积大的体"],
            "scope": "mesh",
        })
    elif count > target:
        out.append({
            "code": "BUDGET_OVER_TARGET",
            "severity": SEV_WARN,
            "title": u"%s 超目标：%s > %s" % (_basis_name(basis), compat.fmt_num(count),
                                          compat.fmt_num(target)),
            "evidence": [u"超出目标 %.1f%%" % (100.0 * (count - target) / target)],
            "cause": u"整体尺寸偏密",
            "actions": [],
            "steps": ["budget"],
            "advice": [u"如求解时间可接受可保持；否则把大面积非承力件的尺寸放大约 5~10%"],
            "scope": "mesh",
        })
    return out


def _basis_name(basis):
    return u"节点数" if basis == "nodes" else u"单元数"


def _rule_geometry(snapshot, cfg):
    out = []
    geometry = snapshot.get("geometry") or {}
    min_edge = geometry.get("min_edge_mm")
    global_size = compat.to_float(geometry.get("global_size_mm"), 0) or compat.to_float(
        (cfg.get("mesh") or {}).get("element_size_mm"), 1.0)
    tolerance = compat.to_float((cfg.get("mesh") or {}).get("defeature_tolerance_mm"), 0) or 0.0
    if min_edge and global_size:
        ratio = min_edge / global_size
        if ratio < 0.005:
            scan = geometry.get("edge_scan") or {}
            hits = scan.get("hits") or []
            evidence = [u"几何最小边长 = %.4g mm，仅为全局尺寸 %.4g mm 的 %.4f%%"
                        % (min_edge, global_size, 100.0 * ratio),
                        u"读数来源: %s" % geometry.get("min_edge_source", u"未知")]
            for hit in hits[:6]:
                evidence.append(u"碎边 %.4g mm 属于: %s" % (hit.get("length_mm") or 0,
                                                       hit.get("owner") or hit.get("name") or u"?"))
            out.append({
                "code": "DEGENERATE_GEOMETRY",
                "severity": SEV_ERROR if ratio < 0.001 else SEV_WARN,
                "title": u"几何存在退化小边（%.3g mm）" % min_edge,
                "evidence": evidence,
                "cause": u"CAD 里的碎边/针状面/微小圆角，网格器必须在这些位置生成极小单元，"
                         u"直接造成 ElementQuality/TetCollapse/AspectRatio/夹角 全线恶化",
                "actions": ["A_DEFEATURE_TOGGLE"] if tolerance < global_size * 0.05 else [],
                "steps": ["defeature"],
                "advice": [u"根治：SpaceClaim -> Repair -> 合并/删除 < 0.1 mm 的边与面",
                           u"次选：把 Defeature Tolerance 设到 0.5~1.0 mm（脚本会尝试），"
                           u"但要确认被去掉的特征不是密封面/接触面",
                           u"若碎边位于接触对：必须回几何修，否则接触会丢"],
                "scope": "geometry",
            })
    if tolerance and global_size and tolerance > global_size * 0.5:
        out.append({
            "code": "DEFEATURE_TOO_LARGE",
            "severity": SEV_WARN,
            "title": u"Defeature Tolerance (%.3g mm) 相对全局尺寸过大" % tolerance,
            "evidence": [u"全局尺寸 %.3g mm，defeature %.3g mm" % (global_size, tolerance)],
            "cause": u"过大的 defeature 会吞掉小特征（螺栓孔、密封台阶），"
                     u"网格变好看但物理结果会变",
            "actions": [],
            "steps": [],
            "advice": [u"把 Defeature Tolerance 控制在全局尺寸的 2%~5%",
                       u"重要特征所在的体单独设小尺寸，别让 defeature 接管"],
            "scope": "mesh",
        })
    shells = geometry.get("shell_bodies") or []
    if shells:
        out.append({
            "code": "SHELL_PRESENT",
            "severity": SEV_INFO,
            "title": u"存在壳体（厚度 > 0）：%d 个" % len(shells),
            "evidence": [u"%s" % u", ".join(shells[:8])],
            "cause": u"壳面走四边形优先路径，四边形网格器失败时会退化成针状三角形",
            "actions": [],
            "steps": [],
            "advice": [u"对失败的面用 Face Meshing = All Triangles",
                       u"或对该面设 AllTriAllTet 方法"],
            "scope": "mesh",
        })
        # 按 policy 检查：哪些壳体不在允许范围内（shell_policy=keep_all 时全部允许）
        policy = cfg.get("policy") or {}
        patterns = policy.get("shell_patterns") or []
        if policy.get("shell_policy", "keep_all") == "patterns" and patterns:
            outside = [s for s in shells
                       if not any(fnmatch.fnmatch(s, p) or fnmatch.fnmatch(
                           s.split(u"\\")[-1], p) for p in patterns)]
            if outside:
                out.append({
                    "code": "SHELL_SCOPE_UNEXPECTED",
                    "severity": SEV_WARN,
                    "title": u"%d 个壳体不在允许范围内（按你的规则只有波纹板用壳）"
                             % len(outside),
                    "evidence": [u"非波纹板壳体：%s" % u", ".join(outside[:10])],
                    "cause": u"可能是历史遗留的壳，或早期试验后没清理；"
                             u"实体+壳重叠会造成刚度重复计算",
                    "actions": [],
                    "steps": [],
                    "advice": [u"逐个确认：这些壳是不是有意保留的；"
                               u"若是遗留，抑制它们并恢复原实体",
                               u"若确实需要（如两侧隔断中面），把 policy.shell_patterns "
                               u"里补上对应通配符，报告就不会再提醒"],
                    "scope": "model",
                })
    return out


def _rule_metrics(snapshot, cfg, body_names):
    out = []
    rows = snapshot.get("rows") or {}
    min_edge = (snapshot.get("geometry") or {}).get("min_edge_mm")
    global_size = compat.to_float((snapshot.get("geometry") or {}).get("global_size_mm"), 0)
    degenerate = bool(min_edge and global_size and min_edge / global_size < 0.005)
    has_shells = bool((snapshot.get("geometry") or {}).get("shell_bodies"))
    SURFACE_METRICS = ("MinTriAngle", "MaxTriAngle", "ParallelDeviation", "WarpingFactor")

    for name, row in rows.items():
        status = row.get("status")
        if status not in (metrics_mod.STATUS_WARN, metrics_mod.STATUS_FAIL):
            continue
        rule = _METRIC_RULES.get(name)
        if rule is None:
            continue
        title, actions, cause, advice = rule
        severity = SEV_ERROR if status == metrics_mod.STATUS_FAIL else SEV_WARN
        evidence = [u"最差值（%s）= %s%s，%s"
                    % (u"最小值" if row.get("worst_kind") == "min" else u"最大值",
                       compat.fmt_num(row.get("worst")),
                       (u" " + row.get("unit", "")) if row.get("unit") else u"",
                       u"已超 error 限" if status == metrics_mod.STATUS_FAIL else u"已超 warning 限"),
                    u"阈值: warn %s / error %s（来源 %s）"
                    % (compat.fmt_num(row.get("warn")), compat.fmt_num(row.get("error")),
                       row.get("source") or u"config")]
        if row.get("avg") is not None:
            evidence.append(u"全模型平均 = %s，标准差 = %s"
                            % (compat.fmt_num(row.get("avg")), compat.fmt_num(row.get("std"))))
        if degenerate:
            evidence.append(u"与 DEGENERATE_GEOMETRY 同源（最小边长 %.3g mm）" % min_edge)
            actions = ["A_DEFEATURE_TOGGLE"]
            steps = ["defeature"]
        elif has_shells and name in SURFACE_METRICS:
            steps = ["quad_shell"]
        else:
            steps = ["smoothing"]
        out.append({
            "code": "METRIC_" + name.upper(),
            "severity": severity,
            "title": title,
            "evidence": evidence,
            "cause": cause,
            "actions": list(actions),
            "steps": steps,
            "advice": list(advice),
            "scope": "mesh",
        })
    return out


def _rule_mesh_settings(snapshot, cfg):
    out = []
    mesh_cfg = cfg.get("mesh") or {}
    rows = snapshot.get("rows") or {}
    has_problem = any(row.get("status") in (metrics_mod.STATUS_WARN, metrics_mod.STATUS_FAIL)
                      for row in rows.values())
    if not has_problem:
        return out

    smoothing = compat.to_text(mesh_cfg.get("smoothing", u"High"))
    iterations = int(compat.to_float(mesh_cfg.get("smoothing_iterations"), 0) or 0)
    if smoothing.lower() != "high" or iterations < 10:
        out.append({
            "code": "SMOOTHING_LOW",
            "severity": SEV_INFO,
            "title": u"平滑设置偏保守（Smoothing=%s，迭代 %d）" % (smoothing, iterations),
            "evidence": [u"Smoothing=%s，SmoothingIterations=%d" % (smoothing, iterations)],
            "cause": u"Mechanical 默认 Medium/5 次，对装配体平均质量有明显损失",
            "actions": ["A_SMOOTHING_HIGH"],
            "steps": ["smoothing"],
            "advice": [u"建议 Smoothing=High + 迭代 10（脚本会自动设置并验证）"],
            "scope": "mesh",
        })

    if not (mesh_cfg.get("capture_proximity") or False):
        body_count = len([b for b in (snapshot.get("bodies") or []) if not b.get("suppressed")])
        skew = rows.get("Skewness") or {}
        if body_count >= 50 and skew.get("status") in (metrics_mod.STATUS_WARN, metrics_mod.STATUS_FAIL):
            out.append({
                "code": "PROXIMITY_OFF",
                "severity": SEV_INFO,
                "title": u"未开启 Capture Proximity（%d 个体装配）" % body_count,
                "evidence": [u"Skewness 最差 %s" % compat.fmt_num(skew.get("worst"))],
                "cause": u"关闭近距捕捉时，窄缝/贴合面处只能靠曲率捕捉，容易出畸变",
                "actions": ["A_PROXIMITY_ON"],
                "steps": ["proximity"],
                "advice": [u"开启后单元数通常涨 5%~20%，脚本会检查预算后再开"],
                "scope": "mesh",
            })

    if not (mesh_cfg.get("capture_curvature") or False):
        out.append({
            "code": "CURVATURE_OFF",
            "severity": SEV_INFO,
            "title": u"未开启 Capture Curvature",
            "evidence": [u"CaptureCurvature=False"],
            "cause": u"曲面/波纹结构靠曲率捕捉保证贴合，关闭后曲面精度靠运气",
            "actions": ["A_CURVATURE_ON"],
            "steps": ["smoothing"],
            "advice": [u"对含曲面/波纹的模型建议开启"],
            "scope": "mesh",
        })

    shells = (snapshot.get("geometry") or {}).get("shell_bodies") or []
    quad = bool(mesh_cfg.get("quad_mesh", True))
    tri = rows.get("MinTriAngle") or {}
    if shells and quad and tri.get("status") in (metrics_mod.STATUS_WARN, metrics_mod.STATUS_FAIL):
        out.append({
            "code": "SHELL_QUAD_FAIL",
            "severity": SEV_WARN,
            "title": u"壳面四边形网格器失败风险（%d 个壳体）" % len(shells),
            "evidence": [u"MinTriAngle 最差 %s deg" % compat.fmt_num(tri.get("worst"))],
            "cause": u"四边形优先策略在中面拓扑有碎面/台阶时必然失败，退化成针状三角形",
            "actions": ["A_SHELL_ALLTRI"],
            "steps": ["quad_shell"],
            "advice": [u"对失败面改用 All Triangles，或修补中面拓扑"],
            "scope": "mesh",
        })
    return out


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------
def _names_in_text(text, candidates):
    found = []
    lowered = compat.to_text(text)
    for name in candidates:
        leaf = name.split(u"\\")[-1]
        if leaf and leaf in lowered:
            found.append(name)
    return found


def has_errors(findings):
    return any(f.get("severity") == SEV_ERROR for f in findings)


def action_ids(findings):
    out = []
    for finding in findings:
        for action in finding.get("actions") or []:
            if action not in out:
                out.append(action)
    return out


def steps_of(findings):
    out = []
    for finding in findings:
        for step in finding.get("steps") or []:
            if step not in out:
                out.append(step)
    return out


def advice_lines(findings, scope=None):
    """把 findings 的 advice 汇总成去重列表。"""
    out = []
    for finding in findings:
        if scope is not None and finding.get("scope") != scope:
            continue
        for line in finding.get("advice") or []:
            if line not in out:
                out.append(line)
    return out


def manual_scope_findings(findings):
    """自动纠正改不动、必须人工处理的 findings（scope 为 geometry/model）。"""
    return [f for f in findings if f.get("scope") in ("geometry", "model")]
