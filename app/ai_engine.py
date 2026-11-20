# -*- coding: utf-8 -*-
"""
ai_engine.py —— AI 分析引擎（Python 3）

两层：
  1) 规则专家系统：把本次调试中确认的规则固化成检查项，给出确定的诊断与建议
  2) 大模型：可选，有 API Key 时把「状态 + 规则结论」交给模型生成自然语言分析与方案

环境变量：
  DEEPSEEK_API_KEY   大模型 Key（也可写在 app/config.json 的 llm.api_key）
  DEEPSEEK_MODEL     默认 deepseek-chat
"""
from __future__ import annotations

import json
import os
import urllib.request

RULE_SET = [
    # (id, 名称, 严重度)
    ("defeature_le_min", "Defeature Size ≤ Minimum Size", "error"),
    ("unit_mm", "单位系统 mm/kg/N", "warn"),
    ("failed_bodies", "无网格失败体", "error"),
    ("node_budget", "节点数在预算内", "warn"),
    ("mesh_solved", "网格状态 Solved/UpToDate", "warn"),
    ("shell_no_sweep", "壳体不应用 Sweep", "warn"),
    ("sizes_in_range", "尺寸在 2~60 mm 合理区间", "warn"),
]


def _num(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _parse_mm(text):
    """把 '40 [mm]' / '0.04 [m]' 解析成 mm 数值"""
    if text is None:
        return None
    s = str(text).strip()
    try:
        val = float(s.split("[")[0].strip())
    except Exception:
        return None
    low = s.replace(" ", "").lower()
    if "[m]" in low and "[mm]" not in low:
        return val * 1000.0
    return val


def run_rules(status: dict, budget_nodes: int = 7000000) -> list:
    """返回诊断列表：[{id, level, title, detail, action}]"""
    out = []
    if not status or not status.get("ok"):
        return [{
            "id": "bridge", "level": "error",
            "title": "未连接 Mechanical",
            "detail": "桥接代理没有响应，无法读取模型状态。",
            "action": "在 Mechanical 的 Scripting 面板运行 execfile(\"D:/ai/meshkit/app/bridge_agent.py\")",
        }]

    st = status.get("settings") or {}
    minimum = _parse_mm(st.get("MinimumSize"))
    defeature = _parse_mm(st.get("DefeatureTolerance"))

    # 1) 黄色项根因
    if minimum is not None and defeature is not None:
        if defeature > minimum:
            out.append({
                "id": "defeature_le_min", "level": "error",
                "title": "黄色项：Defeature 大于 Minimum Size",
                "detail": "Defeature = %.2f mm，Minimum Size = %.2f mm。该状态下 Mechanical 拒绝划网格（API 静默返回 0 单元）。"
                          % (defeature, minimum),
                "action": "把 Minimum Size 提到 ≥ %.1f mm（建议 10 mm）" % defeature,
            })

    # 2) 单位
    unit = str(status.get("unit_system") or "")
    _u = unit.lower()
    # StandardNMM / Metric (mm, kg, N...) 都算 mm 制；StandardMKS 才是米制
    is_mm = ("mm" in _u) or ("standardnmm" in _u) or ("nmm" in _u)
    if unit and not is_mm:
        out.append({
            "id": "unit_mm", "level": "warn",
            "title": "单位系统不是 mm/kg/N",
            "detail": "当前 %s。工程单位是米时，脚本里把 0.235 当 235 用的误判风险很高。" % unit,
            "action": "右下角单位下拉选 Metric (mm, kg, N, s, mV, mA)",
        })

    # 3) 失败体
    failed = status.get("failed_bodies") or []
    if failed:
        names = "、".join(b.get("name", "?") for b in failed[:5])
        more = "" if len(failed) <= 5 else " 等 %d 个" % len(failed)
        out.append({
            "id": "failed_bodies", "level": "error",
            "title": "%d 个体网格失败" % len(failed),
            "detail": "ObjectState 不是 Meshed：%s%s" % (names, more),
            "action": "点「自动修复」：把失败体从 Sweep 摘出并逐个减半尺寸后重划（最多 2 轮）",
        })

    # 4) 节点预算
    nodes = int(_num(status.get("nodes")))
    if nodes > budget_nodes:
        out.append({
            "id": "node_budget", "level": "warn",
            "title": "节点数超预算",
            "detail": "当前 %s 节点，预算 %s。" % (f"{nodes:,}", f"{budget_nodes:,}"),
            "action": "放粗底板类（垫板/横梁/纵梁）尺寸，或降低壳体尺寸",
        })

    # 5) 网格状态
    ms = str(status.get("mesh_state") or "")
    if ms and ms not in ("Solved",):
        out.append({
            "id": "mesh_solved", "level": "warn" if ms == "NotSolved" else "info",
            "title": "网格状态：%s" % ms,
            "detail": "ObjectState=%s，DetailState=%s" % (ms, status.get("mesh_detail_state")),
            "action": "若刚改过设置，需要重新生成网格",
        })

    # 6) 壳体 + Sweep
    controls = status.get("controls") or []
    shells = set()
    for c in controls:
        if str(c.get("name", "")).startswith("SHELL_"):
            shells.add(str(c.get("name"))[6:])
    for c in controls:
        nm = str(c.get("name", ""))
        if nm.startswith("SWEEP_"):
            cat = nm[6:].replace("_实体", "")
            if cat in shells:
                out.append({
                    "id": "shell_no_sweep", "level": "warn",
                    "title": "Sweep 控制项可能含壳体：%s" % nm,
                    "detail": "Mechanical 对壳体不允许 Sweep，写入会被忽略或报错。",
                    "action": "把该类别拆成 SHELL_（Automatic）+ SWEEP_（纯实体）",
                })

    # 7) 尺寸区间
    bad_sizes = []
    for c in controls:
        nm = str(c.get("name", ""))
        if not nm.startswith("SIZE_"):
            continue
        v = _parse_mm(c.get("size"))
        if v is None:
            continue
        if v < 2.0 or v > 60.0:
            bad_sizes.append("%s=%gmm" % (nm[5:], v))
    if bad_sizes:
        out.append({
            "id": "sizes_in_range", "level": "warn",
            "title": "尺寸超出合理区间",
            "detail": "、".join(bad_sizes[:6]),
            "action": "调整到 2~60 mm；大件（块体）可到 60，薄片不低于 2",
        })

    return out


def build_prompt(status: dict, findings: list, quality_text: str = "") -> str:
    lines = [
        "你是 ANSYS Mechanical 网格专家。下面是集装箱模型当前的网格状态与规则检查结果。",
        "请用中文给出：1) 一句话结论；2) 主要问题（按严重度排序）；3) 具体可执行的处理步骤（含建议尺寸数值）。",
        "不要重复罗列数据，直接给判断和动作。",
        "",
        "## 当前状态",
        json.dumps(status, ensure_ascii=False, indent=1)[:6000],
        "",
        "## 规则检查结果",
        json.dumps(findings, ensure_ascii=False, indent=1)[:4000],
    ]
    if quality_text:
        lines += ["", "## 质量报告", quality_text[:4000]]
    return "\n".join(lines)


def call_llm(prompt: str, api_key: str, model: str = "deepseek-chat",
             base_url: str = "https://api.deepseek.com") -> str:
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "你是经验丰富的 ANSYS Mechanical 仿真工程师，回答简洁、可执行。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 1200,
    }).encode("utf-8")

    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def load_config(app_dir: str) -> dict:
    path = os.path.join(app_dir, "config.json")
    cfg = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    llm = cfg.get("llm") or {}
    llm.setdefault("api_key", os.environ.get("DEEPSEEK_API_KEY", ""))
    llm.setdefault("model", os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
    llm.setdefault("base_url", "https://api.deepseek.com")
    cfg["llm"] = llm
    cfg.setdefault("budget_nodes", 7000000)
    return cfg


def analyze(status: dict, quality_text: str = "", app_dir: str = ".") -> dict:
    cfg = load_config(app_dir)
    findings = run_rules(status, int(cfg.get("budget_nodes", 7000000)))
    result = {
        "findings": findings,
        "summary": summarize(findings, status),
        "llm": None,
        "llm_error": None,
    }
    key = (cfg.get("llm") or {}).get("api_key") or ""
    if key:
        try:
            result["llm"] = call_llm(
                build_prompt(status, findings, quality_text),
                api_key=key,
                model=(cfg["llm"].get("model") or "deepseek-chat"),
                base_url=(cfg["llm"].get("base_url") or "https://api.deepseek.com"),
            )
        except Exception as exc:  # noqa: BLE001
            result["llm_error"] = f"{type(exc).__name__}: {exc}"
    return result


def summarize(findings: list, status: dict) -> str:
    errors = [f for f in findings if f["level"] == "error"]
    warns = [f for f in findings if f["level"] == "warn"]
    nodes = int(_num((status or {}).get("nodes")))
    elements = int(_num((status or {}).get("elements")))
    head = f"单元 {elements:,} / 节点 {nodes:,}" if elements else "尚无网格"
    if errors:
        return f"{head}；有 {len(errors)} 个阻塞问题需要先处理：" + "；".join(e["title"] for e in errors)
    if warns:
        return f"{head}；网格可用，但有 {len(warns)} 条提醒：" + "；".join(w["title"] for w in warns[:4])
    return f"{head}；各项检查通过。"
