# -*- coding: utf-8 -*-
"""报告层：把整次运行渲染成 Markdown / TXT / CSV。

一份报告回答四个问题：
1. 我要什么（生效的网格需求）
2. 实际得到什么（网格与质量数据）
3. 出了什么问题、我改了什么、改完怎么样（迭代史 + 诊断 + 动作）
4. 还剩什么问题、下一步怎么走（结论 + 建议 + 产出清单）
"""
from __future__ import division

import fnmatch
import os

from . import compat
from . import config as config_mod
from . import diagnose as diagnose_mod
from . import metrics as metrics_mod


class Report(object):

    def __init__(self, cfg, session, log):
        self.cfg = cfg
        self.session = session
        self.log = log
        self.started = compat.now()
        self.stamp = compat.stamp()
        self.out_dir = cfg["run"]["report_dir"]
        compat.mkdirs(self.out_dir)
        self.rounds = []

    # ------------------------------------------------------------------ 记录
    def add_round(self, snapshot, findings, plan, outcome, generation=None,
                  requirements=None):
        self.rounds.append({
            "index": snapshot.get("round"),
            "snapshot": snapshot,
            "findings": findings,
            "plan": plan,
            "outcome": outcome,
            "generation": generation,
            "requirements": requirements,
        })

    # ------------------------------------------------------------------ 渲染
    def render(self, verdict):
        lines = []
        add = lines.append
        cfg = self.cfg
        info = self.session.describe()
        add(u"# 网格自动化报告 — %s" % (cfg.get("meta", {}).get("model_label") or u"未命名模型"))
        add(u"")
        add(u"> 生成时间：%s　|　meshkit 运行方式：Mechanical 内置 IronPython"
            % self.started.strftime("%Y-%m-%d %H:%M:%S"))
        add(u"")
        add(u"## 0. 结论速览")
        add(u"")
        for line in _verdict_lines(verdict, self.rounds):
            add(line)
        add(u"")

        add(u"## 1. 环境与生效需求")
        add(u"")
        add(u"| 项目 | 值 |")
        add(u"|---|---|")
        add(u"| Mechanical 版本 | %s |" % info.get("version", u"未知"))
        add(u"| 工程 | %s |" % (info.get("project") or u"-"))
        add(u"| 体数 | %s |" % info.get("bodies"))
        add(u"| 运行模式 | %s |" % (u"仅分析（dry-run，不改任何设置）"
                                if cfg["run"]["dry_run"] else u"自动设网格 + 自动纠正"))
        add(u"| 自动纠正 | %s，最多 %d 轮 |" % (
            u"开" if cfg["run"]["auto_repair"] else u"关", cfg["run"]["max_rounds"]))
        add(u"")
        add(u"**全局网格设置（要求）**")
        add(u"")
        add(u"| 项目 | 要求值 |")
        add(u"|---|---|")
        for label, value in config_mod.summarize(cfg):
            add(u"| %s | %s |" % (label, value))
        add(u"")
        controls = cfg.get("controls") or []
        if controls:
            add(u"**局部控制项（要求）**")
            add(u"")
            add(u"| 名称 | 类型 | 尺寸/方法 | 作用体通配 | 定位 |")
            add(u"|---|---|---|---|---|")
            for spec in controls:
                target = (u"%s mm" % compat.fmt_num(spec["size_mm"])
                          if spec.get("size_mm") is not None
                          else (spec.get("method") or u"-"))
                add(u"| %s | %s | %s | %s | %s |"
                    % (spec["name"], spec["type"], target,
                       u", ".join(spec.get("bodies") or []) or u"-",
                       spec.get("role") or u"-"))
            add(u"")

        add(u"## 2. 迭代过程")
        add(u"")
        if not self.rounds:
            add(u"（没有执行任何轮次）")
            add(u"")
        else:
            add(u"| 轮次 | 单元 / 节点 | 质量得分 | 状态 | 测量后采取的动作 | 说明 |")
            add(u"|---|---|---|---|---|---|")
            for record in self.rounds:
                snapshot = record["snapshot"]
                score = snapshot.get("score") or {}
                plan = record["plan"] or {}
                actions = plan.get("actions") or []
                titles = u", ".join(_action_title(a) for a in actions) or u"-"
                note = u""
                outcome = record["outcome"] or {}
                if not outcome.get("applied"):
                    note = u"无动作" if not actions else u"动作未生效"
                elif outcome.get("notes"):
                    note = u"; ".join(outcome.get("notes"))[:120]
                add(u"| %s | %s / %s | %s | %s | %s | %s |"
                    % (compat.to_text(snapshot.get("round")),
                       compat.fmt_num(snapshot.get("elements")),
                       compat.fmt_num(snapshot.get("nodes")),
                       compat.fmt_num(score.get("display")),
                       score.get("status") or u"-", titles, note))
            add(u"")

        final = self.rounds[-1]["snapshot"] if self.rounds else None
        add(u"## 3. 最终网格")
        add(u"")
        if final is None:
            add(u"（无数据）")
            add(u"")
        else:
            add(u"| 项目 | 值 |")
            add(u"|---|---|")
            add(u"| 单元数 | %s |" % compat.fmt_num(final.get("elements")))
            add(u"| 节点数 | %s |" % compat.fmt_num(final.get("nodes")))
            add(u"| 网格状态 | %s |" % compat.to_text(final.get("state")))
            geometry = final.get("geometry") or {}
            add(u"| 几何最小边长 | %s mm（%s） |"
                % (compat.fmt_num(geometry.get("min_edge_mm")), geometry.get("min_edge_source") or u"-"))
            if geometry.get("bbox_diag_mm"):
                add(u"| 包围盒对角 | %s mm |" % compat.fmt_num(geometry.get("bbox_diag_mm")))
            stats = final.get("control_stats") or {}
            add(u"| 控制项 | %d 个（有尺寸 %d，0 尺寸 %d，空作用域 %d） |"
                % (stats.get("count", 0), stats.get("with_size", 0),
                   len(stats.get("zero_size") or []), len(stats.get("no_scope") or [])))
            add(u"")
            bodies = sorted([b for b in (final.get("bodies") or [])],
                            key=lambda b: -(b.get("elements") or 0))
            if bodies and any(b.get("elements") is not None for b in bodies):
                add(u"**单元数最多的体（前 10）**")
                add(u"")
                add(u"| 体 | 单元数 | 厚度 mm |")
                add(u"|---|---|---|")
                for body in bodies[:10]:
                    add(u"| %s | %s | %s |" % (body["name"], compat.fmt_num(body.get("elements")),
                                               compat.fmt_num(body.get("thickness_mm"))))
                add(u"")
            elif bodies:
                add(u"> 本版本不提供逐体单元数（`ElementIdsPerRefId` 是按几何面统计的），"
                    u"定位改用下面的『单元抽样』。")
                add(u"")
            unmeshed = final.get("unmeshed_bodies") or []
            add(u"| 未划上网的体 | %s |" % (u"%d 个：%s" % (len(unmeshed), u", ".join(unmeshed[:10]))
                                        if unmeshed else u"无"))
            add(u"")
            shell_rows = [b for b in (final.get("bodies") or [])
                          if (b.get("thickness_mm") or 0) > 0]
            if shell_rows:
                add(u"**壳体清单（厚度 > 0，共 %d 个）**" % len(shell_rows))
                add(u"")
                add(u"| 壳 | 厚度 mm |")
                add(u"|---|---|")
                for body in shell_rows[:20]:
                    add(u"| %s | %s |" % (body["name"], compat.fmt_num(body.get("thickness_mm"))))
                if len(shell_rows) > 20:
                    add(u"| …… 其余 %d 个 | |" % (len(shell_rows) - 20))
                add(u"")
                policy = self.cfg.get("policy") or {}
                patterns = policy.get("shell_patterns") or []
                if patterns and not policy.get("shell_conversion_allowed", False):
                    outside = [b["name"] for b in shell_rows
                               if not any(fnmatch.fnmatch(b["name"], p)
                                          or fnmatch.fnmatch(b["name"].split(u"\\")[-1], p)
                                          for p in patterns)]
                    if outside:
                        add(u"> ⚠ 按你的规则（只有波纹板用壳），下面这些壳不在允许范围：%s"
                            % u", ".join(outside))
                        add(u"> 请确认是保留还是抑制；确认保留后把 `policy.shell_patterns` 补上对应通配符。")
                        add(u"")

        if final is not None:
            add(u"## 4. 质量指标")
            add(u"")
            rows = final.get("rows") or {}
            if rows:
                add(u"| 指标 | 最小 | 最大 | 平均 | 标准差 | warning | error | 判定 |")
                add(u"|---|---|---|---|---|---|---|---|")
                for name in _metric_order(rows):
                    row = rows[name]
                    if (row.get("status") == metrics_mod.STATUS_SKIP
                            and row.get("min") is None and row.get("max") is None):
                        continue
                    add(u"| %s | %s | %s | %s | %s | %s | %s | %s |"
                        % (name, compat.fmt_num(row.get("min")), compat.fmt_num(row.get("max")),
                           compat.fmt_num(row.get("avg")), compat.fmt_num(row.get("std")),
                           compat.fmt_num(row.get("warn")), compat.fmt_num(row.get("error")),
                           _status_text(row.get("status"))))
                add(u"")
                tally = final.get("tally") or {}
                add(u"统计：PASS %d / WARN %d / FAIL %d / 仅记录 %d"
                    % (tally.get("PASS", 0), tally.get("WARN", 0),
                       tally.get("FAIL", 0), tally.get("SKIP", 0)))
                metric_seconds = (final.get("seconds") or {}).get("metrics")
                if metric_seconds:
                    add(u"")
                    add(u"指标采集用时 %s（每个指标都要重算一次云图）"
                        % compat.elapsed_text(metric_seconds))
                limits = final.get("limits") or {}
                items = limits.get("items") or []
                if items and all((i.get("warn") in (0, 0.0) and i.get("error") in (0, 0.0))
                                 for i in items):
                    add(u"")
                    add(u"> Mechanical 未配置质量限值（读回全为 0），本报告采用配置里的阈值。")
                add(u"")
            else:
                add(u"（没有取到质量指标）")
                add(u"")
            missing = final.get("metrics_missing") or []
            if missing:
                add(u"> 未采集或本版本取不到的指标：%s" % u", ".join(missing))
                add(u"")

            sampling = final.get("sampling") or {}
            if sampling:
                add(u"**单元抽样定位（把坏单元归到体上）**")
                add(u"")
                if sampling.get("ok"):
                    add(u"抽样 %s 个单元（步长 %s，取数方式 %s）；归到 %s 个体、"
                        u"%s 个面未能归体。"
                        % (compat.fmt_num(sampling.get("sampled")),
                           compat.fmt_num(sampling.get("stride")),
                           compat.to_text(sampling.get("method")),
                           compat.fmt_num(sampling.get("matched_bodies")),
                           compat.fmt_num(sampling.get("unmatched_faces"))))
                    add(u"")
                    dist = sampling.get("distribution") or []
                    if dist:
                        add(u"**边比分布（抽样 → 外推全模型 %s 个单元）**"
                            % compat.fmt_num(sampling.get("total_elements")))
                        add(u"")
                        add(u"| 判据 | 抽样命中 | 占比 | 外推到全模型 |")
                        add(u"|---|---|---|---|")
                        for item in dist:
                            add(u"| %s | %s | %.1f%% | 约 %s 个 |"
                                % (item["label"], compat.fmt_num(item["samples"]),
                                   100.0 * item["share"], compat.fmt_num(item["estimate"])))
                        add(u"")
                    add(u"**最差的体（按最大边比）**")
                    add(u"")
                    add(u"| 体 | 最大边比 | 平均边比 | 抽样数 | 代表单元 id |")
                    add(u"|---|---|---|---|---|")
                    for row in (sampling.get("bodies") or [])[:12]:
                        add(u"| %s | %s | %s | %s | %s |"
                            % (row["name"], compat.fmt_num(row["edge_ratio"]),
                               compat.fmt_num(row.get("avg_ratio")),
                               compat.fmt_num(row.get("sample_count")),
                               compat.fmt_num(row["element_id"])))
                else:
                    add(u"抽样未成功：%s" % (sampling.get("error") or u"未知原因"))
                add(u"")
                thin = sampling.get("thin_candidates") or []
                if thin:
                    add(u"**薄壁候选（Sweep / Hex-dominant 优先级）**：平均边比 ≥ 8 且抽样数 ≥ 5 的体，"
                        u"几乎都是『薄板用四面体』的典型症状。按你的规则**不改壳**，"
                        u"优先用 Sweep（厚度方向 2 层）或 Hex-dominant 处理。")
                    add(u"")
                    add(u"| 体 | 平均边比 | 最大边比 | 抽样数 |")
                    add(u"|---|---|---|---|")
                    for row in thin[:20]:
                        add(u"| %s | %s | %s | %s |"
                            % (row["name"], compat.fmt_num(row.get("avg_ratio")),
                               compat.fmt_num(row.get("edge_ratio")),
                               compat.fmt_num(row.get("sample_count"))))
                    add(u"")
                    add(u"> 操作见 `薄壁件不改壳_优化清单.md`。")
                    add(u"")
                add(u"> 边比 = 单元最长边/最短边（**不是** Mechanical 的 AspectRatio），"
                    u"只用于相对排序、定位问题体。")
                add(u"")

        add(u"## 5. 诊断与自动处理")
        add(u"")
        if final is None:
            add(u"（无数据）")
            add(u"")
        else:
            findings = self.rounds[-1]["findings"] if self.rounds else []
            if not findings:
                add(u"本轮未发现需要处理的问题。")
                add(u"")
            for finding in findings:
                add(u"### [%s] %s" % (_severity_text(finding.get("severity")), finding["title"]))
                add(u"")
                add(u"- 代码：`%s`" % finding["code"])
                if finding.get("cause"):
                    add(u"- 根因：%s" % finding["cause"])
                if finding.get("evidence"):
                    add(u"- 证据：")
                    for line in finding["evidence"]:
                        add(u"  - %s" % line)
                actions = finding.get("actions") or []
                if actions:
                    add(u"- 可自动执行：%s" % u", ".join(_action_title(a) for a in actions))
                applied = _applied_actions(self.rounds, actions)
                if applied:
                    add(u"- 实际执行：%s" % u", ".join(applied))
                if finding.get("advice"):
                    add(u"- 人工建议：")
                    for line in finding["advice"]:
                        add(u"  - %s" % line)
                add(u"")

        add(u"## 6. 自动纠正明细")
        add(u"")
        any_action = False
        for record in self.rounds:
            outcome = record["outcome"] or {}
            results = outcome.get("results") or []
            if not results:
                continue
            any_action = True
            add(u"**第 %s 轮**（阶梯步骤：%s）"
                % (record["snapshot"].get("round"), (record["plan"] or {}).get("step") or u"-"))
            add(u"")
            for result in results:
                add(u"- %s" % result.get("title") or result.get("action"))
                for note in result.get("notes") or []:
                    add(u"  - %s" % note)
            if (record["generation"] or {}).get("attempts"):
                for attempt in record["generation"]["attempts"]:
                    add(u"  - 划网格（%s）：%s，%s"
                        % (attempt.get("label"), u"成功" if attempt.get("ok") else u"失败",
                           compat.elapsed_text(attempt.get("seconds"))))
            add(u"")
        if not any_action:
            add(u"本次没有执行任何自动纠正动作。")
            add(u"")

        add(u"## 7. 下一步")
        add(u"")
        for line in _next_steps(verdict, self.rounds, self.cfg):
            add(line)
        add(u"")
        add(u"## 8. 产出文件")
        add(u"")
        add(u"| 文件 | 说明 |")
        add(u"|---|---|")
        add(u"| %s | 本报告 |" % os.path.basename(verdict.get("markdown_path") or u"-"))
        add(u"| %s | 指标明细 CSV（Excel 可开；企业加密软件可能加密此扩展名） |"
            % os.path.basename(verdict.get("csv_path") or u"-"))
        if verdict.get("csv_txt_path"):
            add(u"| %s | 同一份指标明细的明文副本 |"
                % os.path.basename(verdict["csv_txt_path"]))
        add(u"| %s | 运行日志 |" % os.path.basename(verdict.get("log_path") or u"-"))
        if verdict.get("advice_path"):
            add(u"| %s | 人工决策清单 |" % os.path.basename(verdict["advice_path"]))
        if verdict.get("manual_script"):
            add(u"| %s | 人工确认后可执行的补救脚本 |" % os.path.basename(verdict["manual_script"]))
        add(u"")
        add(u"---")
        add(u"")
        add(u"### 附：环境能力探测")
        add(u"")
        caps = info.get("caps") or {}
        for key in sorted(caps.keys()):
            add(u"- `%s` = %s" % (key, compat.to_text(caps[key])))
        add(u"")
        return u"\n".join(lines)

    # ------------------------------------------------------------------ 输出
    def write(self, verdict):
        markdown = self.render(verdict)
        markdown_path = os.path.join(self.out_dir, "网格自动化报告_%s.md" % self.stamp)
        csv_path = os.path.join(self.out_dir, "网格质量指标_%s.csv" % self.stamp)
        compat.write_text(markdown_path, markdown)
        csv_path, csv_txt = self.write_csv(csv_path)
        verdict["markdown_path"] = markdown_path
        verdict["csv_path"] = csv_path
        verdict["csv_txt_path"] = csv_txt
        # 报告里的文件名需要 markdown_path，重写一次保证内容一致
        compat.write_text(markdown_path, self.render(verdict))
        return markdown_path

    def write_csv(self, path):
        final = self.rounds[-1]["snapshot"] if self.rounds else None
        lines = [u"metric,min,max,average,std,warning_limit,error_limit,status"]
        if final:
            rows = final.get("rows") or {}
            for name in _metric_order(rows):
                row = rows[name]
                lines.append(u",".join([name,
                                        _csv(row.get("min")), _csv(row.get("max")),
                                        _csv(row.get("avg")), _csv(row.get("std")),
                                        _csv(row.get("warn")), _csv(row.get("error")),
                                        compat.to_text(row.get("status"))]))
            lines.append(u"")
            lines.append(u"# round,element_count,node_count,score,status")
            for record in self.rounds:
                snapshot = record["snapshot"]
                score = snapshot.get("score") or {}
                lines.append(u"%s,%s,%s,%s,%s" % (
                    snapshot.get("round"), snapshot.get("elements"), snapshot.get("nodes"),
                    _csv(score.get("display")), score.get("status")))
        text = u"\n".join(lines)
        compat.write_text(path, text)
        # 注意：企业透明加密软件（如亿赛通）会加密 .csv 而放过 .txt，
        # 所以同一份内容再写一个 .txt，保证外部工具/别的机器也能读。
        alt = (path[:-4] if path.lower().endswith(".csv") else path) + ".txt"
        compat.write_text(alt, text)
        return path, alt

    def write_plain(self, verdict, path=None):
        """给 Mechanical 控制台/纯文本查看的精简版。"""
        path = path or os.path.join(self.out_dir, "网格摘要_%s.txt" % self.stamp)
        final = self.rounds[-1]["snapshot"] if self.rounds else None
        lines = []
        lines.append(u"=" * 78)
        lines.append(u"MESHKIT 网格自动化 — %s" % (self.cfg.get("meta", {}).get("model_label") or u""))
        lines.append(u"=" * 78)
        for line in _verdict_lines(verdict, self.rounds):
            lines.append(line)
        if final:
            lines.append(u"")
            lines.append(u"最终网格: %s 单元 / %s 节点，状态 %s"
                         % (compat.fmt_num(final.get("elements")),
                            compat.fmt_num(final.get("nodes")), final.get("state")))
            rows = final.get("rows") or {}
            for name in _metric_order(rows):
                row = rows[name]
                if row.get("status") == metrics_mod.STATUS_SKIP:
                    continue
                lines.append(u"  %-26s min=%-10s max=%-10s avg=%-10s [%s]"
                             % (name, compat.fmt_num(row.get("min")), compat.fmt_num(row.get("max")),
                                compat.fmt_num(row.get("avg")), row.get("status")))
        lines.append(u"")
        lines.append(u"报告: %s" % verdict.get("markdown_path"))
        compat.write_text(path, u"\n".join(lines))
        return path


# --------------------------------------------------------------------------
def _verdict_lines(verdict, rounds):
    lines = []
    add = lines.append
    final = rounds[-1]["snapshot"] if rounds else {}
    score = (final or {}).get("score") or {}
    add(u"| 项目 | 结论 |")
    add(u"|---|---|")
    add(u"| 网格 | %s |" % (u"已生成" if (final or {}).get("elements") else u"未生成"))
    add(u"| 元素 | %s 单元 / %s 节点 |" % (compat.fmt_num((final or {}).get("elements")),
                                     compat.fmt_num((final or {}).get("nodes"))))
    add(u"| 质量 | %s（得分 %s/100） |" % (_status_text(score.get("status")),
                                       compat.fmt_num(score.get("display"))))
    add(u"| 自动纠正 | %s |" % (verdict.get("repair_summary") or u"未触发"))
    add(u"| 遗留问题 | %s |" % (verdict.get("residual_summary") or u"无"))
    add(u"")
    if verdict.get("headline"):
        add(u"**%s**" % verdict["headline"])
        add(u"")
    return lines


def _next_steps(verdict, rounds, cfg):
    lines = []
    add = lines.append
    final = rounds[-1]["snapshot"] if rounds else {}
    findings = rounds[-1]["findings"] if rounds else []
    if verdict.get("advice_path"):
        add(u"1. 看 `%s`：里面是自动纠正覆盖不到、需要你决策的事项。" % os.path.basename(verdict["advice_path"]))
        add(u"2. 若同意其中某条，打开同目录的补救脚本，把对应 `ENABLE_*` 改成 `True` 再执行。")
    else:
        add(u"1. 网格与质量已达标，可以直接进入边界条件/求解。")
    residual = [f for f in findings if f.get("severity") == diagnose_mod.SEV_ERROR]
    if residual:
        add(u"3. 仍有 %d 个 error 级问题未消除，建议先处理几何再重跑本脚本。" % len(residual))
    else:
        add(u"3. 若仍需提升质量：优先处理『几何碎边』，这是性价比最高的一步。")
    add(u"4. 复跑命令：`execfile(r\"%s\")`"
        % os.path.join(config_mod.ROOT, "run_mesh.py").replace(u"\\", u"\\\\"))
    add(u"")
    add(u"> 注意：本脚本只在被要求时才保存工程（配置 `run.save_project`），"
        u"默认不改磁盘上的任何结果。")
    return lines


def _applied_actions(rounds, wanted):
    applied = []
    for record in rounds:
        outcome = record["outcome"] or {}
        for action in outcome.get("applied") or []:
            if action in wanted and action not in applied:
                applied.append(_action_title(action))
    return applied


def _action_title(action):
    from . import repair as repair_mod
    return repair_mod.ACTION_TITLES.get(action, action)


def _metric_order(rows):
    preferred = ["ElementQuality", "Skewness", "JacobianRatio", "JacobianRatioCornerNodes",
                 "JacobianRatioGaussPoints", "AspectRatio", "TetCollapse",
                 "MaximumCornerAngle", "MinTriAngle", "MaxTriAngle", "WarpingFactor",
                 "ParallelDeviation"]
    out = [name for name in preferred if name in rows]
    for name in rows:
        if name not in out:
            out.append(name)
    return out


def _status_text(status):
    return {metrics_mod.STATUS_PASS: u"通过",
            metrics_mod.STATUS_WARN: u"警告",
            metrics_mod.STATUS_FAIL: u"不达标",
            metrics_mod.STATUS_SKIP: u"仅记录"}.get(status, compat.to_text(status))


def _severity_text(severity):
    return {diagnose_mod.SEV_ERROR: u"必须处理",
            diagnose_mod.SEV_WARN: u"建议处理",
            diagnose_mod.SEV_INFO: u"提示"}.get(severity, compat.to_text(severity))


def _csv(value):
    if value is None:
        return u""
    return compat.to_text(value).replace(u",", u";")
