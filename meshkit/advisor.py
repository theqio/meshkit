# -*- coding: utf-8 -*-
"""顾问层：自动纠正用尽后，把"必须人工决策的事"整理成清单 + 可执行脚本。

产出三样东西
------------
1. 结构化的 ``items`` 列表（报告直接渲染成表）；
2. Markdown 片段；
3. 一个独立的 ``apply_manual_fix_<时间戳>.py``：里面每个操作块都带 ``ENABLE = False``
   开关与风险说明，用户确认后自己打开运行——脚本**不会**自动执行它。
"""
from __future__ import division

import os
import traceback

from . import compat
from . import config as config_mod
from . import diagnose as diagnose_mod

RISK_TEXT = {
    "geometry": u"会改动几何/拓扑，可能影响接触面与密封面",
    "model": u"会改动模型结构（体/连接），会影响物理结果",
    "mesh": u"只改网格设置，安全",
}

GEOMETRY_PLAYBOOK = [
    (u"清碎边碎面",
     u"SpaceClaim -> Repair -> Small Edges / Small Faces，阈值取全局尺寸的 0.2%~0.5%，"
     u"合并或删除后重新导入 Mechanical",
     u"最治本；能一次性消掉 ElementQuality/TetCollapse/AspectRatio/夹角 的极值"),
    (u"中面（壳）拓扑整理",
     u"对波纹板中面：Repair -> Split/Stitch，保证每个面是单一连续曲面，去掉台阶与碎面",
     u"四边形网格器失败几乎都是中面拓扑不干净导致的"),
    (u"薄壁实体改 Sweep（不改成壳）",
     u"对薄板：Mesh Control -> Method = Sweep，Source = 薄板的一个大面，"
     u"厚度方向分 2 层（Sweep Num Divisions = 2）",
     u"薄壁用四面体必然被压扁（边比 20~60）；Sweep 让厚度方向只有 2 层，"
     u"质量与单元数同时改善，且**仍是实体单元**，不改模型拓扑"),
    (u"薄壁实体改 Hex-dominant",
     u"对形状不规则、无法 Sweep 的薄板：Method = Hex-dominant，"
     u"并把该体局部尺寸降到 ≈ 壁厚",
     u"六面体主占比能显著缓解薄壁压扁，但单元数会比 Sweep 高"),
    (u"接触面保护",
     u"做任何 defeature 前，先确认被删的小面不是接触面/密封面；否则接触会静默丢失",
     u"这类错误网格看不出来，只能靠接触检查"),
]

MODEL_PLAYBOOK = [
    (u"抑制不影响主传力的装饰件",
     u"铭牌、支架盖板、非承力小件可抑制；每抑制一层就少一批难划网格",
     u"降低单元数最有效，但要确认不影响传力路径"),
    (u"规则挤出件换 Sweep/Hex-dominant",
     u"支梁/支柱/底条等长条件：Mesh Control -> Method = Sweep，自动识别源面",
     u"同时提升质量与降低单元数"),
    (u"薄壁件局部尺寸向壁厚看齐",
     u"把该体 Body Sizing 降到 ≈ 壁厚（1~2 倍），必要时只在承力区细化",
     u"四面体路线的兜底手段；单元数会上升，先在小范围验证"),
]


def build(cfg, snapshot, findings, history, log, out_dir=None):
    """生成人工决策清单。"""
    out_dir = out_dir or cfg["run"]["report_dir"]
    stamp = compat.stamp()
    items = []

    # 1) 自动层改不动的诊断
    for finding in diagnose_mod.manual_scope_findings(findings):
        items.append({
            "title": finding["title"],
            "scope": finding.get("scope", "geometry"),
            "risk": RISK_TEXT.get(finding.get("scope"), u"未知"),
            "evidence": finding.get("evidence") or [],
            "cause": finding.get("cause", u""),
            "advice": finding.get("advice") or [],
        })

    # 2) 仍然 FAIL/WARN 的指标
    rows = snapshot.get("rows") or {}
    residual = []
    for name, row in rows.items():
        if row.get("status") not in ("WARN", "FAIL") or row.get("info_only"):
            continue
        residual.append({
            "metric": name, "status": row["status"],
            "worst": row.get("worst"), "warn": row.get("warn"), "error": row.get("error"),
        })
    residual.sort(key=lambda r: -abs(compat.to_float(r.get("worst"), 0) or 0))

    # 3) 结构/几何层的通用打法（只列与当前证据相关的）
    playbook = []
    codes = set(f["code"] for f in findings)
    if any(c.startswith("METRIC_") for c in codes) or "DEGENERATE_GEOMETRY" in codes:
        playbook.extend([{"title": t, "how": h, "why": w} for t, h, w in GEOMETRY_PLAYBOOK])
    if "BUDGET_EXCEEDED" in codes or "BUDGET_OVER_TARGET" in codes or "BODY_UNMESHED" in codes:
        playbook.extend([{"title": t, "how": h, "why": w} for t, h, w in MODEL_PLAYBOOK])

    # 4) 生成可执行脚本
    script_path = None
    try:
        script_path = _write_script(cfg, snapshot, findings, residual, out_dir, stamp)
    except Exception:
        log.warn(u"生成人工修复脚本失败: %s"
                 % compat.to_text(traceback.format_exc()).strip().splitlines()[-1])

    md = _render(cfg, snapshot, items, residual, playbook, history, script_path)
    payload = {
        "items": items,
        "residual": residual,
        "playbook": playbook,
        "script": script_path,
        "markdown": md,
        "path": os.path.join(out_dir, "人工决策清单_%s.md" % stamp),
    }
    compat.write_text(payload["path"], md)
    log.info(u"人工决策清单已写入: %s" % payload["path"])
    return payload


# --------------------------------------------------------------------------
def _render(cfg, snapshot, items, residual, playbook, history, script_path):
    lines = []
    add = lines.append
    add(u"# 人工决策清单")
    add(u"")
    add(u"> 自动纠正只动网格设置；下面这些改不动或不该自动改的部分，需要你拍板。")
    add(u"")
    add(u"## 1. 现状")
    add(u"")
    add(u"| 项目 | 值 |")
    add(u"|---|---|")
    add(u"| 模型 | %s |" % (cfg.get("meta", {}).get("model_label") or u"-"))
    add(u"| 单元 / 节点 | %s / %s |" % (compat.fmt_num(snapshot.get("elements")),
                                    compat.fmt_num(snapshot.get("nodes"))))
    add(u"| 网格状态 | %s |" % compat.to_text(snapshot.get("state")))
    score = snapshot.get("score") or {}
    add(u"| 质量得分 | %s / 100（惩罚值 %s） |" % (compat.fmt_num(score.get("display")),
                                             compat.fmt_num(score.get("penalty"))))
    add(u"| 迭代轮次 | %d |" % len(history or []))
    add(u"")

    if items:
        add(u"## 2. 自动纠正覆盖不到的问题")
        add(u"")
        for index, item in enumerate(items, 1):
            add(u"### 2.%d %s" % (index, item["title"]))
            add(u"")
            add(u"- **范围**：%s（%s）" % (item["scope"], item["risk"]))
            if item.get("cause"):
                add(u"- **根因**：%s" % item["cause"])
            if item.get("evidence"):
                add(u"- **证据**：")
                for line in item["evidence"]:
                    add(u"  - %s" % line)
            if item.get("advice"):
                add(u"- **建议动作**：")
                for line in item["advice"]:
                    add(u"  - %s" % line)
            add(u"")
    else:
        add(u"## 2. 自动纠正覆盖不到的问题")
        add(u"")
        add(u"无 —— 剩余问题都在网格设置层，已在自动阶梯里尝试过。")
        add(u"")

    if residual:
        add(u"## 3. 仍未达标的指标")
        add(u"")
        add(u"| 指标 | 状态 | 最差值 | warning | error |")
        add(u"|---|---|---|---|---|")
        for row in residual:
            add(u"| %s | %s | %s | %s | %s |"
                % (row["metric"], row["status"], compat.fmt_num(row.get("worst")),
                   compat.fmt_num(row.get("warn")), compat.fmt_num(row.get("error"))))
        add(u"")

    if playbook:
        add(u"## 4. 结构性建议（按性价比排序）")
        add(u"")
        for index, item in enumerate(playbook, 1):
            add(u"%d. **%s** —— %s" % (index, item["title"], item["why"]))
            add(u"   - 做法：%s" % item["how"])
        add(u"")

    if script_path:
        add(u"## 5. 可直接运行的补救脚本")
        add(u"")
        add(u"已生成：`%s`" % script_path)
        add(u"")
        add(u"- 里面每个操作块都带 `ENABLE = False`，确认哪一个就用哪一个；")
        add(u"- 默认全部关闭，**脚本不会自己动模型**；")
        add(u"- 每块都写明了会改什么、风险是什么。")
        add(u"")
    return u"\n".join(lines)


# --------------------------------------------------------------------------
def _write_script(cfg, snapshot, findings, residual, out_dir, stamp):
    """生成人工修复脚本（默认全部关闭开关）。"""
    path = os.path.join(out_dir, "apply_manual_fix_%s.py" % stamp)
    bodies = snapshot.get("bodies") or []
    worst_bodies = sorted([b for b in bodies if (b.get("elements") or 0) > 0],
                          key=lambda b: -(b.get("elements") or 0))[:10]
    lines = []
    add = lines.append
    add(u"# -*- coding: utf-8 -*-")
    add(u"# ==========================================================================")
    add(u"#  人工确认后执行的补救脚本（由 meshkit 自动生成，生成时间 %s）" % stamp)
    add(u"#")
    add(u"#  用法：在 Mechanical -> Automation -> Scripting 里")
    add(u"#        execfile(r\"%s\")" % path)
    add(u"#")
    add(u"#  *** 默认所有 ENABLE 都是 False：脚本什么都不会做 ***")
    add(u"#  逐条看完说明，把想执行的那一条改成 True 再跑。")
    add(u"# ==========================================================================")
    add(u"")
    add(u"import traceback")
    add(u"")
    add(u"")
    add(u"def _log(msg):")
    add(u"    try:")
    add(u"        print(msg)")
    add(u"    except Exception:")
    add(u"        pass")
    add(u"")
    add(u"")
    add(u"def _mesh():")
    add(u"    return ExtAPI.DataModel.Project.Model.Mesh")
    add(u"")
    add(u"")
    add(u"def _qty(mm):")
    add(u"    try:")
    add(u"        return Quantity(float(mm), 'mm')")
    add(u"    except Exception:")
    add(u"        return float(mm)")
    add(u"")
    add(u"")
    add(u"# --------------------------------------------------------------------------")
    add(u"# 1) 单元阶次降为一阶：能立刻改善 Jacobian/TetCollapse 极值，")
    add(u"#    代价是弯曲精度下降（薄板/弯曲主导的模型不要用）。")
    add(u"ENABLE_ELEMENT_ORDER_FIRST = False")
    add(u"")
    add(u"# 2) 把所有带作用域的局部控制尺寸放大 10%（用于压单元数/缓解过渡畸变）")
    add(u"ENABLE_SIZE_UP_10 = False")
    add(u"# 以下仅作参考：这不是筛选条件，只是当前体量最大的几个体（放大尺寸优先考虑它们）")
    add(u"BIGGEST_BODIES = [%s]" % u", ".join(u"'%s'" % b["name"].replace(u"\\", u"\\\\")
                                            for b in worst_bodies))
    add(u"")
    add(u"# 3) 导出 Mechanical 自带的网格质量报告（只读，安全）")
    add(u"ENABLE_EXPORT_REPORT = False")
    add(u"REPORT_PATH = r'%s'" % os.path.join(out_dir, "mechanical_mesh_report_%s.txt" % stamp))
    add(u"")
    add(u"")
    add(u"def main():")
    add(u"    mesh = _mesh()")
    add(u"    if ENABLE_ELEMENT_ORDER_FIRST:")
    add(u"        try:")
    add(u"            mesh.ElementOrder = 1")
    add(u"            _log('[ok] ElementOrder -> First')")
    add(u"        except Exception:")
    add(u"            _log('[FAIL] ElementOrder: ' + unicode(traceback.format_exc().splitlines()[-1]))")
    add(u"")
    add(u"    if ENABLE_SIZE_UP_10:")
    add(u"        changed = 0")
    add(u"        for control in mesh.Children:")
    add(u"            try:")
    add(u"                name = unicode(control.Name)")
    add(u"            except Exception:")
    add(u"                continue")
    add(u"            try:")
    add(u"                location = control.Location")
    add(u"                ids = list(location.Ids) if location is not None else []")
    add(u"            except Exception:")
    add(u"                ids = []")
    add(u"            if not ids:")
    add(u"                continue")
    add(u"            try:")
    add(u"                size = control.ElementSize")
    add(u"                value = float(size.Value)")
    add(u"                unit = unicode(size.Unit)")
    add(u"                if unit.lower() != 'mm':")
    add(u"                    continue")
    add(u"                control.ElementSize = _qty(value * 1.10)")
    add(u"                changed += 1")
    add(u"            except Exception:")
    add(u"                continue")
    add(u"        _log('[ok] %d controls enlarged by 10%%' % changed)")
    add(u"")
    add(u"    if ENABLE_EXPORT_REPORT:")
    add(u"        try:")
    add(u"            helper = mesh.SaveMeshReportHelper")
    add(u"            for method in ('Save', 'Export', 'Write', 'SaveTo'):")
    add(u"                fn = getattr(helper, method, None)")
    add(u"                if fn is None:")
    add(u"                    continue")
    add(u"                try:")
    add(u"                    fn(REPORT_PATH)")
    add(u"                    _log('[ok] mesh report -> ' + REPORT_PATH)")
    add(u"                    break")
    add(u"                except Exception:")
    add(u"                    continue")
    add(u"        except Exception:")
    add(u"            _log('[FAIL] mesh report: ' + unicode(traceback.format_exc().splitlines()[-1]))")
    add(u"")
    add(u"")
    add(u"try:")
    add(u"    main()")
    add(u"except Exception:")
    add(u"    _log(traceback.format_exc())")
    add(u"")
    compat.write_text(path, u"\n".join(lines))
    return path
