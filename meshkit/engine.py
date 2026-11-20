# -*- coding: utf-8 -*-
"""编排层：一次完整闭环。

流程
----
::

    读需求 -> 应用设置 -> 划网格 -> 采集数据 -> 诊断
                                   ^              |
                                   |              v
                            再划网格 <-- 自动纠正（只动网格设置）
                                   |
                                   v
                    达标 / 阶梯用尽 -> 报告 + 人工决策清单

特点
----
* **可回滚**：每轮开始前抓一次设置快照，跑完按综合惩罚值挑最优；配置 keep_best 时
  会回到最优那一轮的设置并重新生成网格。
* **可归因**：一轮只推进一步阶梯，报告里能看到"这一步改了什么、指标怎么变"。
* **可中断**：任何未捕获异常都会写成报告 + 日志，并尽量把工程留在可用状态。
"""
from __future__ import division

import os
import time
import traceback

from . import advisor
from . import compat
from . import config as config_mod
from . import controls as controls_mod
from . import diagnose as diagnose_mod
from . import mesher
from . import metrics as metrics_mod
from . import repair as repair_mod
from . import report as report_mod
from . import session as session_mod


def run(config_path=None, overrides=None, log_path=None, log=None, extapi=None):
    """执行一次完整流程。返回 verdict dict（同时把结果打印到 Mechanical 控制台）。"""
    started = time.time()
    cfg = None
    session = None
    report = None
    verdict = {"status": "FAILED", "headline": u"运行失败", "rounds": 0}
    logger = log
    try:
        cfg = config_mod.load(config_path, overrides=overrides)
        out_dir = cfg["run"]["report_dir"]
        compat.mkdirs(out_dir)
        if logger is None:
            logger = compat.Logger(log_path or os.path.join(out_dir, "run_%s.log" % compat.stamp()),
                                   verbose=bool(cfg["run"].get("console_verbose", True)))
        logger.info(u"=" * 70)
        logger.info(u"meshkit 启动：%s" % (cfg.get("meta", {}).get("model_label") or u"未命名模型"))
        logger.info(u"配置文件: %s" % (cfg.get("_sources", {}).get("user_file") or u"内置默认"))
        logger.info(u"预设: %s" % (cfg.get("_sources", {}).get("preset") or u"-"))
        logger.info(u"输出目录: %s" % out_dir)

        problems = config_mod.validate(cfg)
        for problem in problems:
            logger.warn(u"配置检查: %s" % problem)

        session = session_mod.Session(log=logger, extapi=extapi)
        report = report_mod.Report(cfg, session, logger)

        if cfg["run"]["dry_run"]:
            logger.info(u"dry-run 模式：只读分析，不修改任何网格设置")

        # ---------------------------------------------------------- 初始状态
        initial_state = controls_mod.capture_state(session, cfg)
        if not cfg["run"]["dry_run"]:
            snapshot_path = _write_state_snapshot(cfg, initial_state, logger)
            verdict["settings_snapshot"] = snapshot_path
        requirements = None
        if not cfg["run"]["dry_run"]:
            requirements = mesher.apply_requirements(session, cfg, logger)
            if requirements["global_failures"]:
                logger.warn(u"有 %d 项全局设置未生效，详见报告" % len(requirements["global_failures"]))
        else:
            logger.info(u"当前网格: %s" % mesher.summarize_counts(
                {"elements": session.element_count(), "nodes": session.node_count(),
                 "state": session.mesh_state()}))

        # -------------------------------------------------------------- 主循环
        best = None           # {"penalty","round","state"}
        attempt_counts = {}   # {阶梯步骤: 次数}
        attempted_actions = []
        ctx = {"used_defeature_factors": []}
        last_round = 0

        for round_index in range(1, cfg["run"]["max_rounds"] + 1):
            last_round = round_index
            round_state = controls_mod.capture_state(session, cfg)

            generation = None
            if cfg["run"]["dry_run"]:
                generation = {"ok": True, "attempts": [], "error": None,
                              "elements": session.element_count(),
                              "nodes": session.node_count(), "seconds": 0.0}
            else:
                clear_first = bool(ctx.pop("next_clear", False))
                generation = mesher.generate(session, cfg, logger, round_index) if not clear_first \
                    else _generate_with_clear(session, cfg, logger, round_index)

            snapshot = mesher.measure(session, cfg, logger, round_index)
            snapshot["error"] = generation.get("error")
            if cfg["run"].get("element_sampling", {}).get("enabled"):
                sampling = metrics_mod.sample_elements(
                    session, logger, cfg,
                    max_elements=int(cfg["run"]["element_sampling"].get("max_elements") or 20000))
                snapshot["sampling"] = sampling
                if sampling.get("ok"):
                    worst = sampling.get("bodies") or []
                    if worst:
                        logger.info(u"单元抽样最差的体: %s"
                                    % u"; ".join(u"%s(边长比 %.1f)" % (w["name"], w["edge_ratio"])
                                                 for w in worst[:3]))

            score = snapshot.get("score") or {}
            # 质量不达标且几何可疑时，才做较慢的碎边扫描
            if score.get("status") != metrics_mod.STATUS_PASS:
                mesher.deep_geometry(session, snapshot, cfg, logger)

            findings = diagnose_mod.diagnose(snapshot, cfg, logger)
            logger.info(u"第 %d 轮结果: %s，得分 %s/100，诊断 %d 条"
                        % (round_index, mesher.summarize_counts(snapshot),
                           compat.fmt_num(score.get("display")), len(findings)))

            # 记录最优（惩罚值越小越好）
            penalty = compat.to_float(score.get("penalty"), 999.0)
            improved = best is None or penalty < best["penalty"] - 1e-9
            if improved and snapshot.get("elements", 0) > 0:
                best = {"penalty": penalty, "round": round_index, "state": round_state,
                        "snapshot": snapshot}

            plan = None
            outcome = {"applied": [], "results": [], "notes": []}
            stop_reason = _stop_reason(snapshot, findings, cfg, round_index)
            if stop_reason is None:
                plan = repair_mod.plan(findings, snapshot, cfg, attempt_counts,
                                       attempted_actions)
                if plan is None:
                    stop_reason = u"诊断出的问题都没有可自动执行的网格层动作"
            report.add_round(snapshot, findings, plan, outcome, generation, requirements)

            if stop_reason is not None:
                logger.info(u"结束循环：%s" % stop_reason)
                verdict["stop_reason"] = stop_reason
                break

            logger.info(u"本轮纠正计划（阶梯 %s）: %s"
                        % (plan.get("step"), u", ".join(repair_mod.ACTION_TITLES.get(a, a)
                                                        for a in plan["actions"])))
            outcome.update(repair_mod.apply(plan, session, cfg, snapshot, logger, ctx))
            ctx["next_clear"] = bool(outcome.get("next_clear"))
            attempt_counts[plan["step"]] = attempt_counts.get(plan["step"], 0) + 1
            for action in plan["actions"]:
                if action not in attempted_actions:
                    attempted_actions.append(action)

            if not outcome.get("applied"):
                # 计划里的动作一个都没成，再试下一步（但避免同一阶梯反复）
                logger.warn(u"本轮纠正动作均未生效，继续下一轮")
                continue

        # -------------------------------------------------- 回到最优设置（可选）
        if (best is not None and cfg["run"]["keep_best"] and not cfg["run"]["dry_run"]
                and best["round"] != _last_index(report.rounds)):
            logger.info(u"按 keep_best：回滚到第 %d 轮设置（惩罚值最低 %s）"
                        % (best["round"], compat.fmt_num(best["penalty"])))
            changed, failures = controls_mod.apply_state(session, best["state"], logger)
            logger.info(u"回滚写入 %d 项，失败 %d 项" % (len(changed), len(failures)))
            if failures:
                logger.warn(u"回滚有 %d 项没能写回（详见报告的环境探测）" % len(failures))
            generation = _generate_with_clear(session, cfg, logger, last_round + 1)
            snapshot = mesher.measure(session, cfg, logger, last_round + 1)
            snapshot["error"] = generation.get("error")
            mesher.deep_geometry(session, snapshot, cfg, logger)
            findings = diagnose_mod.diagnose(snapshot, cfg, logger)
            report.add_round(snapshot, findings, None, {
                "applied": [], "results": [],
                "notes": [u"回滚到第 %d 轮设置后重新生成" % best["round"]]}, generation, None)
            last_round += 1

        if cfg["run"].get("save_project"):
            saved = session.save()
            logger.info(u"保存工程: %s" % (u"成功" if saved else u"失败（见日志）"))

        verdict = _finalize(verdict, cfg, session, report, logger, started)
    except Exception:
        trace = compat.to_text(traceback.format_exc())
        if logger is not None:
            logger.error(u"运行中断：\n%s" % trace)
        verdict = {
            "status": "FAILED",
            "headline": u"脚本异常中断：%s" % trace.strip().splitlines()[-1],
            "traceback": trace,
            "rounds": len(report.rounds) if report is not None else 0,
        }
        if report is not None and cfg is not None:
            try:
                verdict = _finalize(verdict, cfg, session, report, logger, started)
            except Exception:
                pass
        # 连报告都出不来时，至少留一份失败记录，别让用户对着空白目录发呆
        if report is None and cfg is not None:
            try:
                path = os.path.join(cfg["run"]["report_dir"], "运行失败_%s.txt" % compat.stamp())
                compat.write_text(path, u"\n".join([
                    u"meshkit 运行失败", u"时间: %s" % compat.now().strftime("%Y-%m-%d %H:%M:%S"),
                    u"配置: %s" % (cfg.get("_sources", {}).get("user_file") or u"内置默认"),
                    u"", trace,
                    u"",
                    u"常见原因：没有在 Mechanical 的 Automation -> Scripting 里运行（ExtAPI 不存在）。"]))
                verdict["failure_path"] = path
            except Exception:
                pass
    finally:
        if logger is not None:
            logger.info(u"总耗时 %s" % compat.elapsed_text(time.time() - started))
            verdict["log_path"] = getattr(logger, "path", None)
            _print_summary(verdict, logger)

    return verdict


# --------------------------------------------------------------------------
def _write_state_snapshot(cfg, state, log):
    """把"改之前"的网格设置写成 JSON，供 restore_settings.py 一键还原。"""
    try:
        import json
        path = os.path.join(cfg["run"]["report_dir"], "设置快照_%s.json" % compat.stamp())
        payload = {
            "captured_at": compat.now().strftime("%Y-%m-%d %H:%M:%S"),
            "config": cfg.get("_sources") or {},
            "state": state,
        }
        ok = compat.write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))
        if ok:
            log.info(u"改设置前的快照已保存：%s（可用 restore_settings.py 还原）" % path)
            return path
    except Exception:
        log.warn(u"写设置快照失败: %s"
                 % compat.to_text(traceback.format_exc()).strip().splitlines()[-1])
    return None


def _generate_with_clear(session, cfg, log, round_index):
    """先清空再生成（用于补救阶段）。"""
    result = session.generate_mesh(clear_first=True)
    log.info(u"清空后重新生成: %s，单元 %s / 节点 %s"
             % (u"成功" if result["ok"] else u"失败",
                compat.fmt_num(result["elements"]), compat.fmt_num(result["nodes"])))
    return {"ok": bool(result["ok"]), "error": result.get("error"),
            "elements": result.get("elements"), "nodes": result.get("nodes"),
            "seconds": result.get("seconds"),
            "attempts": [{"label": u"清空后重新生成", "ok": result.get("ok"),
                          "error": result.get("error"), "elements": result.get("elements"),
                          "nodes": result.get("nodes"), "seconds": result.get("seconds")}]}


def _last_index(rounds):
    if not rounds:
        return 0
    return rounds[-1]["snapshot"].get("round") or len(rounds)


def _stop_reason(snapshot, findings, cfg, round_index):
    if cfg["run"]["dry_run"]:
        return u"dry-run 模式，只做一次分析"
    score = snapshot.get("score") or {}
    errors = [f for f in findings if f.get("severity") == diagnose_mod.SEV_ERROR]
    budget = cfg.get("budget") or {}
    basis = budget.get("basis", "elements")
    cap = int(budget.get("hard_cap") or 0)
    count = int(snapshot.get(basis) or 0)
    over_cap = bool(cap and count > cap)

    if not errors and not over_cap and score.get("status") != metrics_mod.STATUS_FAIL:
        return u"网格与质量均达标"
    if not cfg["run"]["auto_repair"]:
        return u"配置关闭了自动纠正"
    if round_index >= cfg["run"]["max_rounds"]:
        return u"达到最大轮次 %d" % cfg["run"]["max_rounds"]
    return None


def _finalize(verdict, cfg, session, report, logger, started):
    """生成报告、人工决策清单，并给出最终结论。"""
    rounds = report.rounds
    final = rounds[-1]["snapshot"] if rounds else {}
    findings = rounds[-1]["findings"] if rounds else []
    score = final.get("score") or {}
    errors = [f for f in findings if f.get("severity") == diagnose_mod.SEV_ERROR]
    budget = cfg.get("budget") or {}
    basis = budget.get("basis", "elements")
    over_cap = bool(int(budget.get("hard_cap") or 0) and
                    int(final.get(basis) or 0) > int(budget.get("hard_cap") or 0))

    repaired = []
    for record in rounds:
        for action in (record["outcome"] or {}).get("applied") or []:
            if action not in repaired:
                repaired.append(action)

    unresolved = (bool(errors) or over_cap or
                  score.get("status") == metrics_mod.STATUS_FAIL)
    needs_human = bool(diagnose_mod.manual_scope_findings(findings)) or unresolved

    advice = None
    if needs_human:
        try:
            advice = advisor.build(cfg, final, findings, rounds, logger, report.out_dir)
        except Exception:
            logger.warn(u"生成人工决策清单失败: %s"
                        % compat.to_text(traceback.format_exc()).strip().splitlines()[-1])

    missing_metrics = final.get("metrics_missing") or []
    if final.get("elements", 0) <= 0:
        status = "FAILED"
        headline = u"网格没有生成成功，必须处理错误后才能继续"
    elif errors or over_cap:
        status = "NEEDS_HUMAN"
        headline = (u"自动纠正已到极限：仍有 %d 个 error 级问题需要人工决策" % len(errors)
                    if errors else u"单元/节点数仍超硬上限，需要人工决定放缩方案")
    elif score.get("status") == metrics_mod.STATUS_WARN:
        status = "OK_WITH_WARNINGS"
        headline = u"网格可用，但部分指标仍在 warning 区，建议按报告建议优化"
    else:
        status = "OK"
        headline = u"网格可用，质量指标全部达标"

    verdict.update({
        "status": status,
        "headline": headline,
        "rounds": len(rounds),
        "elements": final.get("elements"),
        "nodes": final.get("nodes"),
        "score": score,
        "unresolved_errors": len(errors),
        "repair_summary": (u"%d 个动作（%s）" % (len(repaired),
                                            u", ".join(repair_mod.ACTION_TITLES.get(a, a)
                                                       for a in repaired))
                          if repaired else u"未触发"),
        "residual_summary": (u", ".join(f["title"] for f in errors[:3]) if errors
                             else (u"无" if not over_cap else u"超预算上限")),
        "advice_path": advice["path"] if advice else None,
        "manual_script": advice["script"] if advice else None,
        "missing_metrics": missing_metrics,
        "seconds": time.time() - started,
    })
    if logger is not None:
        verdict["log_path"] = getattr(logger, "path", None)
    try:
        report.write(verdict)
        plain = report.write_plain(verdict, os.path.join(
            report.out_dir, "网格摘要_%s.txt" % report.stamp))
        verdict["plain_path"] = plain
    except Exception:
        if logger is not None:
            logger.error(u"写报告失败: %s"
                         % compat.to_text(traceback.format_exc()).strip().splitlines()[-1])
    return verdict


def _print_summary(verdict, logger):
    try:
        logger.info(u"-" * 70)
        logger.info(u"结论: %s" % verdict.get("headline", u""))
        logger.info(u"状态: %s　轮次: %s　单元: %s　节点: %s　得分: %s"
                    % (verdict.get("status"), verdict.get("rounds"),
                       compat.fmt_num(verdict.get("elements")),
                       compat.fmt_num(verdict.get("nodes")),
                       compat.fmt_num((verdict.get("score") or {}).get("display"))))
        if verdict.get("markdown_path"):
            logger.info(u"报告: %s" % verdict["markdown_path"])
        if verdict.get("advice_path"):
            logger.info(u"人工决策清单: %s" % verdict["advice_path"])
        if verdict.get("manual_script"):
            logger.info(u"补救脚本（默认全关）: %s" % verdict["manual_script"])
        logger.info(u"-" * 70)
    except Exception:
        pass
