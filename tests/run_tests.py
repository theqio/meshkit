# -*- coding: utf-8 -*-
"""meshkit 自测：在真 IronPython 2.7 上把整条闭环跑一遍（用假 ExtAPI）。

运行::

    "D:\\Program Files\\ANSYS Inc\\v221\\commonfiles\\IronPython\\ipy64.exe" run_tests.py

覆盖
----
1. 配置加载 / 校验
2. 干净模型：一轮通过、不产生人工决策清单
3. 集装箱场景：0 尺寸控制 + 退化几何 + 壳面四边形失败 -> 自动修复
4. 单元预算超限 -> 按 size^exp 放缩
5. GenerateMesh 首次失败 -> 清空重试成功
6. 持续报错 -> 判定失败 + 产出人工决策清单
7. 怎么改都修不好 -> 走人工决策分支
8. dry-run -> 一个设置都不改
9. 有体没划上网 -> 补控制项后恢复
10. 报告内容完整性
"""
from __future__ import division

import os
import shutil
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import fake_extapi  # noqa: E402

from meshkit import compat          # noqa: E402
from meshkit import config as config_mod  # noqa: E402
from meshkit import engine          # noqa: E402
from meshkit import session as session_mod  # noqa: E402

OUT_ROOT = os.path.join(HERE, "_out")

_results = []


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def check(condition, message):
    _results.append((bool(condition), message))
    return bool(condition)


def clean_dir(path):
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    compat.mkdirs(path)


def run_engine(scenario, preset, name, overrides=None):
    out_dir = os.path.join(OUT_ROOT, name)
    clean_dir(out_dir)
    env = fake_extapi.install(scenario)
    try:
        base = {"preset": preset,
                "run": {"report_dir": out_dir, "console_verbose": False}}
        verdict = engine.run(None, overrides=deep_merge(base, overrides or {}))
    finally:
        fake_extapi.uninstall()
    return verdict, env, out_dir


def deep_merge(base, extra):
    out = dict(base)
    for key, value in extra.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def control_by_name(env, name):
    for control in env["mesh"].Children:
        if compat.to_text(control.Name) == name:
            return control
    return None


def control_size(control):
    try:
        return float(control.ElementSize.Value)
    except Exception:
        return None


# --------------------------------------------------------------------------
# 测试
# --------------------------------------------------------------------------
def test_config():
    cfg = config_mod.load(None, overrides={"preset": "container_1000c4"})
    check(cfg["mesh"]["element_size_mm"] == 36.0, "集装箱预设全局尺寸应为 36 mm")
    check(len(cfg["controls"]) == 17, "集装箱预设应有 17 个控制项，实际 %d" % len(cfg["controls"]))
    check(cfg["budget"]["basis"] == "nodes", "集装箱预设预算基准应为 nodes")
    problems = config_mod.validate(cfg)
    check(len(problems) == 0, "集装箱预设不应有校验问题: %s" % problems)

    cfg2 = config_mod.load(None, overrides={"preset": "generic_default"})
    check(cfg2["mesh"]["element_size_mm"] == 10.0, "通用预设全局尺寸应为 10 mm")
    check(len(cfg2["controls"]) == 0, "通用预设默认没有控制项")

    # 尺寸单位解析
    cfg3 = config_mod.load(None, overrides={
        "preset": "generic_default",
        "mesh": {"element_size_mm": "2 cm"},
        "controls": [{"name": "t", "type": "body_sizing", "size_mm": "0.5 in",
                      "bodies": ["A*"]}]})
    check(abs(cfg3["mesh"]["element_size_mm"] - 20.0) < 1e-9, "『2 cm』应解析为 20 mm")
    check(abs(cfg3["controls"][0]["size_mm"] - 12.7) < 1e-9, "『0.5 in』应解析为 12.7 mm")

    # 阈值方向校验
    bad = config_mod.load(None, overrides={
        "preset": "generic_default",
        "quality": {"metrics": {"Skewness": {"direction": "low", "warn": 0.99, "error": 0.5}}}})
    problems = config_mod.validate(bad)
    check(any(u"Skewness" in p for p in problems), "warn > error 应被校验出来")
    return []


def test_simple_good_path():
    verdict, env, out_dir = run_engine(
        fake_extapi.simple_good_scenario(), "generic_default", "simple_good",
        {"run": {"max_rounds": 4},
         "mesh": {"capture_proximity": True, "smoothing": "High",
                  "smoothing_iterations": 10}})
    check(verdict["status"] == "OK", "干净模型应判 OK，实际 %s（%s）"
          % (verdict["status"], verdict.get("headline")))
    check(verdict["rounds"] == 1, "干净模型应一轮结束，实际 %d 轮" % verdict["rounds"])
    check(not verdict.get("advice_path"), "干净模型不应产生人工决策清单")
    check(os.path.isfile(verdict.get("markdown_path") or ""), "应生成 Markdown 报告")
    check(os.path.isfile(verdict.get("csv_path") or ""), "应生成 CSV")
    return []


def test_container_repairs():
    scenario = fake_extapi.container_like_scenario()
    verdict, env, out_dir = run_engine(scenario, "container_1000c4", "container_repairs",
                                       {"run": {"max_rounds": 8}})
    mesh = env["mesh"]
    check(verdict["status"] in ("OK", "OK_WITH_WARNINGS"),
          "集装箱场景应收敛到可用，实际 %s（%s）" % (verdict["status"], verdict.get("headline")))
    check(verdict["rounds"] >= 3, "应经历多轮纠正，实际 %d 轮" % verdict["rounds"])

    defeature = float(getattr(mesh.DefeatureTolerance, "Value", 0) or 0)
    check(defeature > 0, "DefeatureTolerance 应被自动放大，实际 %s" % defeature)

    legacy = control_by_name(env, u"遗留控制")
    check(legacy is not None, "应能找到『遗留控制』")
    if legacy is not None:
        check(control_size(legacy) > 0, "0 尺寸控制项应被修复，实际 %s" % control_size(legacy))

    mid = control_by_name(env, u"中隔断")
    if mid is not None:
        check(abs(control_size(mid) - 20.0) < 1e-6, "『中隔断』应被设为 20 mm，实际 %s"
              % control_size(mid))

    check(mesh.QuadMesh is False, "壳面四边形失败后应关闭 QuadMesh")
    report = compat.read_text(verdict["markdown_path"])
    check(report is not None and u"诊断与自动处理" in report, "报告应包含诊断章节")
    check(report is not None and u"质量指标" in report, "报告应包含质量指标章节")
    return []


def test_budget_scaling():
    scenario = fake_extapi.simple_good_scenario()
    scenario = dict(scenario)
    scenario["node_multiplier"] = 3.0
    verdict, env, out_dir = run_engine(scenario, "container_1000c4", "budget",
                                       {"run": {"max_rounds": 6}})
    mesh = env["mesh"]
    cap = 9000000
    check(mesh.Nodes <= cap * 1.02, "放缩后节点数应回到上限内，实际 %s（上限 %s）"
          % (compat.fmt_num(mesh.Nodes), compat.fmt_num(cap)))
    report = compat.read_text(verdict["markdown_path"])
    check(report is not None and u"按 size^exp 放缩" in report,
          "报告里应记录尺寸放缩动作")
    return []


def test_retry_on_failure():
    scenario = fake_extapi.simple_good_scenario()
    scenario = dict(scenario)
    scenario["fail_times"] = 1
    verdict, env, out_dir = run_engine(scenario, "generic_default", "retry",
                                       {"run": {"max_rounds": 4}})
    check(env["mesh"]._attempts >= 2, "首次失败后应重试，实际尝试 %d 次" % env["mesh"]._attempts)
    check(verdict["status"] in ("OK", "OK_WITH_WARNINGS"),
          "重试后应成功，实际 %s（%s）" % (verdict["status"], verdict.get("headline")))
    return []


def test_total_failure():
    scenario = fake_extapi.simple_good_scenario()
    scenario = dict(scenario)
    scenario["fail_times"] = 99
    verdict, env, out_dir = run_engine(scenario, "generic_default", "fail",
                                       {"run": {"max_rounds": 3}})
    check(verdict["status"] == "FAILED", "持续失败应判 FAILED，实际 %s" % verdict["status"])
    check(bool(verdict.get("advice_path")), "持续失败应产出人工决策清单")
    check(os.path.isfile(verdict.get("advice_path") or ""), "人工决策清单文件应存在")
    check(os.path.isfile(verdict.get("manual_script") or ""), "应生成补救脚本文件")
    return []


def test_unresolvable():
    scenario = fake_extapi.container_like_scenario()
    scenario = dict(scenario)
    scenario["stuck_bad_quality"] = True
    verdict, env, out_dir = run_engine(scenario, "container_1000c4", "unresolvable",
                                       {"run": {"max_rounds": 8}})
    check(verdict["status"] == "NEEDS_HUMAN",
          "改不动时应判 NEEDS_HUMAN，实际 %s（%s）" % (verdict["status"], verdict.get("headline")))
    check(verdict.get("unresolved_errors", 0) > 0, "应记录残留 error 数")
    advice = compat.read_text(verdict.get("advice_path") or "")
    check(advice is not None and u"人工决策清单" in advice, "人工决策清单应可读")
    script = compat.read_text(verdict.get("manual_script") or "")
    check(script is not None and u"ENABLE_ELEMENT_ORDER_FIRST = False" in script,
          "补救脚本默认开关应全为 False")
    return []


def test_dry_run():
    scenario = fake_extapi.container_like_scenario()
    env = fake_extapi.install(scenario)
    out_dir = os.path.join(OUT_ROOT, "dry_run")
    clean_dir(out_dir)
    try:
        before_size = float(env["mesh"].ElementSize.Value)
        before_controls = len(env["mesh"].Children)
        verdict = engine.run(None, overrides={
            "preset": "container_1000c4",
            "run": {"report_dir": out_dir, "dry_run": True, "console_verbose": False}})
        after_size = float(env["mesh"].ElementSize.Value)
        after_controls = len(env["mesh"].Children)
    finally:
        fake_extapi.uninstall()
    check(abs(before_size - after_size) < 1e-9, "dry-run 不应改全局尺寸（%s -> %s）"
          % (before_size, after_size))
    check(before_controls == after_controls, "dry-run 不应新建控制项（%d -> %d）"
          % (before_controls, after_controls))
    check(verdict["rounds"] == 1, "dry-run 只跑一轮，实际 %d" % verdict["rounds"])
    check(os.path.isfile(verdict.get("markdown_path") or ""), "dry-run 也要出报告")
    return []


def test_unmeshed_body():
    scenario = fake_extapi.simple_good_scenario()
    scenario = dict(scenario)
    scenario["bodies"] = list(scenario["bodies"]) + [fake_extapi.FakeBody(3, u"卡住体")]
    scenario["stuck_bodies"] = [u"卡住体"]
    verdict, env, out_dir = run_engine(scenario, "generic_default", "unmeshed",
                                       {"run": {"max_rounds": 5}})
    stuck = control_by_name(env, u"自动补网 - 卡住体")
    check(stuck is not None, "应为卡住体补一个 Body Sizing")
    check(env["mesh"].body_elements.get(3, 0) > 0, "补控制项后该体应有单元，实际 %s"
          % env["mesh"].body_elements.get(3))
    check(verdict["status"] in ("OK", "OK_WITH_WARNINGS"),
          "补网后应收敛，实际 %s（%s）" % (verdict["status"], verdict.get("headline")))
    return []


def test_state_rollback():
    """回滚机制：改坏 -> apply_state -> 一切复原。"""
    from meshkit import controls as controls_mod
    from meshkit import config as config_mod
    env = fake_extapi.install(fake_extapi.container_like_scenario())
    try:
        log = compat.Logger(verbose=False)
        session = session_mod.Session(log=log)
        cfg = config_mod.load(None, overrides={"preset": "container_1000c4"})
        state = controls_mod.capture_state(session, cfg)
        before = {
            "global": float(env["mesh"].ElementSize.Value),
            "defeature": float(env["mesh"].DefeatureTolerance.Value),
            "mid": control_size(control_by_name(env, u"中隔断")),
            "controls": len(env["mesh"].Children),
        }

        # 故意改坏
        env["mesh"].ElementSize = fake_extapi.FakeQuantity(9.0)
        env["mesh"].DefeatureTolerance = fake_extapi.FakeQuantity(2.0)
        control_by_name(env, u"中隔断").ElementSize = fake_extapi.FakeQuantity(1.0)

        changed, failures = controls_mod.apply_state(session, state, log)
        after = {
            "global": float(env["mesh"].ElementSize.Value),
            "defeature": float(env["mesh"].DefeatureTolerance.Value),
            "mid": control_size(control_by_name(env, u"中隔断")),
        }
    finally:
        fake_extapi.uninstall()

    check(len(changed) >= 3, "回滚应写回至少 3 项，实际 %d（失败 %d）" % (len(changed), len(failures)))
    check(abs(after["global"] - before["global"]) < 1e-9,
          "全局尺寸应回滚到 %s，实际 %s" % (before["global"], after["global"]))
    check(abs(after["defeature"] - before["defeature"]) < 1e-9,
          "DefeatureTolerance 应回滚到 %s，实际 %s" % (before["defeature"], after["defeature"]))
    check(abs((after["mid"] or 0) - (before["mid"] or 0)) < 1e-9,
          "控制项尺寸应回滚到 %s，实际 %s" % (before["mid"], after["mid"]))
    return []


def test_sampling_degrades():
    """单元抽样接口不可用时应降级，不影响主流程。"""
    verdict, env, out_dir = run_engine(
        fake_extapi.container_like_scenario(), "container_1000c4", "sampling",
        {"run": {"max_rounds": 8, "element_sampling": {"enabled": True, "max_elements": 500}}})
    check(verdict["status"] in ("OK", "OK_WITH_WARNINGS", "NEEDS_HUMAN"),
          "开启抽样后仍应正常结束，实际 %s" % verdict["status"])
    check(os.path.isfile(verdict.get("markdown_path") or ""), "抽样降级后仍应出报告")
    return []


def test_probe_env():
    """探针脚本本身能跑通，并写出期望的章节。"""
    out_dir = os.path.join(OUT_ROOT, "probe")
    clean_dir(out_dir)
    os.environ["MESHKIT_PROBE_OUT"] = out_dir
    env = fake_extapi.install(fake_extapi.container_like_scenario())
    try:
        fake_extapi._build_env  # 仅确认模块可用
        path = os.path.join(ROOT, "probe_env.py")
        namespace = {"__name__": "__main__", "__file__": path}
        execfile(path, namespace)   # noqa: F821  (Python 2 内置)
    finally:
        fake_extapi.uninstall()
        os.environ.pop("MESHKIT_PROBE_OUT", None)

    files = ([f for f in os.listdir(out_dir)
              if f.startswith(u"环境探针_") and f.endswith(u".txt")]
             if os.path.isdir(out_dir) else [])
    check(len(files) == 1, "探针应写出 1 个结果文件，实际 %d 个" % len(files))
    text = compat.read_text(os.path.join(out_dir, files[0])) if files else None
    for needle in [u"基础", u"能力探测", u"几何", u"网格控制项", u"质量指标", u"质量限值", u"结论"]:
        check(text is not None and needle in text, "探针输出应包含『%s』" % needle)
    check(text is not None and u"MeshMetric 已还原" in text, "探针应报告 MeshMetric 已还原")
    check(text is not None and u"MeshMetric 已还原: True" in text,
          "MeshMetric 应真的还原成功（报告里显示 True）")
    return []


def test_report_content():
    verdict, env, out_dir = run_engine(
        fake_extapi.container_like_scenario(), "container_1000c4", "report",
        {"run": {"max_rounds": 8}})
    text = compat.read_text(verdict.get("markdown_path") or "")
    check(text is not None, "报告应可读")
    for needle in [u"结论速览", u"环境与生效需求", u"迭代过程", u"最终网格",
                   u"质量指标", u"诊断与自动处理", u"自动纠正明细", u"产出文件"]:
        check(text is not None and needle in text, "报告应包含『%s』章节" % needle)
    csv_text = compat.read_text(verdict.get("csv_path") or "")
    check(csv_text is not None and u"ElementQuality" in csv_text, "CSV 应包含指标数据")
    return []


# --------------------------------------------------------------------------
def main():
    tests = [
        (u"配置加载与校验", test_config),
        (u"干净模型一轮通过", test_simple_good_path),
        (u"集装箱场景自动修复", test_container_repairs),
        (u"单元预算放缩", test_budget_scaling),
        (u"失败后清空重试", test_retry_on_failure),
        (u"持续报错的人工决策", test_total_failure),
        (u"怎么改都修不好", test_unresolvable),
        (u"dry-run 不改设置", test_dry_run),
        (u"未划上网的体", test_unmeshed_body),
        (u"回滚机制", test_state_rollback),
        (u"单元抽样降级", test_sampling_degrades),
        (u"环境探针脚本", test_probe_env),
        (u"报告内容完整性", test_report_content),
    ]
    failures = 0
    for name, func in tests:
        start = len(_results)
        try:
            func()
            problems = [m for ok, m in _results[start:] if not ok]
        except Exception:
            problems = [u"异常: %s" % traceback.format_exc()]
        if problems:
            failures += 1
            print(u"[FAIL] %s" % name)
            for problem in problems:
                print(u"       - %s" % problem)
        else:
            print(u"[ OK ] %s（%d 项断言）" % (name, len(_results) - start))

    total = len(_results)
    passed = len([1 for ok, _m in _results if ok])
    print(u"")
    print(u"%d 个测试，%d 个失败；断言 %d/%d 通过" % (len(tests), failures, passed, total))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
