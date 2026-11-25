# -*- coding: utf-8 -*-
"""
server.py —— MeshKit AI 软件后端（Python 3，仅标准库）

启动：
    python server.py            # 默认 http://127.0.0.1:8765
浏览器打开该地址即为软件界面。

与 Mechanical 的通信：
    写 app/ipc/cmd.json  →  Mechanical 里的 bridge_agent 轮询执行
    ← 写 app/ipc/result.json

API：
    GET  /api/ping              探测桥接是否在线
    GET  /api/status            模型状态
    POST /api/op                通用命令 {op, payload}
    POST /api/ai                AI 分析（规则 + 可选大模型）
    GET  /api/config            读取配置（尺寸方案等）
    POST /api/config            保存配置
    GET  /api/reports           历史报告列表
    GET  /api/report?name=      读取某份报告
"""
from __future__ import annotations

import json
import re
import subprocess
import os
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(APP_DIR)
# 让窗口坐标一律用物理像素（多屏/缩放下必须）
try:
    import ctypes as _ct
    try:
        _ct.windll.user32.SetProcessDpiAwarenessContext(_ct.c_void_p(-4))
    except Exception:
        try:
            _ct.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
except Exception:
    pass

IPC_DIR = os.path.join(APP_DIR, "ipc")
STATIC_DIR = os.path.join(APP_DIR, "static")
REPORTS_DIR = os.path.join(ROOT, "reports")
CFG_PATH = os.path.join(APP_DIR, "config.json")
CMD_PATH = os.path.join(IPC_DIR, "cmd.json")
RES_PATH = os.path.join(IPC_DIR, "result.json")
STATE_PATH = os.path.join(IPC_DIR, "bridge_state.json")
CANCEL_PATH = os.path.join(IPC_DIR, "cancel.flag")
PROGRESS_PATH = os.path.join(IPC_DIR, "progress.json")

HOST = "127.0.0.1"
PORT = int(os.environ.get("MESHKIT_PORT", "8765"))
OP_TIMEOUT = {"ping": 8, "status": 30, "apply": 600, "mesh": 2400,
              "fix_failed": 3000, "quality": 600, "exec": 900}

_ipc_lock = threading.Lock()

DEFAULT_SIZES = {
    "垫板": 40, "横梁": 35, "纵梁": 35, "板": 25, "立柱": 25, "侧封板": 25,
    "波纹墙": 30, "隔板": 30, "顶部支撑梁": 30, "薄垫板": 20, "薄板": 20,
    "百叶窗框": 35, "电缆口框": 35, "侧板固定件": 30, "结构件": 30, "块体": 40,
}
DEFAULT_SETTINGS = {
    "ElementSize": 40, "MinimumSize": 10, "DefeatureTolerance": 1,
    "CurvatureNormalAngle": 18, "CaptureCurvature": True, "CaptureProximity": False,
}


# ============================================================================
#  配置
# ============================================================================
_MODEL_CFG_KEYS = ("sizes", "methods", "thin_divisions", "settings", "advanced")


def _model_cfg_path(key=None):
    try:
        k = key or _model_key()
    except Exception:
        k = "default"
    return os.path.join(APP_DIR, "model_config__%s.json" % k)


def load_config() -> dict:
    cfg = {}
    if os.path.isfile(CFG_PATH):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    # 叠加当前模型独立配置（sizes/methods/thin_divisions/settings/advanced）
    try:
        mp = _model_cfg_path()
        if os.path.isfile(mp):
            with open(mp, "r", encoding="utf-8") as f:
                mcfg = json.load(f) or {}
            for k in _MODEL_CFG_KEYS:
                if k in mcfg:
                    cfg[k] = mcfg[k]
    except Exception:
        pass
    # 同步当前模型的分模型标记（thin_cats/hole_cats/locked_cats/sweep_ok）
    try:
        md, mok = _flags_load()
        if mok:
            for fk in ("thin_cats", "thin_off", "hole_cats", "locked_cats", "sweep_ok"):
                if fk in md:
                    cfg[fk] = list(md.get(fk) or [])
    except Exception:
        pass
    cfg.setdefault("sizes", dict(DEFAULT_SIZES))
    cfg.setdefault("settings", dict(DEFAULT_SETTINGS))
    cfg.setdefault("methods", {})          # {类别: 方法名}，默认空=全部 Automatic
    cfg.setdefault("advanced", {})         # {类别: {参数 API 名: 值}} 高级参数
    cfg.setdefault("history", {})          # 旧版自动记录（仅用于一次性迁移）
    cfg.setdefault("sweep_ok", [])         # 已验证扫掠成功的类别
    cfg.setdefault("hole_cats", [])        # 圆孔标签
    cfg.setdefault("locked_cats", [])      # 已锁定的类别
    cfg.setdefault("thin_cats", [])        # 薄板标签（按模型独立）
    cfg.setdefault("budget_nodes", 7000000)
    cfg.setdefault("budget_elements", 1450000)
    cfg.setdefault("llm", {})
    cfg["llm"].setdefault("api_key", "")
    cfg["llm"].setdefault("model", "deepseek-chat")
    cfg["llm"].setdefault("base_url", "https://api.deepseek.com")
    return cfg


def save_config(cfg: dict) -> None:
    # 模型级字段单独写入 model_config__<model>.json，避免跨模型串写
    try:
        mdata = {k: cfg[k] for k in _MODEL_CFG_KEYS if k in cfg}
        with open(_model_cfg_path(), "w", encoding="utf-8") as f:
            json.dump(mdata, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    # 全局配置仅保存跨模型字段，不再把当前模型的 sizes/methods 写回全局 config.json
    gcfg = {}
    if os.path.isfile(CFG_PATH):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                gcfg = json.load(f) or {}
        except Exception:
            gcfg = {}
    for k, v in cfg.items():
        if k in _MODEL_CFG_KEYS:
            continue
        gcfg[k] = v
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(gcfg, f, ensure_ascii=False, indent=2)


# ============================================================================
#  常用配置（手动保存）
# ============================================================================
FAVORITE_THRESHOLD = 3          # 仅用于迁移旧版"用满 3 次"的记录


def favorites_list() -> list:
    cfg = load_config()
    favs = cfg.get("favorites")
    # 一次性迁移：把旧版"用满 3 次自动收录"的记录转成手动配置
    if not cfg.get("favorites_migrated"):
        if favs is None:
            favs = []
        if cfg.get("history"):
            for entry in (cfg.get("history") or {}).values():
                if int(entry.get("count", 0)) < FAVORITE_THRESHOLD:
                    continue
                favs.append({"name": "配置 %d" % (len(favs) + 1),
                             "sizes": entry.get("sizes") or {},
                             "methods": entry.get("methods") or {},
                             "created": entry.get("last") or "",
                             "uses": int(entry.get("count", 0))})
        cfg["favorites"] = favs
        cfg["favorites_migrated"] = True
        save_config(cfg)
    out = []
    for i, f in enumerate(favs or []):
        out.append({"index": i,
                    "name": f.get("name") or ("配置 %d" % (i + 1)),
                    "sizes": f.get("sizes") or {},
                    "methods": f.get("methods") or {},
                    "created": f.get("created") or "",
                    "uses": int(f.get("uses") or 0)})
    return out


def favorite_save(name: str, sizes: dict, methods: dict) -> dict:
    """手动保存：同名覆盖，否则新增"""
    cfg = load_config()
    favs = cfg.setdefault("favorites", favorites_list())
    name = (name or "").strip() or ("配置 %d" % (len(favs) + 1))
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for f in favs:
        if (f.get("name") or "").strip() == name:
            f["sizes"] = dict(sizes or {})
            f["methods"] = dict(methods or {})
            f["created"] = now
            save_config(cfg)
            return {"ok": True, "updated": True, "name": name}
    favs.append({"name": name, "sizes": dict(sizes or {}),
                 "methods": dict(methods or {}), "created": now, "uses": 0})
    save_config(cfg)
    return {"ok": True, "updated": False, "name": name}


def favorite_rename(old: str, new: str) -> bool:
    cfg = load_config()
    favs = cfg.get("favorites") or []
    new = (new or "").strip()
    if not new:
        return False
    for f in favs:
        if (f.get("name") or "") == old:
            f["name"] = new[:30]
            save_config(cfg)
            return True
    return False


def favorite_delete(name: str) -> bool:
    cfg = load_config()
    favs = cfg.get("favorites") or []
    kept = [f for f in favs if (f.get("name") or "") != name]
    if len(kept) == len(favs):
        return False
    cfg["favorites"] = kept
    save_config(cfg)
    return True


def favorite_bump_uses(sizes: dict, methods: dict) -> None:
    """应用后给完全一致的配置"使用次数 +1"（只用于显示）"""
    cfg = load_config()
    favs = cfg.get("favorites") or []
    for f in favs:
        if (f.get("sizes") or {}) == (sizes or {}) and \
           (f.get("methods") or {}) == (methods or {}):
            f["uses"] = int(f.get("uses") or 0) + 1
            save_config(cfg)
            return


# ============================================================================
#  错误监视器：把"真正失败"的体记录到 reports/错误记录_YYYYMMDD.md
# ============================================================================
_MON = {"seen": set(), "loaded": False}
_MON_LOCK = threading.Lock()


def _errlog_path():
    return os.path.join(REPORTS_DIR, u"错误记录_%s.md" % time.strftime("%Y%m%d"))


def _errlog_load():
    """启动时把今天已记录的体名读进来，避免重复写"""
    path = _errlog_path()
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("- "):
                    parts = line[2:].split(" | ")
                    if parts:
                        _MON["seen"].add(parts[0].strip())
    except Exception:
        pass


def _errlog_append(name, state, elements, nodes):
    path = _errlog_path()
    new_file = not os.path.isfile(path)
    with open(path, "a", encoding="utf-8") as f:
        if new_file:
            f.write(u"# 网格错误记录 %s\n\n" % time.strftime("%Y-%m-%d"))
            f.write(u"格式：`体名 | ObjectState | 单元数 | 节点数 | 时间`\n\n")
        f.write(u"- %s | %s | %s | %s | %s\n"
                % (name, state, elements, nodes, time.strftime("%H:%M:%S")))


def monitor_loop(poll=12):
    """每 poll 秒读一次状态，把"真正的失败体"记下来。

    判据：网格有单元，且失败体数量在合理范围（1~40）。
    - 失败体 > 40 说明是"网格过期"造成的假失败（改尺寸/换单位后所有体都非 Meshed），不记录
    - 失败体 0 时无事可记
    """
    while True:
        try:
            if bridge_alive():
                with _MON_LOCK:
                    if not _MON["loaded"]:
                        _errlog_load()
                        _MON["loaded"] = True
                st = call_bridge("status", timeout=20)
                if st.get("ok"):
                    failed = st.get("failed_bodies") or []
                    els = st.get("elements") or 0
                    if els > 0 and 0 < len(failed) <= 40:
                        nds = st.get("nodes")
                        for b in failed:
                            nm = b.get("name") or "?"
                            if nm in _MON["seen"]:
                                continue
                            _MON["seen"].add(nm)
                            try:
                                _errlog_append(nm, b.get("state", "?"), els, nds)
                            except Exception:
                                pass
        except Exception:
            pass
        time.sleep(poll)


# 结构角色 -> 推荐尺寸系数（>1 放大/变粗，<1 细化）
SIZE_FACTORS = {
    u"窗框架": 1.8, u"上下框架": 1.8, u"连接结构": 1.6,
    u"两侧隔断支撑架": 1.6,
    u"侧支撑板": 1.8,
    u"角件": 0.55,      # 重要承重结构，需要更细
    u"小三角支撑": 0.7,
}


def apply_size_factor(cat, size):
    """按结构角色调整推荐尺寸；不超过全局尺寸，也不低于最小尺寸"""
    try:
        if size is None:
            return size
        f = SIZE_FACTORS.get(cat)
        if not f:
            return size
        return round(float(size) * f, 1)
    except Exception:
        return size


def els_of(status):
    try:
        return int((status or {}).get("elements") or 0)
    except Exception:
        return 0


# ---------------- 经验库状态 ----------------
_EXP = {"last_action": None, "last_action_ts": 0.0,
        "failed_before": set(), "bodies_cache": None, "bodies_ts": 0.0}


def _bodies_features():
    """体名 -> 几何特征（缓存 90 秒）"""
    now = time.time()
    if _EXP.get("bodies_cache") and now - _EXP.get("bodies_ts", 0) < 90:
        return _EXP["bodies_cache"]
    try:
        sys.path.insert(0, APP_DIR)
        import experience
        res = call_bridge("list_bodies", timeout=60)
        out = {}
        for b in (res.get("bodies") or []):
            f = experience.features(b)
            if f:
                out[b.get("name")] = {"features": f,
                                      "group": b.get("category") or ""}
        _EXP.update({"bodies_cache": out, "bodies_ts": now})
        return out
    except Exception:
        return _EXP.get("bodies_cache") or {}


def _remember_failed(status):
    """随时记住当前失败体集合，供 mesh 后对比"""
    try:
        names = set(b.get("name") for b in (status.get("failed_bodies") or []))
        _EXP["failed_before"] = names
    except Exception:
        pass


def _record_mesh_experience(new_failed):
    """网格化后：对比前后失败集合，写入经验"""
    before = set(_EXP.get("failed_before") or set())
    after = set(new_failed or [])
    if not before and not after:
        return {"fixed": 0, "no_effect": 0, "skipped": "no_failure"}
    # ---- 噪声过滤 ----
    # 1) 失败体异常多：多半是"网格过期"造成的假失败，不能当经验
    if len(before) > 40 or len(after) > 40:
        return {"fixed": 0, "no_effect": 0,
                "skipped": "too_many(%d/%d)" % (len(before), len(after))}
    # 2) 没有记录到"做了什么"，无法归因
    if not _EXP.get("last_action"):
        return {"fixed": 0, "no_effect": 0, "skipped": "no_action"}
    # 3) 同一动作、同一失败集合已记过 -> 不重复记
    sig = (json.dumps(_EXP.get("last_action"), sort_keys=True, ensure_ascii=False),
           ",".join(sorted(before)), ",".join(sorted(after)))
    if _EXP.get("last_sig") == sig:
        return {"fixed": 0, "no_effect": 0, "skipped": "duplicate"}
    _EXP["last_sig"] = sig
    feats = _bodies_features()
    action = _EXP.get("last_action") or {"type": "mesh", "note": "重新划分"}
    fixed = [n for n in before if n not in after]
    still = [n for n in (before & after)]
    pairs = []
    for n in fixed:
        info = feats.get(n) or {}
        pairs.append({"body": n, "group": info.get("group"),
                      "problem": "mesh_failed", "outcome": "fixed",
                      "features": info.get("features")})
    for n in still:
        info = feats.get(n) or {}
        pairs.append({"body": n, "group": info.get("group"),
                      "problem": "mesh_failed", "outcome": "no_effect",
                      "features": info.get("features")})
    try:
        sys.path.insert(0, APP_DIR)
        import experience
        n = experience.record_batch(pairs, action, model="")
        return {"fixed": len(fixed), "no_effect": len(still), "recorded": n}
    except Exception:
        return {"fixed": len(fixed), "no_effect": len(still), "recorded": 0}


_BRIDGE_LOCK = threading.RLock()

_QCACHE = {"els": -1, "ts": 0.0, "items": {}}
# 默认只算这三项（稳定、够用）；其余按需单独算
_QUALITY_DEFAULT = [u"Element Quality", u"Skewness", u"Aspect Ratio"]
_QUALITY_EXTRA = [u"Jacobian Ratio (MAPDL)", u"Warping Factor",
                  u"Maximum Corner Angle", u"Parallel Deviation"]


def _quality_items(status):
    """只读缓存，不触发计算"""
    els = status.get("elements") or 0
    if _QCACHE.get("els") != els:
        _QCACHE.update({"els": els, "ts": 0.0, "items": {}})
    return dict(_QCACHE.get("items") or {})


def _quality_run(status, names):
    """逐个指标计算（每次只算一个，避免 Mechanical 内存爆掉）"""
    els = status.get("elements") or 0
    if _QCACHE.get("els") != els:
        _QCACHE.update({"els": els, "ts": 0.0, "items": {}})
    fresh = []
    for nm in names:
        if nm in (_QCACHE.get("items") or {}):
            continue
        res = call_bridge("quality_one", {"name": nm}, timeout=300)
        if res.get("ok"):
            _QCACHE["items"][nm] = res
            fresh.append(nm)
        else:
            _QCACHE["items"][nm] = {"name": nm, "error": res.get("error")}
        time.sleep(1.5)
    _QCACHE["ts"] = time.time()
    return {"ok": True, "computed": fresh, "items": _QCACHE.get("items") or {}}


# 逐体判定的指标与阈值：(指标, 取值方向, 合格上限/下限, 超限阈值, 建议)
# 判定按"平均值"看整块好坏（用最差值会把几乎所有体都判成不合格）：
#   Element Quality 平均 < 0.2 差、< 0.45 偏差
#   Aspect Ratio 平均 > 8 偏差、> 15 差
#   Skewness 平均 > 0.75 偏差、> 0.90 差
BODY_METRIC_RULES = [
    (u"Element Quality", "avg_min", 0.45, 0.20,
     u"整块单元质量差：该组尺寸偏大导致畸变，降到 2.5×壁厚；规整件改扫掠+Automatic Thin"),
    (u"Aspect Ratio", "avg_max", 8.0, 15.0,
     u"单元普遍细长：放宽尺寸或改扫掠，让长方向均匀分段"),
    (u"Skewness", "avg_max", 0.75, 0.90,
     u"单元普遍歪斜：该处需局部加密（Face Sizing）或改善几何过渡"),
]

_QBODY = {"els": -1, "ts": 0.0, "data": None}


def _body_judge(rule, stats):
    """按平均值判定；返回 (等级, 说明, 用于排序的键值)"""
    mode = rule[1]
    key_name = "avg"
    v = stats.get(key_name)
    if v is None:
        return "muted", u"无数据", 0.0
    good, bad = rule[2], rule[3]
    if mode.startswith("avg_min"):
        if v < bad:
            return "bad", u"差", v
        if v < good:
            return "warn", u"偏差", v
        return "ok", u"合格", v
    else:
        if v > bad:
            return "bad", u"差", v
        if v > good:
            return "warn", u"偏差", v
        return "ok", u"合格", v


_HQ = {"els": -1, "ts": 0.0, "data": None}


def hole_quality(st, force=False):
    """只对"圆孔"标签的类别做质量分析（全自动，范围小）"""
    els = st.get("elements") or 0
    now = time.time()
    if (not force and _HQ.get("data") is not None
            and _HQ.get("els") == els and now - _HQ.get("ts", 0) < 600):
        return _HQ["data"]

    cfg = load_config()
    hole_cats = cfg.get("hole_cats") or []
    data = {"cats": hole_cats, "metric": "Element Quality",
            "bodies": [], "offenders": [], "ok_count": 0, "total": 0,
            "error": None}
    if not hole_cats:
        data["error"] = "还没有类别打上「圆孔」标签（该标签需在类别行手动点「+圆孔」，程序不会自动判断）"
        _HQ.update({"els": els, "ts": now, "data": data})
        return data

    plan = call_bridge("read_plan", timeout=60)
    if not plan.get("ok"):
        data["error"] = plan.get("error") or "读不到控制项"
        return data

    want = set()
    for c in plan.get("controls") or []:
        nm = c.get("name") or ""
        if nm.startswith("METHOD_"):
            continue
        g = nm
        for pre in ("SIZE_FIX_", "SIZE_", "SWEEP_", "SHELL_"):
            if g.startswith(pre):
                g = g[len(pre):]
                break
        if g in hole_cats:
            for b in (c.get("bodies") or []):
                want.add(b)
    if not want:
        data["error"] = "圆孔类别的体没找到（可能标签的类别名与控制项不一致）"
        _HQ.update({"els": els, "ts": now, "data": data})
        return data

    res = call_bridge("quality_for_bodies",
                      {"metric": "Element Quality", "names": sorted(want)},
                      timeout=600)
    if not res.get("ok"):
        data["error"] = res.get("error") or "质量计算失败"
        return data

    rule = BODY_METRIC_RULES[0]
    rows = res.get("bodies") or []
    off = []
    ok_n = 0
    for r in rows:
        lvl, verdict, _sv = _body_judge(rule, r)
        item = {"name": r.get("name"), "min": r.get("min"),
                "max": r.get("max"), "avg": r.get("avg"),
                "elements": r.get("elements") or 0,
                "level": lvl, "verdict": verdict}
        if lvl in ("bad", "warn"):
            off.append(item)
        else:
            ok_n += 1
    off.sort(key=lambda d: (d["level"] != "bad", d["avg"] or 0))

    data.update({"bodies": rows, "offenders": off, "ok_count": ok_n,
                 "total": len(rows), "rule": rule[4]})
    _HQ.update({"els": els, "ts": now, "data": data})
    return data


def quality_bodies(status, force=False):
    """只读当前已算好的指标（不切换 MeshMetric，避免重算导致闪退）"""
    els = status.get("elements") or 0
    now = time.time()
    if (not force and _QBODY.get("data") is not None
            and _QBODY.get("els") == els and now - _QBODY.get("ts", 0) < 600):
        return _QBODY["data"]

    # 只分析高风险区域（几何风险前 N 个），大幅减少读取量与内存压力
    focus = []
    try:
        focus = [r["name"] for r in body_risk_rows(24)]
    except Exception:
        focus = []
    res = call_bridge("read_body_quality", {"names": focus} if focus else {},
                      timeout=300)
    if not res.get("ok"):
        return {"error": res.get("error")}

    metric = res.get("metric") or u"?"
    rows = res.get("bodies") or []
    # 按指标名找规则；找不到就用 Element Quality 的规则（但只做展示）
    rule = None
    for r in BODY_METRIC_RULES:
        if r[0].replace(" ", "").lower() == metric.replace(" ", "").lower():
            rule = r
            break
    if rule is None:
        rule = BODY_METRIC_RULES[0]

    off, ok_n = [], 0
    for r in rows:
        if r.get("min") is None:
            continue
        lvl, verdict, _sv = _body_judge(rule, r)
        if lvl in ("bad", "warn"):
            off.append({"name": r.get("name"), "min": r.get("min"),
                        "max": r.get("max"), "avg": r.get("avg"),
                        "elements": r.get("elements") or 0,
                        "level": lvl, "verdict": verdict})
        else:
            ok_n += 1
    off.sort(key=lambda d: (d["level"] != "bad",
                            d["avg"] if rule[1].startswith("avg_min") else -d["avg"]))

    data = {"current_metric": metric, "focus_count": len(focus),
            "total_bodies": len(rows),
            "metric": {"offenders": off, "ok": ok_n, "total": len(rows),
                       "rule": rule[4], "dir": rule[1]}}
    _QBODY.update({"els": els, "ts": now, "data": data})
    return data


_PROJ = {"ts": 0.0, "data": None}


def safe_save(tag=""):
    """保存工程：先试代理接口，失败再用 UI 自动化按 Ctrl+S"""
    r1 = {}
    try:
        r1 = call_bridge("save_project", timeout=120)
        if r1.get("ok"):
            r1["tag"] = tag
            r1["via"] = "agent"
            return r1
    except Exception as e:
        r1 = {"ok": False, "error": str(e)[:120]}

    # UI 自动化保存
    try:
        ps = os.path.join(APP_DIR, "tools", "save_mech.ps1")
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", ps]
        out = subprocess.run(cmd, capture_output=True, timeout=90,
                             creationflags=0x08000000)
        txt = (out.stdout or b"").decode("mbcs", "replace").strip()
        try:
            r2 = json.loads(txt.splitlines()[-1]) if txt else {"ok": False}
        except Exception:
            r2 = {"ok": False, "raw": txt[:200]}
        r2["tag"] = tag
        r2["via"] = "ui"
        r2["agent_error"] = (r1.get("error") or "")[:120]
        return r2
    except Exception as e:
        return {"ok": False, "tag": tag, "via": "ui",
                "error": str(e)[:160]}

def project_file(force=False):
    """读取当前 .wbpj 工程路径（缓存 60 秒）"""
    now = time.time()
    if not force and _PROJ.get("data") and now - _PROJ.get("ts", 0) < 60:
        return _PROJ["data"]
    data = {"ok": False, "name": None, "path": None}
    try:
        cmd = [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'AnsysFWW.exe' "
            "-or $_.Name -eq 'RunWB2.exe' } | Select-Object -ExpandProperty CommandLine)"
        ]
        out = subprocess.run(cmd, capture_output=True, timeout=15,
                             creationflags=0x08000000)   # CREATE_NO_WINDOW
        text = (out.stdout or b"").decode("mbcs", "replace")
        best = None
        for line in text.splitlines():
            m = re.search(r'([A-Za-z]:\\[^"]*?\.wbpj)', line, re.I) or \
                re.search(r'([A-Za-z]:[^"]*?\.wbpj)', line, re.I)
            if m:
                best = m.group(1).strip().strip('"')
                break
        if best:
            data["path"] = best
            base = best.replace("/", "\\").split("\\")[-1]
            data["name"] = base[:-5] if base.lower().endswith(".wbpj") else base
            data["ok"] = True
    except Exception as e:
        data["error"] = str(e)
    _PROJ.update({"ts": now, "data": data})
    return data


def detect_tank(body: dict) -> bool:
    """判断是否是"集装箱内的大立方体结构"= 储罐"""
    try:
        dims = sorted([body.get("lx") or 0, body.get("ly") or 0, body.get("lz") or 0])
        if dims[0] < 1500:
            return False
        if dims[0] <= 0:
            return False
        if (dims[2] / dims[0]) > 2.5:
            return False
        bbox = dims[0] * dims[1] * dims[2]
        vol = body.get("vol") or 0
        fill = (vol / bbox) if bbox else 0
        return fill >= 0.5
    except Exception:
        return False


def body_suppressed(b):
    """体是否被抑制"""
    try:
        return bool(b.get("suppressed"))
    except Exception:
        return False


def _is_wave(name):
    return ("波纹" in name) or ("WAVE" in name.upper())


def _is_bottom_plate(b, name):
    """大平面底板：扁平、面积大"""
    try:
        d = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
        flat = d[0] < 60 and d[1] > 400 and d[2] > 400
        return flat or ("底板" in name)
    except Exception:
        return "底板" in name


def _bodies_once():
    """取一次体列表（含 id 与 name）"""
    try:
        r = call_bridge("list_bodies", timeout=300)
        if r.get("ok"):
            return r.get("bodies") or []
    except Exception:
        pass
    return []


def migrate_overrides_per_model():
    """旧全局覆盖表 → 按模型拆分（只保留能解析的体 Id/体名）"""
    legacy = os.path.join(APP_DIR, "group_overrides.json")
    if not os.path.isfile(legacy):
        return {"ok": True, "note": "没有旧文件"}
    try:
        with open(legacy, encoding="utf-8") as f:
            old = json.load(f) or {}
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}
    if not old:
        return {"ok": True, "note": "旧文件为空"}

    report = []
    for a in _agents_raw():
        pid = int(a.get("pid"))
        try:
            name, path = _project_of_pid(pid)
        except Exception:
            name, path = (a.get("project"), a.get("project"))
        base = (name or "").strip()
        if base.lower().endswith(".wbpj"):
            base = base[:-5]
        key = "".join(c if (c.isalnum() or c in "-_") else "_" for c in base)[:60]
        if not key:
            continue
        try:
            lb = call_bridge_pid(pid, "list_bodies", {}, timeout=40)
        except Exception:
            continue
        if not lb.get("ok"):
            report.append({"model": key, "error": "读不到体"})
            continue
        id2name = {}
        names = set()
        for b in (lb.get("bodies") or []):
            if b.get("id") is not None:
                id2name[str(b["id"])] = b.get("name")
            if b.get("name"):
                names.add(b["name"])
        keep = {}
        dropped = 0
        for k, v in old.items():
            if str(k) in id2name:
                keep[str(k)] = v
            elif str(k) in names:
                keep[str(k)] = v
            else:
                dropped += 1
        outp = os.path.join(APP_DIR, "group_overrides__%s.json" % key)
        try:
            with open(outp, "w", encoding="utf-8") as f:
                json.dump(keep, f, ensure_ascii=False, indent=1)
        except Exception as e:
            report.append({"model": key, "error": str(e)[:60]})
            continue
        report.append({"model": key, "kept": len(keep), "dropped": dropped})
    try:
        os.rename(legacy, legacy + ".bak")
    except Exception:
        pass
    return {"ok": True, "report": report, "legacy": "已改名为 .bak"}


def _model_key():
    """当前目标模型的短标识（工程文件名，去掉扩展名）"""
    try:
        tp = target_pid()
        if tp:
            name, path = _project_of_pid(tp)
            base = name or (os.path.basename(path or "") if path else "")
            base = (base or "").strip()
            if base.lower().endswith(".wbpj"):
                base = base[:-5]
            if base:
                return "".join(c if (c.isalnum() or c in "-_") else "_"
                               for c in base)[:60]
    except Exception:
        pass
    return "default"


def _ovr_path(key=None):
    return os.path.join(APP_DIR, "group_overrides__%s.json" % (key or _model_key()))


def _ovr_load():
    """读当前模型的覆盖表；没有则回退旧全局文件"""
    for p in (_ovr_path(), os.path.join(APP_DIR, "group_overrides.json")):
        try:
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as f:
                    return json.load(f) or {}, p
        except Exception:
            continue
    return {}, None


def _ovr_save(data):
    """写当前模型的覆盖表"""
    try:
        with open(_ovr_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False


def merge_custom_groups(groups):
    """把人工分组叠加到识别结果上
       - 覆盖表键：体 Id（新）或体名（兼容旧数据）
       - 自定义分组即使为空也保留
    """
    try:
        with open(os.path.join(APP_DIR, "group_overrides.json"), encoding="utf-8") as f:
            ov = json.load(f) or {}
    except Exception:
        ov = {}
    try:
        with open(os.path.join(APP_DIR, "custom_groups.json"), encoding="utf-8") as f:
            cust = json.load(f) or []
    except Exception:
        cust = []

    groups = {k: list(v) for k, v in (groups or {}).items()}

    if ov:
        bodies = _bodies_once()
        id2name = {}
        for b in bodies:
            _bid = b.get("id")
            _bn = b.get("name")
            if _bid is not None and _bn:
                id2name[str(_bid)] = _bn

        where = {}
        for g, ns in groups.items():
            for n in ns:
                where[n] = g

        _names_set = set(id2name.values())
        _stale = 0
        for key, target in ov.items():
            n = id2name.get(str(key))
            if not n:
                # 兼容旧数据：键本身就是当前模型的体名才认
                if str(key) in _names_set:
                    n = str(key)
                else:
                    _stale += 1
                    continue
            if not n:
                continue
            cur = where.get(n)
            if cur == target:
                continue
            if cur and n in groups.get(cur, []):
                groups[cur] = [x for x in groups[cur] if x != n]
            groups.setdefault(target, [])
            if n not in groups[target]:
                groups[target].append(n)
            where[n] = target
        groups = {k: v for k, v in groups.items() if v}

    for c in cust:
        groups.setdefault(c, [])
    return groups

# ---- 网格消息归类规则（按 Mechanical 常见消息）----
_MESH_RULES = [
    (("jacobian",), "单元质量差（雅可比超限）",
     "该体存在退化单元：把该体尺寸减小 20~30%，或改用扫掠/六面体主导"),
    (("not be oriented correctly", "surfaces or surface boundaries"),
     "几何面朝向异常", "检查该体几何是否有翻转面/极小面；必要时用虚拟拓扑合并"),
    (("mesher failed to generate mesh",),
     "网格生成失败（尺寸或方法不当）",
     "先用该体单独尺寸调小；仍失败则改扫掠或 Automatic Thin"),
    (("surface meshing was successful", "tetrahedron meshing failed"),
     "面网格成功但体网格失败",
     "该体形状不适合四面体：改用扫掠（若为长构件）或加大去特征值"),
    (("target metrics", "problematic bodies"),
     "质量指标未达标", "放宽目标指标或减小该体尺寸重新划分"),
    (("minimum size",), "最小尺寸设置冲突",
     "最小尺寸必须 ≥ 去特征尺寸，否则网格静默失败"),
    (("defeature",), "去特征值相关", "去特征值过大可能删除重要特征；按体单独调整"),
    (("self intersecting", "self-intersecting"), "几何自相交",
     "导入几何存在自相交，需要先修复几何"),
    (("contact",), "接触相关", "检查接触面网格是否匹配"),
    (("zero", "degenerate"), "退化几何", "存在零厚度/退化面，需要清理几何"),
]


def analyze_mesh_messages():
    """生成网格后：读全部消息，归类 + 按体聚合 + 给意见"""
    msg = call_bridge("mesh_messages", timeout=120)
    if not msg.get("ok"):
        return {"ok": False, "error": msg.get("error") or "读不到消息"}
    items = msg.get("items") or []

    buckets = {}
    by_body = {}
    for it in items:
        text = (it.get("text") or "")
        low = text.lower()
        sev = (it.get("severity") or "")
        src = (it.get("source") or "")
        hit = None
        for keys, title, advice in _MESH_RULES:
            if any(k in low for k in keys):
                hit = (title, advice)
                break
        if hit is None:
            if sev.lower() == "error":
                hit = ("其他错误", "查看 Mechanicl 消息原文定位")
            elif "warning" in sev.lower():
                hit = ("其他警告", "一般可忽略，注意观察质量指标")
            else:
                continue
        title, advice = hit
        b = buckets.setdefault(title, {"issue": title, "count": 0,
                                       "bodies": [], "advice": advice})
        b["count"] += 1
        if src and src not in b["bodies"] and len(b["bodies"]) < 20:
            b["bodies"].append(src)
        if src:
            by_body.setdefault(src, []).append(title)

    issues = sorted(buckets.values(), key=lambda x: -x["count"])

    # 每个体的主要问题 + 建议动作
    body_advice = []
    for name, titles in by_body.items():
        uniq = []
        for t in titles:
            if t not in uniq:
                uniq.append(t)
        body_advice.append({"body": name, "issues": uniq[:4],
                            "action": _action_for(uniq)})
    body_advice.sort(key=lambda x: -len(x["issues"]))

    # 总体结论
    summary = []
    if not issues:
        if not items:
            summary.append("本次运行没有产生新的报错或警告。")
        else:
            summary.append("本次运行只有提示类消息，没有报错或警告。")
    else:
        errs = sum(1 for i in items if "error" in (i.get("severity") or "").lower())
        warns = sum(1 for i in items if "warning" in (i.get("severity") or "").lower())
        summary.append("本次运行共 %d 条新消息：错误 %d 条、警告 %d 条。"
                       % (len(items), errs, warns))
        if body_advice:
            summary.append("涉及 %d 个体，主要是「%s」。"
                           % (len(body_advice), issues[0]["issue"]))

    return {"ok": True, "messages": items[:60], "total": len(items),
            "since": msg.get("since"), "since_source": msg.get("since_source"),
            "raw_total": msg.get("raw_total"), "skipped_old": msg.get("skipped_old"),
            "issues": issues, "bodies": body_advice[:20],
            "summary": " ".join(summary)}


def _action_for(titles):
    """按问题组合给出下一步动作"""
    t = " ".join(titles)
    if "网格生成失败" in t or "体网格失败" in t:
        return "先减半尺寸单划；不行改扫掠"
    if "单元质量差" in t:
        return "减小 20~30% 尺寸或开扫掠"
    if "朝向异常" in t or "自相交" in t:
        return "清理几何 / 虚拟拓扑"
    if "质量指标未达标" in t:
        return "放宽指标或减小尺寸"
    if "最小尺寸" in t:
        return "提高最小尺寸或降低去特征值"
    return "查看消息原文"


MECH_CONNECT_CMD = (
    'import os;os.environ["MESHKIT_ROOT"]=r"%s";'
    'execfile(r"%s")'
) % (ROOT, os.path.join(APP_DIR, "bridge_agent.py").replace("\\", "/"))


_PROCS_CACHE = {"ts": 0, "data": []}


def _list_mech_procs(ttl=60.0):
    """所有正在运行的 AnsysWBU 进程 + 其 Workbench 父进程里的工程路径（带缓存）"""
    now = time.time()
    if _PROCS_CACHE["data"] and now - _PROCS_CACHE["ts"] < ttl:
        return list(_PROCS_CACHE["data"])
    try:
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='AnsysWBU.exe'\" | "
              "ForEach-Object { $p=$_; $q=Get-CimInstance Win32_Process "
              "-Filter \"ProcessId=$($p.ParentProcessId)\" -ErrorAction SilentlyContinue; "
              "\"$($p.ProcessId)|$($q.CommandLine)\" }")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, timeout=30, creationflags=0x08000000)
        text = (r.stdout or b"").decode("mbcs", "ignore")
    except Exception:
        return []
    out = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        pid_s, cmd = line.split("|", 1)
        pid_s = pid_s.strip()
        if not pid_s.isdigit():
            continue
        proj = None
        m = cmd.find(".wbpj")
        if m > 0:
            start = cmd.rfind('"', 0, m)
            proj = cmd[start + 1:m + 5] if start >= 0 else None
            if proj:
                proj = os.path.basename(proj)[:-5]
        out.append({"pid": int(pid_s), "project": proj})
    _PROCS_CACHE["ts"] = time.time()
    _PROCS_CACHE["data"] = list(out)
    return out


def _prefetch_procs():
    try:
        _list_mech_procs(ttl=0)
    except Exception:
        pass


try:
    import threading as _th
    _th.Thread(target=_prefetch_procs, daemon=True).start()
except Exception:
    pass


def instances_list():
    """所有 Mechanical 实例（含未连接）+ 连接状态"""
    raw = _agents_raw() if "_agents_raw" in globals() else []
    reg = {}
    for a in raw:
        pid = a.get("pid")
        try:
            pid = int(pid)
        except Exception:
            continue
        if _pid_alive(pid):
            reg[pid] = a
    tgt = target_pid()
    out = []
    for proc in _list_mech_procs():
        pid = proc["pid"]
        a = reg.get(pid)
        out.append({
            "pid": pid,
            "project": proc.get("project") or (a or {}).get("project"),
            "connected": bool(a),
            "version": (a or {}).get("version"),
            "busy": bool((a or {}).get("busy")),
            "target": (pid == tgt),
        })
    alive_pids = set(x["pid"] for x in out)
    if out and tgt not in alive_pids:
        # 选中的实例已关闭：切到第一个（优先已连接）
        pick = next((x["pid"] for x in out if x["connected"]), out[0]["pid"])
        try:
            cfg = load_config()
            cfg["target_pid"] = pick
            save_config(cfg)
            tgt = pick
            for x in out:
                x["target"] = (x["pid"] == pick)
        except Exception:
            pass
    out.sort(key=lambda x: (not x["connected"], x["pid"]))
    _fg = _foreground_mech_pid()
    for x in out:
        x["foreground"] = (x["pid"] == _fg)
        try:
            x["screen"] = window_screen(x["pid"]).get("monitor")
        except Exception:
            x["screen"] = None
        if x.get("connected"):
            try:
                x.update(instance_summary(x["pid"]))
            except Exception:
                pass
    return {"ok": True, "instances": out,
            "connected_count": sum(1 for x in out if x["connected"]),
            "target": tgt, "foreground": _fg,
            "connect_cmd": MECH_CONNECT_CMD}


def _scripting_pane_open(pid):
    """UIA 检查该窗口是否已有 Scripting 面板（ACTConsole）"""
    try:
        ps = (
            "Add-Type -AssemblyName UIAutomationClient;"
            "Add-Type -AssemblyName UIAutomationTypes;"
            "$AE=[System.Windows.Automation.AutomationElement];"
            "$root=$AE::RootElement;"
            "$all=$root.FindAll([System.Windows.Automation.TreeScope]::Children,"
            "[System.Windows.Automation.Condition]::TrueCondition);"
            "foreach($w in $all){ if($w.Current.ProcessId -eq %d){"
            " $kids=$w.FindAll([System.Windows.Automation.TreeScope]::Descendants,"
            "[System.Windows.Automation.Condition]::TrueCondition);"
            " $hit=0;"
            " foreach($k in $kids){ if($k.Current.Name -match 'ACTConsole|Mechanical Scripting'){$hit++} };"
            " if($hit -gt 0){'yes'}else{'no'}; break } }"
        ) % int(pid)
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, timeout=25, creationflags=0x08000000)
        out = (r.stdout or b"").decode("mbcs", "ignore").strip().lower()
        return "yes" in out
    except Exception:
        return False


SCRIPT_STATE = os.path.join(os.environ.get("APPDATA", ""),
                            r"Ansys\v221\ACT\preferences",
                            "ScriptStateMechanical_NewScripting.json")

CONNECT_CODE = 'execfile("D:/ai/meshkit/app/bridge_agent.py")'


def _ensure_script_state():
    """把加载命令写进 Mechanical 的 Scripting 脚本状态文件
       这样新开的 Mechanical 打开 Scripting 时编辑器里就有这条命令
    """
    try:
        if not os.path.isfile(SCRIPT_STATE):
            return False
        with open(SCRIPT_STATE, "r", encoding="utf-8") as f:
            data = json.load(f)
        user = data.setdefault("User", {})
        # 找到任意一个已有条目，或新建
        key = None
        for k, v in user.items():
            if isinstance(v, dict):
                key = k
                break
        if key is None:
            key = "New Script  1"
            user[key] = {"Name": key, "Code": "", "Description": "",
                         "FilePath": "", "Edited": False,
                         "BreakPoints": [], "OnDisk": False}
        item = user[key]
        code = (item.get("Code") or "").strip()
        if "bridge_agent" in code:
            return True
        item["Code"] = CONNECT_CODE
        item["Edited"] = False
        with open(SCRIPT_STATE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return True
    except Exception:
        return False


def _scripting_pane_rect(pid):
    """UIA 取 Scripting 面板矩形（物理像素）"""
    try:
        ps = (
            "Add-Type -AssemblyName UIAutomationClient;"
            "Add-Type -AssemblyName UIAutomationTypes;"
            "$AE=[System.Windows.Automation.AutomationElement];"
            "$root=$AE::RootElement;"
            "$all=$root.FindAll([System.Windows.Automation.TreeScope]::Children,"
            "[System.Windows.Automation.Condition]::TrueCondition);"
            "foreach($w in $all){ if($w.Current.ProcessId -eq %d){"
            " $kids=$w.FindAll([System.Windows.Automation.TreeScope]::Descendants,"
            "[System.Windows.Automation.Condition]::TrueCondition);"
            " foreach($k in $kids){ if($k.Current.Name -match 'Mechanical Scripting'){"
            "  $r=$k.Current.BoundingRectangle;"
            "  Write-Output ('R|' + [int]$r.X + '|' + [int]$r.Y + '|' + [int]$r.Width + '|' + [int]$r.Height);"
            "  break } } } }"
        ) % int(pid)
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, timeout=25, creationflags=0x08000000)
        out = (r.stdout or b"").decode("mbcs", "ignore")
        for line in out.splitlines():
            if line.startswith("R|"):
                parts = line.split("|")
                return tuple(int(x) for x in parts[1:5])
    except Exception:
        pass
    return None


MUTATING_OPS = {
    "apply", "mesh", "group_geometry", "ungroup_folders", "rename_bodies",
    "fix_body", "fix_failed", "quality_run", "set_local_defeature",
    "save_project", "op_apply", "regroup",
}


_INST_CACHE = {}


def call_bridge_pid(pid, op, payload=None, timeout=30):
    """直接向指定 pid 的代理下命令（不改当前目标）"""
    pid = int(pid)
    os.makedirs(IPC_DIR, exist_ok=True)
    seq = uuid.uuid4().hex[:12]
    cmd_path = os.path.join(IPC_DIR, "cmd_%d.json" % pid)
    res_path = os.path.join(IPC_DIR, "result_%d.json" % pid)
    try:
        if os.path.isfile(res_path):
            os.remove(res_path)
    except Exception:
        pass
    try:
        with open(cmd_path, "w", encoding="utf-8") as f:
            json.dump({"seq": seq, "op": op, "payload": payload or {}}, f)
    except Exception as e:
        return {"ok": False, "error": "写命令失败: %s" % str(e)[:80]}
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.25)
        try:
            with open(res_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if data.get("seq") == seq:
            return data
    return {"ok": False, "error": "超时（%ds 无回应）" % timeout}


_INST_BUSY = {}


def _refresh_summary(pid):
    """后台线程：真正去问 Mechanical"""
    pid = int(pid)
    try:
        info = {"bodies": None, "groups": None, "ungrouped": None}
        lb = call_bridge_pid(pid, "list_bodies", {}, timeout=20)
        if lb.get("ok"):
            info["bodies"] = len(lb.get("bodies") or [])
        gg = call_bridge_pid(pid, "geom_groups", {}, timeout=20)
        if gg.get("ok"):
            info["groups"] = len(gg.get("groups") or [])
            info["ungrouped"] = len(gg.get("ungrouped") or [])
        if info["bodies"] is not None or info["groups"] is not None:
            _INST_CACHE[pid] = (time.time(), info)
    except Exception:
        pass
    finally:
        _INST_BUSY[pid] = False


def instance_summary(pid, ttl=30):
    """立即返回缓存；缓存过期则后台刷新（不阻塞页面）"""
    pid = int(pid)
    now = time.time()
    c = _INST_CACHE.get(pid)
    if c and now - c[0] < ttl:
        return c[1]
    if not _INST_BUSY.get(pid):
        _INST_BUSY[pid] = True
        try:
            import threading
            threading.Thread(target=_refresh_summary, args=(pid,),
                             daemon=True).start()
        except Exception:
            _INST_BUSY[pid] = False
    if c:
        return c[1]
    return {"bodies": None, "groups": None, "ungrouped": None}


def _monitors():
    """所有显示器矩形：[{index,x,y,w,h,primary}]（物理像素）"""
    out = []
    try:
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                        ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

        proc = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HMONITOR, wt.HDC,
                                  ctypes.POINTER(wt.RECT), wt.LPARAM)

        def cb(hmon, hdc, lprc, lp):
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                r = mi.rcMonitor
                out.append({"x": r.left, "y": r.top,
                            "w": r.right - r.left, "h": r.bottom - r.top,
                            "primary": bool(mi.dwFlags & 1)})
            return 1

        user32.EnumDisplayMonitors(0, 0, proc(cb), 0)
    except Exception:
        pass
    # 先主屏，再按 x 排副屏
    out.sort(key=lambda m: (0 if m.get("primary") else 1, m["x"]))
    for i, m in enumerate(out):
        m["index"] = i + 1
        m["label"] = "主屏" if m.get("primary") else ("副屏%d" % i)
    return out


def _window_rect(pid):
    try:
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        res = []

        class RECT(ctypes.Structure):
            _fields_ = [("L", ctypes.c_long), ("T", ctypes.c_long),
                        ("R", ctypes.c_long), ("B", ctypes.c_long)]

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb(h, l):
            p = wt.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value == int(pid) and user32.IsWindowVisible(h):
                r = RECT()
                user32.GetWindowRect(h, ctypes.byref(r))
                if r.R - r.L > 600 and r.B - r.T > 400:
                    res.append((r.L, r.T, r.R - r.L, r.B - r.T))
            return True

        user32.EnumWindows(cb, 0)
        if not res:
            return None
        return max(res, key=lambda x: x[2] * x[3])
    except Exception:
        return None


def window_screen(pid):
    """目标窗口在哪块屏幕：{monitor:{...}, rect:[x,y,w,h], offscreen:bool}"""
    rect = _window_rect(pid)
    mons = _monitors()
    if not rect:
        return {"monitor": None, "rect": None, "offscreen": False,
                "monitors": mons}
    x, y, w, h = rect
    cx, cy = x + w // 2, y + h // 2
    hit = None
    for m in mons:
        if m["x"] <= cx < m["x"] + m["w"] and m["y"] <= cy < m["y"] + m["h"]:
            hit = m
            break
    return {"monitor": hit, "rect": list(rect),
            "offscreen": hit is None, "monitors": mons}


def _ensure_window_visible(pid):
    """窗口整块在屏幕外（例如副屏被拔掉）时，挪回主屏"""
    info = window_screen(pid)
    if not info.get("offscreen"):
        return False
    mons = info.get("monitors") or []
    prim = next((m for m in mons if m.get("primary")), mons[0] if mons else None)
    rect = info.get("rect")
    if not prim or not rect:
        return False
    try:
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        res = []

        class RECT(ctypes.Structure):
            _fields_ = [("L", ctypes.c_long), ("T", ctypes.c_long),
                        ("R", ctypes.c_long), ("B", ctypes.c_long)]

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb(h, l):
            p = wt.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value == int(pid) and user32.IsWindowVisible(h):
                r = RECT()
                user32.GetWindowRect(h, ctypes.byref(r))
                if r.R - r.L > 600 and r.B - r.T > 400:
                    res.append(h)
            return True

        user32.EnumWindows(cb, 0)
        if not res:
            return False
        user32.SetWindowPos(res[0], 0, prim["x"] + 40, prim["y"] + 40,
                            0, 0, 0x0001 | 0x0004 | 0x0010)
        time.sleep(0.3)
        return True
    except Exception:
        return False


def _foreground_mech_pid():
    """当前前台窗口如果是 Mechanical 进程，返回其 pid，否则 None
       （按进程判定，未注册代理的 Mechanical 也算）
    """
    try:
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        h = user32.GetForegroundWindow()
        if not h:
            return None
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        p = int(pid.value)
        # 先看已注册代理
        for a in _agents_raw():
            if int(a.get("pid") or 0) == p:
                return p
        # 再看所有 Mechanical 进程（覆盖未注册代理的窗口）
        try:
            for item in _list_mech_procs():
                if int(item.get("pid") or 0) == p:
                    return p
        except Exception:
            pass
        return None
    except Exception:
        return None


def ensure_target_front(strict=True):
    """确保目标实例在前台
       返回 dict: {ok, switched, front_before, front_after}
       strict=True 且最终不在前台 → ok=False（调用方应拒绝执行）
    """
    tgt = target_pid()
    if not tgt:
        return {"ok": False, "switched": False, "front_before": None,
                "front_after": None, "error": "没有可用的目标实例"}
    front = _foreground_mech_pid()
    if front == tgt:
        return {"ok": True, "switched": False, "front_before": front,
                "front_after": front}
    switched = False
    try:
        if _activate_window(int(tgt)) == "ok":
            switched = True
    except Exception:
        pass
    time.sleep(0.35)
    after = _foreground_mech_pid()
    if after == tgt:
        return {"ok": True, "switched": switched, "front_before": front,
                "front_after": after}
    if not strict:
        return {"ok": True, "switched": switched, "front_before": front,
                "front_after": after}
    return {"ok": False, "switched": switched, "front_before": front,
            "front_after": after,
            "error": "目标实例不在前台（前台是 %s），已阻止本次写操作"
                     % (after if after else "其它窗口")}


_CACHE = {}
_CACHE_BUSY = {}


def cache_get(key, ttl, producer, block=False):
    """缓存读取；过期则后台刷新，先返回旧值；没有旧值时返回 loading"""
    now = time.time()
    hit = _CACHE.get(key)

    def _tag(data, refreshing=False):
        if isinstance(data, dict):
            data = dict(data)
            if hit:
                data["cached"] = True
                data["cache_age"] = round(now - hit[0], 1)
            if refreshing:
                data["refreshing"] = True
        return data

    if hit and now - hit[0] < ttl:
        return _tag(hit[1])

    def _spawn():
        _CACHE_BUSY[key] = True

        def _bg():
            try:
                val = producer()
                if isinstance(val, dict) and val.get("ok"):
                    _CACHE[key] = (time.time(), val)
            except Exception:
                pass
            finally:
                _CACHE_BUSY[key] = False

        try:
            import threading
            threading.Thread(target=_bg, daemon=True).start()
        except Exception:
            _CACHE_BUSY[key] = False

    if hit:
        if not _CACHE_BUSY.get(key):
            _spawn()
        return _tag(hit[1], refreshing=True)

    if not block:
        if not _CACHE_BUSY.get(key):
            _spawn()
        return {"ok": False, "loading": True,
                "error": "首次读取中（Mechanical 较慢），几秒后自动刷新"}

    val = producer()
    if isinstance(val, dict) and val.get("ok"):
        # 控制项还没读到 -> 不缓存，否则"全灰"会停留 60 秒
        if not (isinstance(val, dict) and val.get("_ctrl_known") is False):
            _CACHE[key] = (time.time(), val)
    return val


def cache_invalidate(prefix=""):
    for k in list(_CACHE.keys()):
        if not prefix or str(k).startswith(str(prefix)):
            _CACHE.pop(k, None)
    if not prefix:
        _CTRL_CACHE.clear()


def _warm_categories():
    """同步重建类别缓存（改动标记后调用，保证页面下一次读到新结果）"""
    try:
        tp = target_pid()
        val = categories_from_recognition(fast=True)
        if isinstance(val, dict) and val.get("ok"):
            _CACHE[("categories", tp)] = (time.time(), val)
        return True
    except Exception:
        return False


def _warm_cache():
    """后台预热：连上就先把重的数据算好"""
    try:
        _tp = target_pid()
        if not _tp:
            return
        for key, ttl, fn in (
            (("status", _tp), 30, lambda: call_bridge("status", timeout=180)),
            (("categories", _tp), 60,
             lambda: categories_from_recognition(fast=True)),
            (("body_counts", _tp), 90, lambda: call_bridge("body_counts", timeout=180)),
        ):
            try:
                if key not in _CACHE:
                    val = fn()
                    if isinstance(val, dict) and val.get("ok"):
                        _CACHE[key] = (time.time(), val)
            except Exception:
                pass
    except Exception:
        pass


_CTRL_CACHE = {}
_CTRL_BUSY = {}


def ctrl_names_cached(pid=None, ttl=300, wait=False):
    """Mesh 控制项名字缓存；wait=True 时同步读一次（约 0.8 秒）"""
    pid = pid or target_pid()
    now = time.time()
    hit = _CTRL_CACHE.get(pid)
    if hit and now - hit[0] < ttl:
        return list(hit[1]), round(now - hit[0], 1)

    if wait:
        try:
            rp = call_bridge("read_plan", timeout=120)
            items = []
            for c in (rp.get("controls") or []):
                nm = (c.get("name") or "").strip()
                if not nm:
                    continue
                for pre in ("SIZE_FIX_", "SIZE_", "METHOD_", "SWEEP_", "SHELL_"):
                    if nm.startswith(pre):
                        nm = nm[len(pre):]
                        break
                items.append({"name": nm,
                              "size": c.get("size_mm"),
                              "method": c.get("method"),
                              "bodies": c.get("count")})
            if rp.get("ok"):
                _CTRL_CACHE[pid] = (time.time(), items)
                _CTRL_BUSY[pid] = False
                return items, 0.0
        except Exception:
            pass

    def _spawn():
        _CTRL_BUSY[pid] = True

        def _bg():
            try:
                rp = call_bridge("read_plan", timeout=120)
                items = []
                for c in (rp.get("controls") or []):
                    nm = (c.get("name") or "").strip()
                    if not nm:
                        continue
                    for pre in ("SIZE_FIX_", "SIZE_", "METHOD_", "SWEEP_", "SHELL_"):
                        if nm.startswith(pre):
                            nm = nm[len(pre):]
                            break
                    items.append({"name": nm,
                                  "size": c.get("size_mm"),
                                  "method": c.get("method"),
                                  "bodies": c.get("count")})
                if rp.get("ok"):
                    _CTRL_CACHE[pid] = (time.time(), items)
            except Exception:
                pass
            finally:
                _CTRL_BUSY[pid] = False

        try:
            import threading
            threading.Thread(target=_bg, daemon=True).start()
        except Exception:
            _CTRL_BUSY[pid] = False

    if hit:
        if not _CTRL_BUSY.get(pid):
            _spawn()
        return list(hit[1]), round(now - hit[0], 1)

    if not _CTRL_BUSY.get(pid):
        _spawn()
    return None, None


def premesh_check():
    """网格前预估：单元/节点数 + 是否超预算"""
    try:
        lb = call_bridge("list_bodies", timeout=300)
        if not lb.get("ok"):
            return {"ok": False, "error": lb.get("error") or "读不到体"}
        bodies = lb.get("bodies") or []
        cats = categories_from_recognition(fast=True)
        groups = {}
        for c in (cats.get("categories") or []):
            groups[c.get("name")] = c.get("body_names") or []
        cfg = load_config()
        sizes = cfg.get("sizes") or {}
        sset = cfg.get("settings") or {}
        global_size = float(sset.get("ElementSize") or 40.0)
        budget_nodes = int(cfg.get("budget_nodes") or 7000000)
        bmap = {b.get("name"): b for b in bodies}

        detail = []
        est_total = 0.0
        for name, names in groups.items():
            if name in ("未分组", "其他"):
                continue
            size = sizes.get(name) or global_size
            try:
                size = float(size)
            except Exception:
                size = global_size
            if size <= 0:
                continue
            est = 0.0
            vol = 0.0
            for n in names:
                b = bmap.get(n) or {}
                try:
                    _v = float(b.get("vol") or 0)
                except Exception:
                    _v = 0.0
                vol += _v
                _d = sorted([float(b.get("lx") or 0), float(b.get("ly") or 0),
                             float(b.get("lz") or 0)])
                if _d[0] <= 0:
                    continue
                if _d[0] <= 12:
                    # 薄板：面积 × 厚度方向 2 层
                    est += (_d[1] * _d[2]) / (size * size) * 2.0
                elif _v > 0:
                    est += _v / (size ** 3)
                else:
                    est += (_d[1] * _d[2]) / (size * size) * (_d[0] / size)
            if est <= 0:
                continue
            est_total += est
            detail.append({"category": name, "bodies": len(names),
                           "size": size, "volume": round(vol, 1),
                           "est_elements": int(est)})
        detail.sort(key=lambda x: -x["est_elements"])
        est_nodes = int(est_total * 1.6)
        return {"ok": True,
                "est_elements": int(est_total),
                "est_nodes": est_nodes,
                "budget_nodes": budget_nodes,
                "budget_elements": int(cfg.get("budget_elements") or 1450000),
                "over": est_nodes > budget_nodes,
                "ratio": round(float(est_nodes) / budget_nodes, 2) if budget_nodes else 0,
                "top": detail[:8],
                "current_elements": cats.get("elements") or 0}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


def _ctrl_validation_fallback():
    """用 read_plan + 全局设置做校验（代理未重载时的兜底）"""
    try:
        rp = call_bridge("read_plan", timeout=120)
    except Exception:
        return {"ok": False, "error": "read_plan 失败"}
    if not rp.get("ok"):
        return rp
    cfg = load_config()
    st = cfg.get("settings") or {}
    g_min = float(st.get("MinimumSize") or 0)
    g_def = float(st.get("DefeatureTolerance") or 0)
    g_size = float(st.get("ElementSize") or 0)
    controls, fatal, warn = [], [], []
    for c in (rp.get("controls") or []):
        nm = (c.get("name") or "").strip()
        if not nm:
            continue
        kind = "sizing" if ("Sizing" in (c.get("category") or "")
                            or nm.startswith("SIZE_")) else "method"
        item = {"name": nm, "kind": kind, "bodies": c.get("count") or 0,
                "size_mm": c.get("size_mm"), "method": c.get("method"),
                "fatal": [], "warn": []}
        if not item["bodies"]:
            item["fatal"].append("作用域为空（控制项没有指定任何体）")
        if kind == "sizing":
            sz = item["size_mm"] or 0
            if not sz:
                item["fatal"].append("未设置尺寸（ElementSize 为空）")
            else:
                if g_min and sz < g_min:
                    item["warn"].append("尺寸 %.2f 小于全局最小尺寸 %.2f" % (sz, g_min))
                if g_def and sz < g_def:
                    item["warn"].append("尺寸 %.2f 小于去特征尺寸 %.2f" % (sz, g_def))
                if g_size and sz > g_size * 2.5:
                    item["warn"].append("尺寸 %.2f 明显大于全局 %.2f" % (sz, g_size))
        else:
            if not item["method"]:
                item["fatal"].append("方法类型未设置")
        item["problems"] = item["fatal"] + item["warn"]
        if item["fatal"]:
            fatal.append({"name": nm, "problems": item["fatal"]})
        elif item["warn"]:
            warn.append({"name": nm, "problems": item["warn"]})
        controls.append(item)
    return {"ok": True, "controls": controls, "fatal": fatal, "warn": warn,
            "settings": {"ElementSize": g_size, "MinimumSize": g_min,
                         "DefeatureTolerance": g_def},
            "source": "fallback"}


def _ctrl_validation():
    """调用代理校验已有控制项（缓存 30 秒）；代理不支持时用 read_plan 兜底"""
    tp = target_pid()
    cached = cache_get(("ctrlval", tp), 30,
                       lambda: call_bridge("validate_controls", timeout=120),
                       block=False)
    if isinstance(cached, dict) and cached.get("ok"):
        return cached
    # 代理还没有 validate_controls（未重载）→ 兜底
    try:
        return cache_get(("ctrlval_fb", tp), 30, _ctrl_validation_fallback)
    except Exception:
        return {"ok": False, "error": "控制项校验不可用"}


def _strip_ctrl_prefix(name):
    for pre in ("SIZE_FIX_", "SIZE_", "METHOD_", "SWEEP_", "SHELL_"):
        if name.startswith(pre):
            return name[len(pre):]
    return name


def _lights_from_controls(cats):
    """用控制项校验结果覆盖类别灯色；返回是否成功"""
    try:
        val = _ctrl_validation()
    except Exception:
        return False
    if not isinstance(val, dict) or not val.get("ok"):
        return False
    fatal, warn = {}, {}
    for item in (val.get("controls") or []):
        cat = _strip_ctrl_prefix(item.get("name") or "")
        if not cat:
            continue
        if item.get("fatal"):
            fatal.setdefault(cat, []).extend(item["fatal"])
        elif item.get("warn"):
            warn.setdefault(cat, []).extend(item["warn"])
    have = set()
    for item in (val.get("controls") or []):
        have.add(_strip_ctrl_prefix(item.get("name") or ""))

    for c in (cats or []):
        name = c.get("name")
        if c.get("is_all_suppressed"):
            c["light"], c["light_reason"] = "muted", "全部已抑制（不参与网格）"
            continue
        if name in fatal:
            c["light"] = "red"
            c["light_reason"] = "控制项问题：" + "；".join(fatal[name][:3])
        elif name in warn:
            c["light"] = "yellow"
            c["light_reason"] = "控制项告警：" + "；".join(warn[name][:3])
        elif name in have:
            c["light"] = "green"
            c["light_reason"] = "控制项正常"
        else:
            c["light"] = "red"
            c["light_reason"] = "Mesh 里没有该类别控制项（点「应用设置与尺寸」生成）"
    return True


def _flags_path(key=None):
    return os.path.join(APP_DIR, "model_flags__%s.json" % (key or _model_key()))


def _flags_load():
    """按模型读标记：thin_cats/thin_off/hole_cats/locked_cats/sweep_ok"""
    try:
        p = _flags_path()
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                d = json.load(f) or {}
            return d, True
    except Exception:
        pass
    return {}, False


def _flags_save(d):
    try:
        with open(_flags_path(), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False


def _flags_get(cfg, key):
    """优先按模型；没有记录时回退全局配置"""
    d, ok = _flags_load()
    if ok and key in d:
        return list(d.get(key) or [])
    return list(cfg.get(key) or [])


def _flags_set(key, value):
    d, _ = _flags_load()
    d[key] = list(value or [])
    return _flags_save(d)


def _mech_busy():
    """Mechanical 是否在跑长任务
       只看真正耗时的步骤（网格/修复），不看内部读操作，避免把读接口挡掉
    """
    try:
        d = _read_json(PROGRESS_PATH) or {}
        step = (d.get("step") or "").strip()
        op = (d.get("op") or "").strip().lower()
        ts = d.get("ts") or ""
        long_ops = ("mesh", "fix_failed", "group_geometry", "rename_bodies",
                    "ungroup_folders", "controls_from_plan", "apply")
        if op not in long_ops:
            return ""
        if not step or step in ("已结束", "结束", "完成", "开始", "idle"):
            return ""
        try:
            import datetime as _dt
            t0 = _dt.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            if (time.time() - t0.timestamp()) < 1800:
                return step
        except Exception:
            return step
    except Exception:
        pass
    return ""

def _mech_auto_connect(pid):
    """安全版：只激活/最大化/开面板/Ctrl+F5，不盲粘贴"""
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32

    class RECT(ctypes.Structure):
        _fields_ = [("L", ctypes.c_long), ("T", ctypes.c_long),
                    ("R", ctypes.c_long), ("B", ctypes.c_long)]

    def find_win():
        res = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb(h, l):
            p = wt.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value == int(pid) and user32.IsWindowVisible(h):
                r = RECT()
                user32.GetWindowRect(h, ctypes.byref(r))
                if r.R - r.L > 800 and r.B - r.T > 600:
                    res.append((h, r.L, r.T, r.R - r.L, r.B - r.T))
            return True

        user32.EnumWindows(cb, 0)
        return max(res, key=lambda x: x[3] * x[4]) if res else None

    def agent_file():
        return os.path.join(APP_DIR, "ipc", "agents", "%d.json" % int(pid))

    def registered():
        f = agent_file()
        if not os.path.isfile(f):
            return False
        try:
            return _pid_alive(int(pid))
        except Exception:
            return True

    win = find_win()
    if not win:
        return {"ok": False, "stage": "找不到窗口"}
    h, L, T, W, H = win

    # 1) 激活 + 最大化
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    time.sleep(0.12)
    cur = k32.GetCurrentThreadId()
    tgt = user32.GetWindowThreadProcessId(h, None)
    user32.AttachThreadInput(cur, tgt, True)
    user32.BringWindowToTop(h)
    user32.SetForegroundWindow(h)
    user32.AttachThreadInput(cur, tgt, False)
    time.sleep(0.35)
    try:
        user32.ShowWindow(h, 3)      # 最大化
        time.sleep(0.8)
    except Exception:
        pass

    # 2) 确保 Scripting 面板
    if not _scripting_pane_open(pid):
        _open_scripting_pane(pid)
        time.sleep(1.0)
        if not _scripting_pane_open(pid):
            _open_scripting_pane(pid)
            time.sleep(1.0)
    if not _scripting_pane_open(pid):
        return {"ok": False, "stage": "面板未打开",
                "error": "无法自动打开 Scripting 面板，请手动打开后按 Ctrl+F5"}

    # 2.5) 确保脚本状态里有加载命令（新开的 Mechanical 也能读到）
    try:
        _ensure_script_state()
    except Exception:
        pass

    # 3) Ctrl+F5 运行（编辑器里有上次的命令，Ctrl+F5 直接跑）
    for attempt in range(3):
        user32.keybd_event(0x11, 0, 0, 0)
        time.sleep(0.07)
        user32.keybd_event(0x74, 0, 0, 0)
        time.sleep(0.08)
        user32.keybd_event(0x74, 0, 2, 0)
        time.sleep(0.07)
        user32.keybd_event(0x11, 0, 2, 0)
        time.sleep(0.6)
        for _ in range(5):
            if registered():
                return {"ok": True, "stage": "已连接", "attempt": attempt + 1}
            time.sleep(1.0)

    # 3.5) 兜底：往编辑区粘贴一次命令再 Ctrl+F5
    rect = _scripting_pane_rect(pid)
    if rect:
        try:
            win = find_win()
            if win:
                h2, L2, T2, W2, H2 = win
                # UIA 矩形是物理像素，窗口矩形是逻辑像素 → 换算
                scale = (W2 / float(W2)) if W2 else 1.0
                px = rect[0] - _win_origin_phys_x(pid)
                py = rect[1] - _win_origin_phys_y(pid)
                # 点击面板内靠上位置（避开标题栏/工具条）
                cx = int(px / 2 + 80)
                cy = int(py / 2 + 120)
                user32.SetCursorPos(L2 + cx, T2 + cy)
                time.sleep(0.3)
                user32.mouse_event(0x0002, 0, 0, 0, 0)
                time.sleep(0.1)
                user32.mouse_event(0x0004, 0, 0, 0, 0)
                time.sleep(0.5)
                _set_clipboard(CONNECT_CODE)
                for mod, vk in ((0x11, 0x41), (0x11, 0x56)):
                    user32.keybd_event(mod, 0, 0, 0)
                    time.sleep(0.06)
                    user32.keybd_event(vk, 0, 0, 0)
                    time.sleep(0.06)
                    user32.keybd_event(vk, 0, 2, 0)
                    time.sleep(0.06)
                    user32.keybd_event(mod, 0, 2, 0)
                    time.sleep(0.3)
                user32.keybd_event(0x11, 0, 0, 0)
                time.sleep(0.06)
                user32.keybd_event(0x74, 0, 0, 0)
                time.sleep(0.08)
                user32.keybd_event(0x74, 0, 2, 0)
                time.sleep(0.06)
                user32.keybd_event(0x11, 0, 2, 0)
                for _ in range(8):
                    time.sleep(1.0)
                    if registered():
                        return {"ok": True, "stage": "粘贴后已连接"}
        except Exception:
            pass

    return {"ok": False, "stage": "运行后仍未连接",
            "error": "已自动激活并发送 Ctrl+F5，但代理仍未注册。"
                     "请在 Scripting 面板里点一下代码区，再按 Ctrl+F5"}

def _open_scripting_pane(pid):
    """点 Automation 标签下的 Scripting 按钮（窗口相对坐标 155,76）"""
    try:
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32

        class RECT(ctypes.Structure):
            _fields_ = [("L", ctypes.c_long), ("T", ctypes.c_long),
                        ("R", ctypes.c_long), ("B", ctypes.c_long)]

        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb(h, l):
            p = wt.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value == int(pid) and user32.IsWindowVisible(h):
                r = RECT()
                user32.GetWindowRect(h, ctypes.byref(r))
                if r.R - r.L > 800 and r.B - r.T > 600:
                    found.append((h, r.L, r.T))
            return True

        user32.EnumWindows(cb, 0)
        if not found:
            return "nowin"
        h, L, T = found[0]
        user32.SetForegroundWindow(h)
        time.sleep(0.3)
        user32.SetCursorPos(L + 155, T + 76)
        time.sleep(0.25)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.1)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(1.2)
        return "ok"
    except Exception as e:
        return "err:%s" % str(e)[:40]


def _activate_window(pid):
    """把指定 pid 的 Mechanical 窗口切到前台（AttachThreadInput 方式）"""
    try:
        _ensure_window_visible(pid)
    except Exception:
        pass
    try:
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32

        class RECT(ctypes.Structure):
            _fields_ = [("L", ctypes.c_long), ("T", ctypes.c_long),
                        ("R", ctypes.c_long), ("B", ctypes.c_long)]

        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb(h, l):
            p = wt.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value == int(pid) and user32.IsWindowVisible(h):
                r = RECT()
                user32.GetWindowRect(h, ctypes.byref(r))
                if r.R - r.L > 800 and r.B - r.T > 600:
                    found.append(h)
            return True

        user32.EnumWindows(cb, 0)
        if not found:
            return "nowin"
        h = found[0]
        # 1) 若最小化先恢复
        if user32.IsIconic(h):
            user32.ShowWindow(h, 9)
            time.sleep(0.25)
        # 2) 强制置顶（HWND_TOPMOST=-1, SWP 标志）
        user32.SetWindowPos(h, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
        # 3) AttachThreadInput 方式抢焦点
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        time.sleep(0.12)
        cur = k32.GetCurrentThreadId()
        tgt = user32.GetWindowThreadProcessId(h, None)
        user32.AttachThreadInput(cur, tgt, True)
        user32.BringWindowToTop(h)
        user32.SetForegroundWindow(h)
        user32.AttachThreadInput(cur, tgt, False)
        # 4) 任务栏闪烁提醒
        try:
            class FLASHWINFO(ctypes.Structure):
                _fields_ = [("cbSize", wt.UINT), ("hwnd", wt.HWND),
                            ("dwFlags", wt.DWORD), ("uCount", wt.UINT),
                            ("dwTimeout", wt.DWORD)]
            fi = FLASHWINFO(ctypes.sizeof(FLASHWINFO), h, 3 | 12, 6, 0)
            user32.FlashWindowEx(ctypes.byref(fi))
        except Exception:
            pass
        # 4.5) 兜底：SwitchToThisWindow（对某些前台锁有效）
        try:
            user32.SwitchToThisWindow(h, True)
            time.sleep(0.2)
        except Exception:
            pass
        time.sleep(0.4)
        # 5) 取消置顶，避免永远压着别的窗口
        try:
            user32.SetWindowPos(h, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
        except Exception:
            pass
        return "ok" if user32.GetForegroundWindow() == h else "no"
    except Exception as e:
        return "err:%s" % str(e)[:40]


def _activate_window_ps(pid):
    """旧的 powershell 兜底方式"""
    try:
        cmd = [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "$sig='[DllImport(\"user32.dll\")] public static extern bool "
            "SetForegroundWindow(IntPtr h);"
            "[DllImport(\"user32.dll\")] public static extern bool "
            "BringWindowToTop(IntPtr h);"
            "[DllImport(\"user32.dll\")] public static extern IntPtr "
            "GetForegroundWindow();';"
            "$t=Add-Type -MemberDefinition $sig -Name W -Namespace MK "
            "-PassThru;"
            "$p=Get-Process -Id %d -ErrorAction SilentlyContinue;"
            "if($p -and $p.MainWindowHandle -ne 0){"
            " $h=$p.MainWindowHandle;"
            " [MK.W]::BringWindowToTop($h) | Out-Null;"
            " [MK.W]::SetForegroundWindow($h) | Out-Null;"
            " Start-Sleep -Milliseconds 300;"
            " if([MK.W]::GetForegroundWindow() -eq $h){'ok'}else{'no'}"
            "} else {'nowin'}" % int(pid)
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=20,
                           creationflags=0x08000000)
        return (r.stdout or b"").decode("mbcs", "ignore").strip()
    except Exception as e:
        return "err:%s" % str(e)[:40]


def _set_clipboard(text):
    try:
        import subprocess as _sp
        esc = text.replace("'", "''")
        r = _sp.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "Set-Clipboard -Value '%s'" % esc],
                    capture_output=True, timeout=20, creationflags=0x08000000)
        return r.returncode == 0
    except Exception:
        return False


def connect_instance(pid):
    """已连接→切换；未连接→激活窗口 + 命令进剪贴板，返回引导信息"""
    pid = int(pid)
    for proc in _list_mech_procs():
        if proc["pid"] == pid:
            break
    else:
        return {"ok": False, "error": "该 Mechanical 进程不存在（可能已关闭）"}

    # 已连接：直接切换
    for a in (_agents_raw() if "_agents_raw" in globals() else []):
        try:
            if int(a.get("pid")) == pid and _pid_alive(pid):
                try:
                    _TARGET["pid"] = int(pid)
                    _target_save(int(pid))
                except Exception:
                    pass
                try:
                    cfg = load_config()
                    cfg["target_pid"] = int(pid)
                    save_config(cfg)
                except Exception:
                    pass
                return {"ok": True, "connected": True, "pid": pid,
                        "message": "已切换到该实例"}
        except Exception:
            pass

    # 未连接：只做"激活窗口 + 打开面板 + 复制命令"，不自动敲 Ctrl+F5
    clip = _set_clipboard(MECH_CONNECT_CMD)
    auto = {"ok": False, "stage": "等待手动加载",
            "error": "为避免重复启动定时器导致 Mechanical 闪退，连接不再自动执行脚本"}
    try:
        act = _activate_window(pid)
        if act == "ok" and not _scripting_pane_open(pid):
            _open_scripting_pane(pid)
    except Exception:
        pass
    if auto.get("ok"):
        try:
            _TARGET["pid"] = int(pid)
            _target_save(int(pid))
        except Exception:
            pass
        try:
            cfg = load_config()
            cfg["target_pid"] = int(pid)
            save_config(cfg)
        except Exception:
            pass
        return {"ok": True, "connected": True, "pid": pid,
                "message": "已自动连接并切换",
                "stage": auto.get("stage")}
    act = "ok"
    pane = "ready"
    return {"ok": True, "connected": False, "pid": pid,
            "activated": "ok", "pane": pane, "clipboard": clip,
            "stage": auto.get("stage"),
            "command": MECH_CONNECT_CMD,
            "message": "已激活该窗口并把命令放进剪贴板。请在它的 Scripting 面板里按一次 Ctrl+F5"
                       "（只需一次；重复执行会堆叠定时器导致闪退）"}


# ============================================================================
#  知识包（常用配置 + 分组几何特征）
# ============================================================================
KB_PATH = os.path.join(APP_DIR, "presets_kb.json")


def _kb_load():
    try:
        with open(KB_PATH, encoding="utf-8") as f:
            return json.load(f) or {"packs": [], "enabled": []}
    except Exception:
        return {"packs": [], "enabled": []}


def _kb_save(d):
    with open(KB_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def kb_list():
    d = _kb_load()
    en = set(d.get("enabled") or [])
    out = []
    for p in d.get("packs") or []:
        cats = p.get("categories") or {}
        out.append({"name": p.get("name"), "created": p.get("created"),
                    "categories": len(cats), "enabled": p.get("name") in en,
                    "sigs": sum(len(v.get("sigs") or []) for v in cats.values())})
    return {"ok": True, "packs": out, "enabled": sorted(en)}


def kb_capture(name, overwrite=True):
    """把当前模型「识别分组 + 几何尺寸」记成一条知识包"""
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "缺少名称"}
    lb = call_bridge("list_bodies", timeout=300)
    bodies = lb.get("bodies") or []
    bmap = {b.get("name"): b for b in bodies}
    gg = call_bridge("geom_groups", timeout=300)
    groups = {}
    for g in (gg.get("groups") or []):
        groups[g.get("name")] = list(g.get("bodies") or [])
    for n in (gg.get("ungrouped") or []):
        groups.setdefault("未分组", []).append(n)
    if not groups:
        return {"ok": False, "error": "当前模型没有分组，先执行 ② 落地"}

    cats = {}
    for cat, names in groups.items():
        if cat in ("未分组",):
            continue
        sigs = []
        for n in names:
            b = bmap.get(n) or {}
            d = sorted([round(b.get("lx") or 0), round(b.get("ly") or 0),
                        round(b.get("lz") or 0)])
            if d[0] > 0:
                s = "%dx%dx%d" % (d[0], d[1], d[2])
                if s not in sigs:
                    sigs.append(s)
        if sigs:
            cats[cat] = {"sigs": sigs[:40], "count": len(names)}

    cfg = load_config()
    sizes = cfg.get("sizes") or {}
    methods = cfg.get("methods") or {}
    pack = {"name": name, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "categories": cats,
            "sizes": {k: v for k, v in sizes.items() if k in cats},
            "methods": {k: v for k, v in methods.items() if k in cats}}

    d = _kb_load()
    packs = [p for p in (d.get("packs") or []) if p.get("name") != name]
    packs.append(pack)
    d["packs"] = packs
    if name not in (d.get("enabled") or []):
        d.setdefault("enabled", []).append(name)
    _kb_save(d)
    return {"ok": True, "name": name, "categories": len(cats),
            "enabled": d.get("enabled")}


def kb_toggle(name, on):
    d = _kb_load()
    en = [x for x in (d.get("enabled") or []) if x != name]
    if on:
        en.append(name)
    d["enabled"] = en
    _kb_save(d)
    return {"ok": True, "enabled": en}


def kb_delete(name):
    d = _kb_load()
    d["packs"] = [p for p in (d.get("packs") or []) if p.get("name") != name]
    d["enabled"] = [x for x in (d.get("enabled") or []) if x != name]
    _kb_save(d)
    return {"ok": True, "packs": len(d["packs"])}


def kb_apply_pack(name):
    """把某个知识包里的尺寸/方法写进当前配置（立即生效）"""
    d = _kb_load()
    pack = None
    for p in d.get("packs") or []:
        if p.get("name") == name:
            pack = p
            break
    if not pack:
        return {"ok": False, "error": "找不到该知识包"}
    cfg = load_config()
    sizes = cfg.setdefault("sizes", {})
    methods = cfg.setdefault("methods", {})
    n_s = n_m = 0
    for k, v in (pack.get("sizes") or {}).items():
        if v:
            sizes[k] = v
            n_s += 1
    for k, v in (pack.get("methods") or {}).items():
        if v:
            methods[k] = v
            n_m += 1
    save_config(cfg)
    return {"ok": True, "sizes": n_s, "methods": n_m}


def kb_extra_rules():
    """勾选的知识包 → {类别: [尺寸签名]}，用于增强识别"""
    d = _kb_load()
    en = set(d.get("enabled") or [])
    table = {}
    for p in d.get("packs") or []:
        if p.get("name") not in en:
            continue
        for cat, info in (p.get("categories") or {}).items():
            table.setdefault(cat, [])
            for s in (info.get("sigs") or []):
                if s not in table[cat]:
                    table[cat].append(s)
    return table


def kb_apply_sizes(cats):
    """把勾选知识包里的尺寸/方法套到同名类别（返回补充了多少）"""
    d = _kb_load()
    en = set(d.get("enabled") or [])
    cfg = load_config()
    sizes = cfg.setdefault("sizes", {})
    methods = cfg.setdefault("methods", {})
    added = 0
    names = {c.get("name") for c in (cats or [])}
    for p in d.get("packs") or []:
        if p.get("name") not in en:
            continue
        for cat, v in (p.get("sizes") or {}).items():
            if cat in names and cat not in sizes and v:
                sizes[cat] = v
                added += 1
        for cat, v in (p.get("methods") or {}).items():
            if cat in names and cat not in methods and v:
                methods[cat] = v
    if added:
        save_config(cfg)
    return added


def zones_preview():
    """按 7 原则给出分组预览（不改模型）"""
    lb = call_bridge("list_bodies", timeout=300)
    if not lb.get("ok"):
        return {"ok": False, "error": lb.get("error") or "读不到体"}
    bs = [b for b in (lb.get("bodies") or []) if not b.get("suppressed")]
    if not bs:
        return {"ok": False, "error": "没有可用的体（都被抑制？）"}

    ys = sorted([b.get("cy") or 0 for b in bs])
    y0, y1 = ys[0], ys[-1]
    span = max(1.0, y1 - y0)

    xs = sorted([abs(b.get("cx") or 0) for b in bs])
    zs = sorted([abs(b.get("cz") or 0) for b in bs])
    xmax = xs[-1] if xs else 1.0
    zmax = zs[-1] if zs else 1.0

    _pos = sum(1 for b in bs if (b.get("cx") or 0) > 0)
    _inner_pos = (_pos >= (len(bs) - _pos))

    groups = {}
    for b in bs:
        n = (b.get("name") or "").strip()
        cy = b.get("cy") or 0
        # 绝对分层线（之前定过的标准）：下部 <3748，中部 3748~6664，上部 >6664
        layer = "下" if cy < 3748 else ("上" if cy > 6664 else "中")

        # 内外侧：X 正负两侧，件多的一侧=内侧（波纹板壁有原件的一侧）
        _cx = b.get("cx") or 0
        side = "内" if (_cx > 0) == (_inner_pos) else "外"

        elem = "壳" if b.get("shell") else "实体"

        # 角色桶
        if layer == "上":
            role = "上层整体"
        elif _is_wave(n):
            role = "波纹板"
        elif layer == "下" and _is_bottom_plate(b, n):
            role = "大平面底板"
        else:
            role = "其余结构"

        _dd = sorted([round(b.get("lx") or 0), round(b.get("ly") or 0),
                      round(b.get("lz") or 0)])
        _sig = "%dx%dx%d" % (_dd[0], _dd[1], _dd[2])
        key = "%s_%s侧_%s层_%s_%s" % (elem, side, layer, role, _sig)
        groups.setdefault(key, []).append(n)

    out = []
    for k, names in groups.items():
        out.append({"group": k, "count": len(names), "samples": names[:3]})
    out.sort(key=lambda x: -x["count"])
    return {"ok": True, "total": len(bs), "groups": out}


def refresh_derived_data():
    """体重命名/分组变化后，刷新所有按体名索引的数据"""
    out = {"ok": True}
    try:
        lb = call_bridge("list_bodies", timeout=300)
        bodies = lb.get("bodies") or []
        cur = set(b.get("name") for b in bodies if not b.get("suppressed"))
        bmap = {b.get("name"): b for b in bodies}

        sys.path.insert(0, APP_DIR)
        import geo_recognize
        out["overrides"] = geo_recognize.prune_overrides(cur)

        # 按当前 Geometry 分组重建识别规则表
        gg = call_bridge("geom_groups", timeout=300)
        rules = {}
        for grp in (gg.get("groups") or []):
            names = [n for n in (grp.get("bodies") or []) if n in bmap]
            if not names:
                continue
            buckets = {}
            for n in names:
                b = bmap[n]
                d = sorted([round(b.get("lx") or 0), round(b.get("ly") or 0),
                            round(b.get("lz") or 0)])
                bbox = max(1.0, d[0] * d[1] * d[2])
                fill = round((b.get("vol") or 0) / bbox / 0.05) * 0.05
                sig = "%dx%dx%d" % (d[0], d[1], d[2])
                e = buckets.setdefault(sig, {"count": 0, "fill": fill,
                                             "shell": bool(b.get("shell")),
                                             "samples": []})
                e["count"] += 1
                if len(e["samples"]) < 2:
                    e["samples"].append(n)
            rules[grp["name"]] = {
                "sizes": [{"sig": k, "count": v["count"], "fill": v["fill"],
                           "shell": v["shell"], "samples": v["samples"]}
                          for k, v in sorted(buckets.items(),
                                             key=lambda kv: -kv[1]["count"])],
                "total": len(names),
            }
        if rules:
            with open(os.path.join(APP_DIR, "part_rules.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"source": "auto_refresh",
                           "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                           "rules": rules}, f, ensure_ascii=False, indent=1)
            out["rules_groups"] = len(rules)
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)[:160]
    return out


def debug_thin_detail(cat):
    """诊断：某类别内每个体的薄板判定明细"""
    sys.path.insert(0, APP_DIR)
    import geo_recognize
    try:
        import experience as _exp
        _exp_err = None
    except Exception as e:
        _exp = None
        _exp_err = str(e)[:100]
    lb = call_bridge("list_bodies", timeout=300)
    bodies = lb.get("bodies") or []
    rec = geo_recognize.recognize(bodies, kb_extra_rules())
    groups = rec.get("groups") or {}
    names = groups.get(cat) or []
    bmap = {b.get("name"): b for b in bodies}
    out = []
    plate = 0
    for n in names:
        b = bmap.get(n) or {}
        d = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
        if d[0] <= 0:
            continue
        ratio = d[1] / d[0]
        w = None
        if _exp is not None:
            try:
                w = (_exp.features(b) or {}).get("wall")
            except Exception:
                w = None
        is_plate = False
        if d[0] <= 8 and ratio >= 3:
            is_plate = True
        elif ratio >= 3 and d[0] <= 60 and w and w <= 4.5:
            is_plate = True
        if is_plate:
            plate += 1
        out.append({"name": n, "dims": [round(x, 1) for x in d],
                    "ratio": round(ratio, 2), "wall": w,
                    "vol": b.get("vol"), "plate": is_plate})
    return {"ok": True, "cat": cat, "n": len(names), "plate": plate,
            "auto_thin": bool(names and plate * 2 >= len(names)),
            "exp_ok": _exp is not None, "exp_err": _exp_err, "bodies": out}


def categories_from_recognition(fast=False):
    """类别 = 几何识别出的分组（与扫描结果一致）"""
    sys.path.insert(0, APP_DIR)
    import geo_recognize
    lb = call_bridge("list_bodies", timeout=300)
    if not lb.get("ok"):
        return {"ok": False, "error": lb.get("error") or "读不到体"}
    bodies = lb.get("bodies") or []
    rec = geo_recognize.recognize(bodies, kb_extra_rules())
    groups = merge_custom_groups(rec.get("groups") or {})   # 叠加人工分组
    if not groups:
        return {"ok": False, "error": "识别结果为空"}

    # 模型已有 Geometry 分组时，以分组为准（保证类别与图形一致）
    try:
        _gg = call_bridge("geom_groups", timeout=300)
        _g = {}
        _allnames = set(b.get("name") for b in bodies)
        for _x in (_gg.get("groups") or []):
            _ns = [n for n in (_x.get("bodies") or []) if n in _allnames]
            if _ns:
                _g[_x.get("name")] = _ns
        _un = [n for n in (_gg.get("ungrouped") or []) if n in _allnames]
        if _un:
            _g.setdefault("未分组", []).extend(_un)
        if _g and sum(len(v) for v in _g.values()) == len(bodies):
            groups = _g
    except Exception:
        pass

    # 勾选的知识包：把里面同名类别的尺寸/方法自动补齐（不用手点应用）
    try:
        _filled = kb_apply_sizes([{"name": g} for g in groups])
    except Exception:
        _filled = 0

    cfg = load_config()
    sz = cfg.get("sizes") or {}
    md = cfg.get("methods") or {}
    _cur_names = set(groups.keys())
    def _only_current(lst):
        return [x for x in (lst or []) if x in _cur_names]

    thin = _only_current(_flags_get(cfg, "thin_cats"))
    hole = _only_current(_flags_get(cfg, "hole_cats"))
    lock = _only_current(_flags_get(cfg, "locked_cats"))
    sw = _only_current(_flags_get(cfg, "sweep_ok"))
    _thin_off = _only_current(_flags_get(cfg, "thin_off"))
    setts = cfg.get("settings") or {}
    minsize = setts.get("MinimumSize") or 10

    ctrl_info = {}
    ctrl_known = True
    try:
        if fast:
            _cn, _cage = ctrl_names_cached(wait=True)
            if _cn is None:
                ctrl_known = False          # 还在后台读，先不判灯
                _cn = []
            for _it in _cn:
                if isinstance(_it, dict):
                    _n = _it.get("name")
                    if _n and _n != "未覆盖":
                        cur_info = ctrl_info.setdefault(_n, {})
                        if _it.get("size") is not None and cur_info.get("size") is None:
                            cur_info["size"] = _it.get("size")
                        if _it.get("method") and not cur_info.get("method"):
                            cur_info["method"] = _it.get("method")
                elif _it and _it != "未覆盖":
                    ctrl_info.setdefault(_it, {})
        else:
            sc = call_bridge("scan_categories", timeout=120)
            for c in (sc.get("categories") or []):
                n = c.get("name")
                if n and n != "未覆盖":
                    ctrl_info[n] = {"size": c.get("size_mm"),
                                    "method": c.get("method")}
    except Exception:
        ctrl_known = False
    ctrl_names = set(ctrl_info.keys())

    bmap = {b.get("name"): b for b in bodies}
    cats = []
    for g, names in groups.items():
        walls = []
        sup = 0
        try:
            import experience as _exp
        except Exception:
            _exp = None
        for n in names:
            b = bmap.get(n) or {}
            _w = None
            if _exp is not None:
                try:
                    _f = _exp.features(b) or {}
                    _w = _f.get("wall")
                except Exception:
                    _w = None
            if not _w:
                d = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
                _w = d[0]
            if _w and _w > 0:
                walls.append(_w)
            if b.get("suppressed"):
                sup += 1
        rec_size = None
        basis = None
        med = None
        if walls:
            walls.sort()
            med = walls[len(walls) // 2]
            rec_size = max(2.0, min(40.0, round(med * 2.5, 1)))
            basis = "2.5×壁厚（中位 %.2f mm）" % med

        # 自动判定薄板：中位壁厚 ≤ 8mm，或整体很薄（最薄 < 5mm）
        # 薄板判定：板状（宽度远大于厚度），不是方管/实心块
        _plate = 0
        for n in names:
            b = bmap.get(n) or {}
            _d = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
            if _d[0] <= 0:
                continue
            _ratio = _d[1] / _d[0]
            _w = None
            if _exp is not None:
                try:
                    _w = (_exp.features(b) or {}).get("wall")
                except Exception:
                    _w = None
            _is_plate = False
            if _d[0] <= 8 and _ratio >= 3:
                _is_plate = True
            elif _ratio >= 3 and _d[0] <= 60 and _w and _w <= 4.5:
                _is_plate = True
            if _is_plate:
                _plate += 1
        auto_thin = bool(names and _plate * 2 >= len(names))

        ci = ctrl_info.get(g) or {}
        # 输入框只显示 Mesh 里的真实尺寸；配置值仅作为推荐/占位
        size = ci.get("size") if ci.get("size") is not None else None
        if size is None and ci:
            size = None
        _ratio = (float(_plate) / len(names)) if names else 0.0
        # 用户明确取消过（thin_off）就不再自动标薄板
        _auto = auto_thin and (g not in _thin_off)
        # 规则只有两条：标了薄板 = 扫掠；没标 = 自动
        _is_thin_now = (g in thin) or _auto
        if _is_thin_now:
            method = "Sweep"
        else:
            method = md.get(g) or "Automatic"
            if method == "Sweep" and g not in sw and g not in thin:
                method = "Automatic"

        if sup and sup >= len(names) and names:
            light, reason = "muted", "全部已抑制（不参与网格）"
        elif not ctrl_known:
            light, reason = "muted", "控制项清单读取中（后台刷新，稍后自动更新）"
        elif g not in ctrl_names:
            light, reason = "red", "Mesh 里没有该类别控制项（点「应用设置与尺寸」生成）"
        else:
            probs = []
            if size is None:
                probs.append("控制项没设尺寸")
            else:
                if size < minsize:
                    probs.append("尺寸 %g 小于最小尺寸 %g" % (size, minsize))
                # 尺寸由用户按标准设定，这里不再因"大于推荐值"报警
            if method == "Sweep" and g not in sw and g not in thin and not _auto:
                probs.append("扫掠可能不适用，建议改自动")
            if probs:
                light, reason = "yellow", "；".join(probs)
            else:
                light = "green"
                reason = ("已建控制项：%g mm" % size) if size is not None else "已建控制项"

        cats.append({
            "name": g, "bodies": len(names), "body_names": names[:400],
            "size_mm": size, "size_recommended": rec_size, "size_basis": basis,
            "method": method,
            "is_thin_cat": (g in thin) or _auto,
            "auto_thin": _auto,
            "plate_bodies": _plate,
            "thin_ratio": (round(float(_plate) / len(names), 3) if names else 0),
            "thin_divisions": (2 if ((g in thin) or _auto) else None),
            "median_wall": med, "is_hole_cat": g in hole,
            "is_locked": g in lock, "is_sweep_ok": g in sw,
            "source": "recognize",
            "suppressed": sup, "is_all_suppressed": bool(names) and sup >= len(names),
            "light": light, "light_reason": reason,
        })
    cats.sort(key=lambda c: -c["bodies"])
    # 灯色以「Mesh 里已有控制项的真实校验」为准
    try:
        _ok_lights = _lights_from_controls(cats)
    except Exception:
        _ok_lights = False
    if not _ok_lights:
        for _c in cats:
            if _c.get("light") != "muted":
                _c["light"] = "muted"
                _c["light_reason"] = "控制项校验暂不可用（Mechanical 忙），稍后自动刷新"
    return {"ok": True, "categories": cats,
            "_ctrl_known": bool(ctrl_known),
            "ctrl_pending": (not ctrl_known),
            "total_bodies": rec.get("total") or len(bodies),
            "methods_available": ["Automatic", "Tetrahedrons", "HexDominant",
                                  "Sweep", "MultiZone", "CutCell"],
            "source": "recognize", "hole_cats": hole, "locked_cats": lock,
            "sweep_ok": sw, "suggestion": None}


def categories_by_prefix():
    """按体名前缀生成类别"""
    sys.path.insert(0, APP_DIR)
    lb = call_bridge("list_bodies", timeout=300)
    if not lb.get("ok"):
        return {"ok": False, "error": lb.get("error") or "读不到体"}
    bodies = lb.get("bodies") or []
    cfg = load_config()
    sz = cfg.get("sizes") or {}
    md = cfg.get("methods") or {}
    thin = cfg.get("thin_cats") or []
    hole = cfg.get("hole_cats") or []
    lock = cfg.get("locked_cats") or []
    sw = cfg.get("sweep_ok") or []

    # 词表：优先用"使用中"的那份记忆；没有则用全部记忆
    _vocab = set()
    _active = (cfg.get("active_memory") or "").strip()
    try:
        import model_memory as _MM
        _picks = []
        if _active:
            _p = _MM.get_profile(_active)
            if _p:
                _picks = [_p]
        if not _picks:
            for _pp in _MM.list_profiles():
                _p = _MM.get_profile(_pp["name"])
                if _p:
                    _picks.append(_p)
        for _prof in _picks:
            for _c in (_prof.get("categories") or []):
                _cn = _c.get("name")
                if _cn:
                    _vocab.add(_cn)
    except Exception:
        pass
    _vocab = sorted([v for v in _vocab if v], key=len, reverse=True)  # 长词优先

    groups = {}
    for b in bodies:
        n = (b.get("name") or "").strip()
        if not n:
            continue
        g = None
        # ---- 按尺寸识别（名字被改乱后靠这个恢复）----
        try:
            _d = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
            _d0, _d2 = _d[0], _d[2]
            _generic = (u"结构件" in n) or (u"块体" in n) or (u"未分类" in n)
            if _generic and 100 <= _d0 and _d2 <= 260:
                g = u"角件"                     # 118×162×178 / 118×178×218
            elif _generic and 20 <= _d0 <= 30 and 60 <= _d[1] <= 90 and 60 <= _d2 <= 90:
                g = u"小三角支撑"                # 25×75×75
        except Exception:
            g = None

        # 储罐单独成类：名字含"储罐"，或名字是"块体"且已被抑制
        if (u"储罐" in n) or ((u"块体" in n) and body_suppressed(b)):
            g = u"储罐"
        for v in (_vocab if g is None else []):   # 关键词命中（长词优先）
            if v and v in n:
                g = v
                break
        if g is None:                    # 没命中 -> 去掉末尾序号
            import re as _re2
            _m2 = _re2.match(r"^(.*?)[_\- ](\d{1,3})$", n)
            g = _m2.group(1) if _m2 else n
        groups.setdefault(g, []).append(b)

    # 已有控制项的类别名
    ctrl_info = {}
    try:
        sc = call_bridge("scan_categories", timeout=90)
        for c in (sc.get("categories") or []):
            n = c.get("name")
            if n and n != "未覆盖":
                ctrl_info[n] = {"size": c.get("size_mm"),
                                "method": c.get("method")}
    except Exception:
        pass
    ctrl_names = set(ctrl_info.keys())

    cats = []
    for g, items in groups.items():
        names = [x.get("name") for x in items]
        sup_n = sum(1 for x in items if x.get("suppressed"))
        walls = []
        for x in items:
            dims = sorted([x.get("lx") or 0, x.get("ly") or 0, x.get("lz") or 0])
            if dims[0] > 0:
                walls.append(dims[0])
        rec = None
        basis = None
        if walls:
            walls.sort()
            med = walls[len(walls) // 2]
            rec = max(2.0, min(40.0, round(med * 2.5, 1)))
            basis = "2.5×壁厚（中位 %.2f mm）" % med
        _ci = ctrl_info.get(g) or {}
        size = _ci.get("size") if _ci.get("size") is not None else sz.get(g)
        _method = _ci.get("method") or md.get(g) or ("Sweep" if (g in thin or g in sw) else "Automatic")
        cats.append({
            "name": g, "bodies": len(names), "body_names": names[:400],
            "size_mm": size, "size_recommended": rec, "size_basis": basis,
            "method": _method,
            "is_thin_cat": g in thin, "is_hole_cat": g in hole,
            "is_locked": g in lock, "is_sweep_ok": g in sw,
            "source": "name",
            "suppressed": sup_n,
            "is_all_suppressed": (sup_n == len(names) and len(names) > 0),
            "light": "red", "light_reason": "",
        })
    # 去重：同名类别合并（防止名字里夹杂不可见差异）
    merged = {}
    for c in cats:
        k = c["name"]
        if k not in merged:
            merged[k] = c
        else:
            have = set(merged[k].get("body_names") or [])
            for n in (c.get("body_names") or []):
                if n not in have:
                    merged[k]["body_names"].append(n)
                    have.add(n)
            merged[k]["bodies"] = len(have)
    cats = list(merged.values())

    _setts = cfg.get("settings") or {}
    _minsize = _setts.get("MinimumSize") or 10
    for _c in cats:
        _n = _c["name"]
        _cnt = len(_c.get("body_names") or [])
        _sup = _c.get("suppressed") or 0
        if _sup and _sup >= _cnt:
            _c["light"] = "muted"
            _c["light_reason"] = "全部已抑制（不参与网格）"
            continue
        if _n not in ctrl_names:
            _c["light"] = "red"
            _c["light_reason"] = "Mesh 里没有该类别控制项（点「应用设置与尺寸」生成）"
            continue
        _probs = []
        _szv = _c.get("size_mm")
        if _szv is None:
            _probs.append("控制项没设尺寸")
        else:
            if _szv < _minsize:
                _probs.append("尺寸 %g 小于最小尺寸 %g" % (_szv, _minsize))
            _rec = _c.get("size_recommended")
            if _rec and _szv > _rec * 3:
                _probs.append("尺寸 %g 远大于推荐 %g，易畸形" % (_szv, _rec))
        if _c.get("method") == "Sweep" and _n not in sw:
            _probs.append("扫掠不适用于不规则件，建议改自动")
        if _probs:
            _c["light"] = "yellow"
            _c["light_reason"] = "；".join(_probs)
        else:
            _c["light"] = "green"
            _c["light_reason"] = ("已建控制项：%g mm" % _szv) if _szv is not None else "已建控制项"

    cats.sort(key=lambda c: -c["bodies"])
    return {"ok": True, "categories": cats,
            "total_bodies": len(bodies), "methods_available":
            ["Automatic", "Tetrahedrons", "HexDominant", "Sweep", "MultiZone", "CutCell"],
            "source": "name", "hole_cats": hole, "locked_cats": lock,
            "sweep_ok": sw, "suggestion": None}


def regroup_by_name(dry=False):
    """按体名前缀重建 Geometry 分组"""
    sys.path.insert(0, APP_DIR)
    lb = call_bridge("list_bodies", timeout=300)
    if not lb.get("ok"):
        return {"ok": False, "error": lb.get("error") or "读不到体"}
    bodies = lb.get("bodies") or []
    groups = {}
    for b in bodies:
        n = (b.get("name") or "").strip()
        if not n:
            continue
        g = n
        # 去掉末尾的 _01 / -3 / (2) 之类序号
        i = len(g) - 1
        while i >= 0 and (g[i].isdigit() or g[i] in "_- ()"):
            i -= 1
        if i > 0:
            g = g[:i + 1].rstrip("_- ()")
        elif g == n:
            # 没有序号：用整体名做组
            g = n
        groups.setdefault(g, []).append(n)
    items = [{"group": k, "bodies": v} for k, v in groups.items() if v]
    if dry:
        return {"ok": True, "dry": True,
                "groups": [{"name": k, "count": len(v)} for k, v in groups.items()]}
    r = call_bridge("group_geometry", {"items": items}, timeout=3600)
    return {"ok": bool(r.get("ok")), "made": len(r.get("groups") or []),
            "moved": r.get("moved") or 0, "failed": r.get("failed") or [],
            "detail": r.get("groups") or []}


def _snapshot_groups():
    """当前 Geometry 分组快照 {名字: [体名]}"""
    try:
        gg = call_bridge("geom_groups", timeout=300)
        out = {}
        for g in (gg.get("groups") or []):
            ns = list(g.get("bodies") or [])
            if ns:
                out[g.get("name")] = ns
        return out
    except Exception:
        return {}


def _verify_groups(expected):
    """校验分组是否都建好；返回缺失/不一致列表"""
    try:
        gg = call_bridge("geom_groups", timeout=300)
        have = {}
        for g in (gg.get("groups") or []):
            have[g.get("name")] = set(g.get("bodies") or [])
        bad = []
        for name, members in expected.items():
            got = have.get(name)
            if got is None:
                bad.append({"group": name, "reason": "缺失",
                            "want": len(members), "got": 0})
                continue
            want = set(members)
            if got != want:
                bad.append({"group": name, "reason": "成员不一致",
                            "want": len(want), "got": len(got),
                            "missing": sorted(want - got)[:5],
                            "extra": sorted(got - want)[:5]})
        return bad
    except Exception as e:
        return [{"group": "-", "reason": "校验失败: %s" % str(e)[:80]}]


def _restore_groups(snapshot):
    """用快照把分组重建回去（尽力而为）"""
    if not snapshot:
        return {"ok": False, "error": "没有快照"}
    items = [{"group": k, "bodies": v} for k, v in snapshot.items() if v]
    try:
        call_bridge("ungroup_folders", {"names": ["*"]}, timeout=1800)
        r = call_bridge("group_geometry", {"items": items}, timeout=3600)
        return {"ok": bool(r.get("ok")), "groups": r.get("groups"),
                "failed": r.get("failed")}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


def scan_model(profile_name=None):
    """全局扫描：每个体给出建议组名 + 几何依据（不改模型）"""
    sys.path.insert(0, APP_DIR)
    import experience
    import model_memory as M

    bodies = call_bridge("list_bodies", timeout=120)
    if not bodies.get("ok"):
        return {"ok": False, "error": bodies.get("error") or "读取体失败"}
    bl = bodies.get("bodies") or []

    prof = None
    if profile_name:
        prof = M.get_profile(profile_name)
    if prof is None:
        _cfg2 = load_config()
        _act2 = (_cfg2.get("active_memory") or "").strip()
        if _act2:
            prof = M.get_profile(_act2)
    if prof is None:
        pf = M.list_profiles()
        if pf:
            # 优先用"我的划分"（用户手工确认过的）
            names = [x["name"] for x in pf]
            pick = None
            for n in names:
                if "我的划分" in n:
                    pick = n
                    break
            prof = M.get_profile(pick or names[0])

    plan = []
    if prof:
        res = M.plan_for(bl, prof)
        for p in res.get("plan") or []:
            for n in p["bodies"]:
                plan.append({"body": n, "group": p["category"],
                             "size": p.get("size")})
        unmatched = res.get("unmatched") or []
    else:
        unmatched = [b.get("name") for b in bl]

    # 补充几何依据与经验建议
    # 未匹配的体：先按"大立方体=储罐"规则归类
    feat_map = {b.get("name"): experience.features(b) for b in bl}
    by_name_raw = {b.get("name"): b for b in bl}
    tanks = []
    still_un = []
    for nm in unmatched:
        b = by_name_raw.get(nm)
        if b is not None and detect_tank(b):
            tanks.append(nm)
            plan.append({"body": nm, "group": "储罐", "size": 40.0})
        else:
            still_un.append(nm)
    unmatched = still_un

    rows = []
    for item in plan:
        f = feat_map.get(item["body"]) or {}
        adv = []
        try:
            adv = experience.advise(f, limit=2)
        except Exception:
            adv = []
        rows.append({"body": item["body"], "group": item["group"],
                     "size": item.get("size"),
                     "wall": f.get("wall"), "aspect": f.get("aspect"),
                     "hollow": f.get("hollow"),
                     "advice": adv[0] if adv else None})

    # 记忆没匹配到任何体 → 退回"按体名前缀分组"，保证扫描有结果
    if not rows:
        try:
            _pf = categories_by_prefix()
            if _pf.get("ok"):
                for _c in (_pf.get("categories") or []):
                    for _bn in (_c.get("body_names") or []):
                        _f = feat_map.get(_bn) or {}
                        rows.append({"body": _bn, "group": _c["name"],
                                     "size": _c.get("size_mm"),
                                     "wall": _f.get("wall"),
                                     "aspect": _f.get("aspect"),
                                     "hollow": _f.get("hollow"),
                                     "advice": None})
                unmatched = []
        except Exception:
            pass

    return {"ok": True,
            "profile": prof.get("name") if prof else None,
            "total": len(bl),
            "matched": len(plan),
            "unmatched": unmatched,
            "rows": rows,
            "groups": sorted(set(r["group"] for r in rows))}


def body_group_map():
    """体名 -> 所属组（按实际控制项）"""
    try:
        plan = call_bridge("read_plan", timeout=60)
    except Exception:
        return {}
    if not plan.get("ok"):
        return {}
    out = {}
    for c in plan.get("controls") or []:
        nm = c.get("name") or ""
        if nm.startswith("METHOD_"):
            continue
        g = nm
        for pre in ("SIZE_FIX_", "SIZE_", "SWEEP_", "SHELL_"):
            if g.startswith(pre):
                g = g[len(pre):]
                break
        for b in (c.get("bodies") or []):
            out.setdefault(b, g)
    return out


def body_risk_rows(limit=40):
    """按几何风险给体排序：壁厚/长细比/面数/单元数 -> 风险分与原因"""
    try:
        bodies = call_bridge("list_bodies", timeout=60)
        plan = call_bridge("read_plan", timeout=60)
        counts = call_bridge("body_counts", timeout=60)
    except Exception:
        return []
    if not bodies.get("ok"):
        return []
    els = {r.get("name"): r.get("elements") or 0
           for r in (counts.get("bodies") or [])}
    group_of = {}
    for c in ((plan.get("controls") if plan.get("ok") else None) or []):
        nm = c.get("name") or ""
        if nm.startswith("METHOD_"):
            continue
        g = nm
        for pre in ("SIZE_FIX_", "SIZE_", "SWEEP_", "SHELL_"):
            if g.startswith(pre):
                g = g[len(pre):]
                break
        for b in (c.get("bodies") or []):
            group_of.setdefault(b, g)

    rows = []
    for b in bodies.get("bodies") or []:
        dims = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
        if dims[0] <= 0:
            continue
        bbox = dims[0] * dims[1] * dims[2] or 1.0
        fill = max(0.0, min(1.0, (b.get("vol") or 0) / bbox))
        if fill >= 0.6:
            wall = dims[0]
        else:
            wall = 0.5 * dims[1] * (1.0 - max(0.0, 1.0 - fill) ** 0.5)
            wall = max(0.3, min(wall, dims[0]))
        aspect = (dims[2] / dims[0]) if dims[0] else 0
        e = els.get(b.get("name")) or 0
        # 风险分：薄壁 + 长细 + 单元占比
        score = 0.0
        if fill < 0.6:
            score += 2.0 * (40.0 / max(wall, 0.3))
        score += 1.5 * min(aspect, 50) / 10.0
        score += 1.0 * (e / 50000.0)
        why = []
        if fill < 0.6 and wall < 4:
            why.append(u"薄壁 %.1fmm" % wall)
        if aspect > 20:
            why.append(u"长细比 %.0f" % aspect)
        if e > 50000:
            why.append(u"单元多 %.0f万" % (e / 10000.0))
        rows.append({"name": b.get("name"), "group": group_of.get(b.get("name"), u"-"),
                     "wall": round(wall, 2), "aspect": round(aspect, 1),
                     "elements": e, "score": round(score, 1),
                     "why": u"、".join(why) or u"形状/尺寸一般"})
    rows.sort(key=lambda d: -d["score"])
    return rows[:limit]


def compute_light(item: dict, cfg: dict) -> tuple:
    """状态灯：green=尺寸方法都配好；yellow=配置有问题；red=未配置"""
    settings = cfg.get("settings") or {}
    try:
        floor = float(settings.get("MinimumSize") or 10.0)
        cap = float(settings.get("ElementSize") or 40.0)
    except Exception:
        floor, cap = 10.0, 40.0

    size = item.get("size_mm")
    method = item.get("method") or "Automatic"
    shells = item.get("shells") or 0

    if not item.get("has_size_ctrl"):
        return "red", "没有尺寸控制项"
    if size is None:
        return "yellow", "尺寸未设置（跟随全局 %g mm）" % cap
    if size < floor - 1e-6 or size > cap + 1e-6:
        return "yellow", "尺寸 %g 超出 %g~%g mm" % (size, floor, cap)
    if shells and method in ("Sweep", "MultiZone"):
        return "yellow", "含壳体，不支持%s" % method
    if method != "Automatic" and not item.get("has_method_ctrl"):
        return "yellow", "方法控制项缺失"
    if method == "Automatic" and item.get("has_method_ctrl"):
        return "yellow", "存在多余的方法控制项"
    return "green", "尺寸/方法已配置"


# ============================================================================
#  IPC
# ============================================================================
def _read_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, default=str)
    if os.path.isfile(path):
        os.remove(path)
    os.rename(tmp, path)


AGENTS_DIR = os.path.join(IPC_DIR, "agents")
_TARGET = {"pid": None}


def _agents_raw():
    """读取 ipc/agents 下的注册文件，返回仍活着的实例"""
    out = []
    try:
        if not os.path.isdir(AGENTS_DIR):
            return out
        for fn in os.listdir(AGENTS_DIR):
            if not fn.endswith(".json"):
                continue
            d = _read_json(os.path.join(AGENTS_DIR, fn)) or {}
            pid = d.get("pid")
            if not pid:
                continue
            age = time.time() - os.path.getmtime(os.path.join(AGENTS_DIR, fn))
            if age > 20:
                continue
            d["age"] = round(age, 1)
            out.append(d)
    except Exception:
        pass
    out.sort(key=lambda d: d.get("pid") or 0)
    return out


def _pid_alive(pid):
    """该 pid 是否还活着（ctypes，毫秒级）"""
    try:
        import ctypes
        import ctypes.wintypes as wt
        k32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        code = wt.DWORD()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == STILL_ACTIVE
    except Exception:
        return True

def agents_list():
    """在线实例 + 工程名（沿父进程找到 Workbench 的 wbpj）"""
    out = []
    for a in _agents_raw():
        if not _pid_alive(a.get("pid")):
            continue
        pid = a.get("pid")
        item = {"pid": pid, "version": a.get("version"),
                "busy": bool(a.get("busy")), "age": a.get("age"),
                "project": None, "path": None}
        try:
            item["project"], item["path"] = _project_of_pid(pid)
        except Exception:
            pass
        out.append(item)
    return out


_PID_PROJ = {}


def _project_of_pid(pid):
    """沿 Mechanical 的父进程（Workbench）读工程路径，缓存 60 秒"""
    now = time.time()
    c = _PID_PROJ.get(pid)
    if c and now - c[0] < 60:
        return c[1], c[2]
    name = path = None
    try:
        cmd = [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "$p=Get-CimInstance Win32_Process -Filter \"ProcessId=%d\";"
            "if($p){$q=Get-CimInstance Win32_Process -Filter "
            "\"ProcessId=$($p.ParentProcessId)\";"
            "if($q){$q.CommandLine}} else {''}" % pid
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=15,
                           creationflags=0x08000000)
        text = (r.stdout or b"").decode("mbcs", "replace")
        m = re.search(r'([A-Za-z]:[^"]*?\.wbpj)', text, re.I)
        if m:
            path = m.group(1).strip().strip('"')
            base = path.replace("/", "\\").split("\\")[-1]
            name = base[:-5] if base.lower().endswith(".wbpy") else base
            if name.lower().endswith(".wbpj"):
                name = name[:-5]
    except Exception:
        pass
    _PID_PROJ[pid] = (now, name, path)
    return name, path


TARGET_FILE = os.path.join(IPC_DIR, "target.json")


def _target_load():
    try:
        with open(TARGET_FILE, encoding="utf-8") as f:
            return (json.load(f) or {}).get("pid")
    except Exception:
        return None


def _target_save(pid):
    try:
        os.makedirs(IPC_DIR, exist_ok=True)
        with open(TARGET_FILE, "w", encoding="utf-8") as f:
            json.dump({"pid": pid}, f)
    except Exception:
        pass


def target_pid():
    """当前选中的实例：内存 → target.json → 第一个在线实例
       注意：target.json 记录的是"用户选择"，只要那个 Mechanical 进程还在就认它
             （代理文件可能暂时过期，但进程在就说明模型还开着）
    """
    alive = [a["pid"] for a in _agents_raw()]
    # 进程集合必须一直算（带缓存，毫秒级）：
    # 长任务期间代理文件不刷新，若只看代理文件会把目标误判为离线
    procs = set()
    try:
        procs = set(p["pid"] for p in _list_mech_procs())
    except Exception:
        procs = set()

    cur = _TARGET.get("pid")
    if cur in alive or cur in procs:
        return cur

    saved = _target_load()
    if saved is not None and (saved in alive or saved in procs):
        _TARGET["pid"] = saved
        return saved

    if alive:
        _TARGET["pid"] = alive[0]
        return alive[0]
    if procs:
        pick = sorted(procs)[0]
        _TARGET["pid"] = pick
        return pick
    _TARGET["pid"] = None
    return None


def bridge_alive(max_age=15.0) -> bool:
    """目标实例是否在线"""
    return target_pid() is not None


def _call_bridge_impl(op: str, payload: dict | None = None, timeout: float | None = None) -> dict:
    os.makedirs(IPC_DIR, exist_ok=True)
    seq = uuid.uuid4().hex[:12]
    timeout = timeout if timeout is not None else OP_TIMEOUT.get(op, 120)

    pid = target_pid()
    if pid is None:
        return {"ok": False, "error": "没有在线的 Mechanical 实例"}
    cmd_path = os.path.join(IPC_DIR, "cmd_%d.json" % pid)
    res_path = os.path.join(IPC_DIR, "result_%d.json" % pid)

    # 按实例独立文件队列，由外层 _PID_LOCKS[pid] 保证单实例串行，不再用全局锁互堵
    try:
        if os.path.isfile(res_path):
            os.remove(res_path)
    except Exception:
        pass
    _write_json(cmd_path, {"seq": seq, "op": op, "payload": payload or {}})

    deadline = time.time() + timeout
    while time.time() < deadline:
        res = _read_json(res_path)
        if res and res.get("seq") == seq:
            try:
                os.remove(res_path)
            except Exception:
                pass
            return res
        time.sleep(0.4)

    return {
        "ok": False,
        "error": (
            "桥接代理超时（%ss）。请在 Mechanical 的 Scripting 面板运行：\n"
            "execfile(\"D:/ai/meshkit/app/bridge_agent.py\")" % int(timeout)
        ),
        "bridge_offline": True,
    }


# ============================================================================
#  HTTP
# ============================================================================

READ_OPS = frozenset((
    "ping", "status", "list_bodies", "geom_groups", "read_plan",
    "scan_categories", "validate_controls", "body_counts", "failed_detail",
    "mesh_messages", "diagnose", "quality_metrics", "get_params",
    "probe_tree", "probe_geom_groups", "project_info", "probe_ctrl_scope",
))


_PID_LOCKS = {}
_PID_LOCK_TS = {}


def _pid_lock(pid):
    """每个实例一把锁：一个实例卡住不影响另一个"""
    pid = int(pid or 0)
    lk = _PID_LOCKS.get(pid)
    if lk is None:
        lk = threading.RLock()
        _PID_LOCKS[pid] = lk
    return lk


def reset_bridge_locks():
    """强制丢弃所有按实例锁（卡死时用）"""
    n = len(_PID_LOCKS)
    _PID_LOCKS.clear()
    _PID_LOCK_TS.clear()
    return n


def call_bridge(op, payload=None, timeout=None, wait=None):
    """串行化包装（按实例）：读操作拿不到锁立刻返回 busy，写操作最多等 180 秒"""
    pid = target_pid()
    if pid is None:
        return {"ok": False, "error": "没有在线的 Mechanical 实例"}
    lk = _pid_lock(pid)
    if wait is None:
        # 读操作排队等 12 秒（页面会并发发多个读请求，直接拒绝会让列表空白）
        wait = 12.0 if op in READ_OPS else 180.0
    if not lk.acquire(timeout=wait):
        held = round(time.time() - _PID_LOCK_TS.get(int(pid), time.time()), 1)
        return {"ok": False, "busy": True,
                "error": "该实例正在忙（上一个操作已占用 %.0f 秒），可稍候或点「解除卡住」" % max(held, 0)}
    _PID_LOCK_TS[int(pid)] = time.time()
    try:
        return _call_bridge_impl(op, payload, timeout)
    finally:
        try:
            lk.release()
        except Exception:
            pass
        _PID_LOCK_TS.pop(int(pid), None)


_JOBS = {}
_JOBS_LOCK = threading.Lock()


def _prune_jobs_locked():
    if len(_JOBS) <= 50:
        return
    ordered = sorted(_JOBS.items(), key=lambda kv: kv[1].get("created_at", 0))
    for k, _ in ordered[:-50]:
        if _JOBS[k].get("status") != "running":
            _JOBS.pop(k, None)


def _start_async_job(op, fn):
    jid = uuid.uuid4().hex[:12]
    job = {
        "job_id": jid, "op": op, "status": "running",
        "created_at": time.time(), "finished_at": None,
        "result": None, "error": None,
    }
    with _JOBS_LOCK:
        _JOBS[jid] = job
        _prune_jobs_locked()

    def _run():
        try:
            res = fn()
            with _JOBS_LOCK:
                job["result"] = res
                job["status"] = "done" if (isinstance(res, dict) and res.get("ok")) else "error"
                if isinstance(res, dict) and not res.get("ok"):
                    job["error"] = res.get("error") or ""
                job["finished_at"] = time.time()
        except Exception:
            with _JOBS_LOCK:
                job["status"] = "error"
                job["error"] = traceback.format_exc()
                job["result"] = {"ok": False, "error": job["error"]}
                job["finished_at"] = time.time()

    threading.Thread(target=_run, daemon=True).start()
    return jid


class Handler(BaseHTTPRequestHandler):
    server_version = "MeshKitAI/1.0"

    def log_message(self, fmt, *args):  # 静音
        pass

    # ---------- 工具 ----------
    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    # ---------- 路由 ----------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])

            if path == "/api/ping":
                return self._json({"ok": True, "bridge": bridge_alive(),
                                   "pid": os.getpid(), "time": time.strftime("%H:%M:%S")})

            if path == "/api/job":
                jid = (qs.get("id") or [""])[0].strip()
                if not jid:
                    with _JOBS_LOCK:
                        items = [
                            {k: v for k, v in j.items() if k != "result"}
                            for j in sorted(_JOBS.values(),
                                            key=lambda x: x.get("created_at", 0),
                                            reverse=True)[:20]
                        ]
                    return self._json({"ok": True, "jobs": items})
                with _JOBS_LOCK:
                    job = _JOBS.get(jid)
                    snap = dict(job) if job else None
                if not snap:
                    return self._json({"ok": False, "error": "任务不存在或已清理"}, 404)
                snap["ok"] = True
                snap["progress"] = _read_json(PROGRESS_PATH) or {}
                return self._json(snap)

            if path == "/api/auto_scan":
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                lb = call_bridge("list_bodies", timeout=300)
                if not lb.get("ok"):
                    return self._json({"ok": False, "error": lb.get("error")})
                sys.path.insert(0, APP_DIR)
                import geo_recognize
                r = geo_recognize.recognize(lb.get("bodies") or [], kb_extra_rules())
                r["groups"] = merge_custom_groups(r.get("groups") or {})
                r["group_count"] = len(r["groups"])
                return self._json(r)

            if path == "/api/zones":
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                return self._json(zones_preview())

            if path == "/api/scan":
                q = self.path.split("?", 1)[-1]
                prof_name = None
                for kv in q.split("&"):
                    if kv.startswith("profile="):
                        from urllib.parse import unquote
                        prof_name = unquote(kv[8:])
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                return self._json(scan_model(prof_name))

            if path == "/api/experience":
                sys.path.insert(0, APP_DIR)
                import experience
                return self._json({"ok": True, "stats": experience.stats(),
                                   "recent": (experience._load().get("cases") or [])[-12:]})

            if path == "/api/advice":
                sys.path.insert(0, APP_DIR)
                import experience
                q = self.path.split("?", 1)[-1]
                body_name = None
                for kv in q.split("&"):
                    if kv.startswith("body="):
                        from urllib.parse import unquote
                        body_name = unquote(kv[5:])
                feats = _bodies_features()
                info = feats.get(body_name) or {}
                if not info.get("features"):
                    return self._json({"ok": False, "error": "找不到该体的几何信息"})
                adv = experience.advise(info["features"])
                return self._json({"ok": True, "body": body_name,
                                   "group": info.get("group"),
                                   "advice": adv})

            if path == "/api/instances":
                return self._json(instances_list())

            if path == "/api/agents":
                return self._json({"ok": True, "agents": agents_list(),
                                   "target": target_pid()})

            if path == "/api/project":
                return self._json(project_file(force=self.path.endswith("?force=1")))

            if path == "/api/status":

                _busy = _mech_busy()
                if _busy:
                    return self._json({"ok": False, "busy": True,
                                       "step": _busy,
                                       "error": "Mechanical 正在忙：%s，稍后自动重试" % _busy})
                if not bridge_alive():
                    return self._json({"ok": False, "bridge_offline": True,
                                       "error": "桥接代理未运行。请在 Mechanical 里运行 bridge_agent.py"})
                _tp_s = target_pid()
                _st = cache_get(("status", _tp_s), 30,
                                lambda: call_bridge("status", timeout=180))
                try:
                    _remember_failed(_st)      # status_remember
                except Exception:
                    pass
                return self._json(_st)

            if path == "/api/categories":

                _busy = _mech_busy()
                if _busy:
                    return self._json({"ok": False, "busy": True,
                                       "step": _busy,
                                       "error": "Mechanical 正在忙：%s，稍后自动重试" % _busy})
                if not bridge_alive():
                    return self._json({"ok": False, "bridge_offline": True,
                                       "error": "桥接代理未运行（bridge_agent.py）"})
                
                # ⓿ 优先用几何识别结果（与扫描分组一致）—— 60 秒缓存
                _tp_c = target_pid()
                _rec = cache_get(("categories", _tp_c), 60,
                                 lambda: categories_from_recognition(fast=True))
                if _rec.get("loading"):
                    return self._json(_rec)
                # 灯色每次按「控制项校验」实时套用（校验结果自身有 30 秒缓存）
                try:
                    if _rec.get("categories"):
                        _lights_from_controls(_rec["categories"])
                except Exception:
                    pass
                if _rec.get("ok"):
                    try:
                        _tp0 = target_pid()
                        _pn0, _pp0 = _project_of_pid(_tp0)
                        _rec["pid"] = _tp0
                        _rec["project"] = _pn0
                    except Exception:
                        pass
                    return self._json(_rec)

                # ⓿b 退化：按体名前缀分组
                _pref = categories_by_prefix()
                if _pref.get("ok") and (_pref.get("categories") or []):
                    return self._json(_pref)
                
                # ① 优先用 Geometry 分组作为类别来源
                _gg = call_bridge("geom_groups", timeout=180)
                if _gg.get("ok") and (_gg.get("groups") or []):
                    _cfg = load_config()
                    _sz = _cfg.get("sizes") or {}
                    _md = _cfg.get("methods") or {}
                    _thin = _cfg.get("thin_cats") or []
                    _hole = _cfg.get("hole_cats") or []
                    _lock = _cfg.get("locked_cats") or []
                    _sw = _cfg.get("sweep_ok") or []
                    _cats = []
                    for _g in (_gg.get("groups") or []):
                        _n = _g.get("name")
                        _bs = _g.get("bodies") or []
                        _cats.append({"name": _n, "bodies": len(_bs),
                                      "body_names": _bs[:400],
                                      "size_mm": _sz.get(_n),
                                      "method": _md.get(_n) or ("Sweep" if (_n in _thin or _n in _sw) else "Automatic"),
                                      "is_thin_cat": _n in _thin,
                                      "is_hole_cat": _n in _hole,
                                      "is_locked": _n in _lock,
                                      "is_sweep_ok": _n in _sw,
                                      "source": "geometry"})
                    _un = _gg.get("ungrouped") or []
                    if _un:
                        _cats.append({"name": "未分组", "bodies": len(_un),
                                      "body_names": _un[:400],
                                      "size_mm": _sz.get("未分组"),
                                      "method": _md.get("未分组") or "Automatic",
                                      "is_thin_cat": False, "is_hole_cat": False,
                                      "is_locked": False, "is_sweep_ok": False,
                                      "source": "geometry"})
                    # 合并 Mesh 里的控制项类别（Geometry 分组丢失时也能读全）
                    try:
                        _sc = call_bridge("scan_categories", timeout=90)
                        _have = set(c["name"] for c in _cats)
                        for _c in (_sc.get("categories") or []):
                            _n2 = _c.get("name")
                            if not _n2 or _n2 in _have or _n2 == "未覆盖":
                                continue
                            _cats.append({"name": _n2, "bodies": _c.get("bodies") or 0,
                                          "body_names": _c.get("body_names") or [],
                                          "size_mm": _c.get("size_mm"),
                                          "method": _c.get("method") or "Automatic",
                                          "is_thin_cat": _n2 in _thin,
                                          "is_hole_cat": _n2 in _hole,
                                          "is_locked": _n2 in _lock,
                                          "is_sweep_ok": _n2 in _sw,
                                          "source": "mesh"})
                            _have.add(_n2)
                    except Exception:
                        pass
                    # 推荐尺寸：按各组体的"中位壁厚 × 2.5"给建议
                    try:
                        _feats = _bodies_features()
                        for _c in _cats:
                            _ws = []
                            for _bn in (_c.get("body_names") or []):
                                _fi = _feats.get(_bn) or {}
                                _w = (_fi.get("features") or {}).get("wall")
                                if _w:
                                    _ws.append(_w)
                            if _ws:
                                _ws.sort()
                                _med = _ws[len(_ws) // 2]
                                _c["size_recommended"] = max(2.0, min(40.0, round(_med * 2.5, 1)))
                                _c["size_basis"] = "2.5×壁厚（中位 %.2f mm）" % _med
                    except Exception:
                        pass
                    
                    _METHODS = ["Automatic", "Tetrahedrons", "HexDominant",
                                "Sweep", "MultiZone", "CutCell"]
                    # 状态灯：红灯=未设尺寸；黄灯=有尺寸但未建控制项；绿灯=已建控制项
                    try:
                        _mesh_names = set(c["name"] for c in _cats if c.get("source") == "mesh")
                        for _c in _cats:
                            _szv = _c.get("size_mm")
                            if _szv is None:
                                _c["light"] = "red"
                                _c["light_reason"] = "未设尺寸（跟随全局 %g mm）" % (_sz.get("ElementSize") or 40)
                            elif _c.get("source") == "mesh" or _c["name"] in _mesh_names:
                                _c["light"] = "green"
                                _c["light_reason"] = "已建控制项：%g mm" % _szv
                            else:
                                _c["light"] = "yellow"
                                _c["light_reason"] = "已设 %g mm，尚未应用到 Mesh（点「应用设置与尺寸」）" % _szv
                    except Exception:
                        pass
                    _cats.sort(key=lambda c: -c["bodies"])
                    return self._json({"ok": True, "categories": _cats,
                                       "total_bodies": _gg.get("total") or 0,
                                       "methods_available": _METHODS, "source": "geometry",
                                       "hole_cats": _hole, "locked_cats": _lock,
                                       "sweep_ok": _sw, "suggestion": None})
                res = call_bridge("scan_categories", timeout=90)
                if not res.get("ok"):
                    return self._json(res)
                cfg = load_config()
                sizes = cfg.get("sizes") or {}
                methods = cfg.get("methods") or {}
                for item in (res.get("categories") or []):
                    cat = item.get("name")
                    base = item.get("base") or cat
                    if item.get("size_mm") is None:
                        # 模型里没有尺寸控制：先用配置（含按基础类别继承），再用建议值
                        val = sizes.get(cat)
                        if val is None:
                            val = sizes.get(base)
                        if val is None:
                            val = item.get("size_suggested")
                        item["size_mm"] = val
                        item["size_is_default"] = True
                    elif cat not in sizes:
                        sizes[cat] = item["size_mm"]
                    if item.get("method") in (None, "", "Automatic"):
                        item["method"] = (methods.get(cat)
                                          or methods.get(base) or "Automatic")
                cfg["sizes"] = sizes
                # 手动标记为薄板的类别：覆盖自动判定
                thin_cats = cfg.get("thin_cats") or []
                for item in (res.get("categories") or []):
                    if item.get("name") in thin_cats:
                        item["is_thin_cat"] = True
                        item["thin"] = max(int(item.get("thin") or 0),
                                           int(item.get("bodies") or 1))
                        item["thin_manual"] = True
                    else:
                        item["thin_manual"] = False
                res["thin_cats"] = thin_cats
                sweep_ok = cfg.get("sweep_ok") or []
                hole_cats = cfg.get("hole_cats") or []
                for item in (res.get("categories") or []):
                    item["is_sweep_ok"] = item.get("name") in sweep_ok
                    item["is_hole_cat"] = item.get("name") in hole_cats
                res["sweep_ok"] = sweep_ok
                res["hole_cats"] = hole_cats
                locked = cfg.get("locked_cats") or []
                for item in (res.get("categories") or []):
                    item["is_locked"] = item.get("name") in locked
                res["locked_cats"] = locked
                save_config(cfg)
                res["methods_available"] = ["Automatic", "Tetrahedrons", "HexDominant",
                                            "Sweep", "MultiZone", "CutCell"]
                # 尺寸推荐（按几何特征 + 单元预算）
                try:
                    sys.path.insert(0, APP_DIR)
                    import sizing_advisor
                    cats = res.get("categories") or []
                    sug = sizing_advisor.recommend(
                        cats, cfg,
                        budget_elements=cfg.get("budget_elements"),
                        current_elements=res.get("elements") or 0)
                    rec = {it["name"]: it for it in sug.get("items") or []}
                    for item in cats:
                        r = rec.get(item.get("name"))
                        if r:
                            item["size_recommended"] = r["size_budget"]
                            item["size_feature"] = r["size"]
                            item["size_basis"] = r["basis"]
                    # 按结构角色调整推荐尺寸（框架放大、角件细化…），并按全局上下限收敛
                    _s = cfg.get("settings") or {}
                    _cap = float(_s.get("ElementSize") or 40.0)
                    _floor = float(_s.get("MinimumSize") or 2.0)
                    for item in cats:
                        for key in ("size_recommended", "size_feature"):
                            v = item.get(key)
                            if not v:
                                continue
                            v = apply_size_factor(item.get("name"), v)
                            item[key] = round(max(_floor, min(_cap, v)), 1)
                    res["suggestion"] = {
                        "k": sug.get("k"), "scale": sug.get("scale"),
                        "budget_elements": sug.get("budget_elements"),
                        "est_elements": sug.get("est_elements"),
                        "est_feature": sug.get("est_feature"),
                        "current_elements": sug.get("current_elements"),
                        "floor": sug.get("floor"), "cap": sug.get("cap"),
                        "over_budget": sug.get("over_budget"),
                        "note": sug.get("note"),
                    }
                    # 状态灯
                    for item in cats:
                        light, reason = compute_light(item, cfg)
                        item["light"] = light
                        item["light_reason"] = reason
                    res["summary_lights"] = {
                        "green": len([c for c in cats if c.get("light") == "green"]),
                        "yellow": len([c for c in cats if c.get("light") == "yellow"]),
                        "red": len([c for c in cats if c.get("light") == "red"]),
                    }
                except Exception:
                    res["suggestion_error"] = traceback.format_exc()[-800:]
                return self._json(res)

            if path == "/api/params":
                if not bridge_alive():
                    return self._json({"ok": False, "bridge_offline": True,
                                       "error": "桥接代理未运行（bridge_agent.py）"})
                cat = (qs.get("category") or [""])[0]
                cname = (qs.get("control_name") or [""])[0]
                method = (qs.get("method") or [""])[0]
                res = call_bridge("get_params",
                                  {"category": cat, "control_name": cname,
                                   "method": method}, timeout=60)
                if res.get("ok"):
                    cfg = load_config()
                    saved = ((cfg.get("advanced") or {}).get(cat) or {})
                    for p in res.get("params") or []:
                        p["saved"] = saved.get(p["api"])
                return self._json(res)

            if path == "/api/memory":
                sys.path.insert(0, APP_DIR)
                import model_memory
                _cfg = load_config()
                _act = _cfg.get("active_memory")
                _items = model_memory.list_profiles()
                for _it in _items:
                    _it["active"] = (_it.get("name") == _act)
                return self._json({"ok": True, "items": _items,
                                   "active": _act})

            if path == "/api/risk":
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                bodies = call_bridge("list_bodies", timeout=120)
                if not bodies.get("ok"):
                    return self._json({"ok": False, "error": bodies.get("error")})
                plan = call_bridge("read_plan", timeout=120)
                sys.path.insert(0, APP_DIR)
                import mesh_risk
                cfg = load_config()
                gsize = float((cfg.get("settings") or {}).get("ElementSize") or 40.0)

                # 用"用户控制项"给每个体打组名 + 该组的当前尺寸
                grp_of, size_of = {}, {}
                for c in ((plan.get("controls") if plan.get("ok") else None) or []):
                    nm = c.get("name") or ""
                    if nm.startswith("SIZE_FIX_") or nm.startswith("METHOD_"):
                        continue
                    # 去掉 SIZE_ 等前缀，与类别表保持一致
                    g = nm
                    for pre in ("SIZE_", "SWEEP_", "SHELL_"):
                        if g.startswith(pre):
                            g = g[len(pre):]
                            break
                    for b in (c.get("bodies") or []):
                        grp_of.setdefault(b, g)
                    if c.get("size_mm") and g not in size_of:
                        size_of[g] = c.get("size_mm")

                bl = bodies.get("bodies") or []
                for b in bl:
                    b["group"] = grp_of.get(b.get("name")) or u"未分组"
                res = mesh_risk.assess(bl, gsize)
                res["categories"] = mesh_risk.assess_categories(
                    [dict(b, category=b["group"]) for b in bl], size_of, gsize)
                # 每个类别里"尺寸修不了"的体（极薄壁长梁）
                bad_method = []
                for x in res["items"]:
                    if x["level"] == "high" and x["size"] is None:
                        bad_method.append({"name": x["name"],
                                           "group": grp_of.get(x["name"]),
                                           "wall": x["wall"], "aspect": x["aspect"]})
                res["bad_method"] = bad_method
                res["ok"] = True
                res["global_size"] = gsize
                return self._json(res)

            if path == "/api/body_counts":

                _busy = _mech_busy()
                if _busy:
                    return self._json({"ok": False, "busy": True,
                                       "step": _busy,
                                       "error": "Mechanical 正在忙：%s，稍后自动重试" % _busy})
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                _tp_b = target_pid()
                return self._json(cache_get(("body_counts", _tp_b), 90,
                                            lambda: call_bridge("body_counts",
                                                                timeout=180)))

            if path == "/api/diagnose":
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                return self._json(call_bridge("diagnose", timeout=120))

            if path == "/report":
                return self._report_page()

            if path == "/api/mesh_analyze":
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                return self._json(analyze_mesh_messages())

            if path == "/api/failed":
                if not bridge_alive():
                    return self._json({"ok": False, "bridge_offline": True,
                                       "error": "桥接代理未运行（bridge_agent.py）"})
                return self._json(call_bridge("failed_detail", timeout=60))

            if path == "/api/errorlog":
                path_file = _errlog_path()
                text = u""
                if os.path.isfile(path_file):
                    try:
                        with open(path_file, "r", encoding="utf-8") as f:
                            text = f.read()
                    except Exception:
                        pass
                entries = [ln[2:].strip() for ln in text.splitlines() if ln.startswith("- ")]
                return self._json({"ok": True, "file": os.path.basename(path_file),
                                   "count": len(entries), "entries": entries[-60:],
                                   "text": text[-8000:]})

            if path == "/api/progress":
                prog = _read_json(PROGRESS_PATH) or {}
                prog["cancelled"] = os.path.isfile(CANCEL_PATH)
                prog["ok"] = True
                return self._json(prog)

            if path == "/api/ovr/migrate":
                return self._json(migrate_overrides_per_model())

            if path == "/api/debug/thin":
                _cat = ""
                try:
                    _qs = self.path.split("?", 1)[1] if "?" in self.path else ""
                    for _kv in _qs.split("&"):
                        if _kv.startswith("cat="):
                            _cat = unquote(_kv[4:].replace("+", " "))
                except Exception:
                    _cat = ""
                return self._json(debug_thin_detail(_cat))

            if path == "/api/debug/ctrl":
                _out = {"cache": {str(k): (v[0], len(v[1])) for k, v in _CTRL_CACHE.items()},
                        "busy": dict(_CTRL_BUSY)}
                try:
                    _rp = call_bridge("read_plan", timeout=120)
                    _out["plan_ok"] = _rp.get("ok")
                    _out["plan_n"] = len(_rp.get("controls") or [])
                    _out["plan_err"] = (_rp.get("error") or "")[:120]
                except Exception as _e:
                    _out["plan_err"] = str(_e)[:120]
                try:
                    _cn, _age = ctrl_names_cached()
                    _out["cached_n"] = None if _cn is None else len(_cn)
                    _out["cached_age"] = _age
                except Exception as _e:
                    _out["cached_err"] = str(_e)[:120]
                return self._json(_out)

            if path == "/api/controls_validate":
                return self._json(_ctrl_validation())

            if path == "/api/premesh":
                return self._json(premesh_check())

            if path == "/api/model_key":
                try:
                    _k = _model_key()
                    return self._json({"ok": True, "key": _k,
                                       "path": _ovr_path(_k),
                                       "exists": os.path.isfile(_ovr_path(_k))})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:80]})

            if path == "/api/kb":
                return self._json(kb_list())

            if path == "/api/favorites":
                return self._json({"ok": True, "items": favorites_list()})

            if path == "/api/config":
                return self._json({"ok": True, "config": load_config()})

            if path == "/api/reports":
                items = []
                if os.path.isdir(REPORTS_DIR):
                    for fn in sorted(os.listdir(REPORTS_DIR), reverse=True):
                        if fn.endswith(".md"):
                            full = os.path.join(REPORTS_DIR, fn)
                            items.append({"name": fn, "size": os.path.getsize(full),
                                          "mtime": time.strftime(
                                              "%Y-%m-%d %H:%M:%S",
                                              time.localtime(os.path.getmtime(full)))})
                return self._json({"ok": True, "reports": items[:60]})

            if path == "/api/report":
                name = (qs.get("name") or [""])[0]
                full = os.path.join(REPORTS_DIR, os.path.basename(name))
                if not os.path.isfile(full):
                    return self._json({"ok": False, "error": "报告不存在"}, 404)
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    return self._json({"ok": True, "name": name, "text": f.read()})

            return self._json({"ok": False, "error": "not found: " + path}, 404)
        except Exception:
            return self._json({"ok": False, "error": traceback.format_exc()}, 500)


    # 质量指标的判定标准与建议（ANSYS 常用工程经验值）
    QUALITY_RULES = [
        (u"Element Quality", u"单元质量", u"越高越好", 0.20, 0.50, u"lower",
         u"低于 0.2 的单元属畸变，需局部加密、改扫掠或修几何"),
        (u"Aspect Ratio", u"长宽比", u"越低越好", 10.0, 20.0, u"upper",
         u"大于 10 偏细长、大于 20 很差；优先放宽局部尺寸或改扫掠"),
        (u"Jacobian Ratio (MAPDL)", u"雅可比比", u"越接近 1 越好", 10.0, 20.0, u"upper",
         u"过大说明单元扭曲，检查尖角/薄壁处"),
        (u"Jacobian Ratio (Gauss Points)", u"雅可比比(高斯点)", u"越接近 1 越好",
         10.0, 20.0, u"upper", u"同上，高斯点版本更敏感"),
        (u"Jacobian Ratio (Corner Nodes)", u"雅可比比(角节点)", u"越接近 1 越好",
         0.30, 0.10, u"lower", u"过低说明角部单元畸形"),
        (u"Skewness", u"偏斜度", u"越低越好", 0.80, 0.95, u"upper",
         u"大于 0.8 偏差、大于 0.95 不可接受；该区域需加密"),
        (u"Warping Factor", u"翘曲因子", u"越低越好", 0.05, 0.10, u"upper",
         u"薄板常见；建议用扫掠生成规整单元"),
        (u"Maximum Corner Angle", u"最大角", u"四面体理想 60~120°", 165.0, 175.0, u"upper",
         u"接近 180° 是扁平单元，需重划该处"),
        (u"Parallel Deviation", u"平行偏差", u"越低越好", 60.0, 90.0, u"upper",
         u"过大说明对面边不平行，薄壁扫掠尤其要关注"),
    ]

    def _fmt_num(self, v):
        try:
            f = float(v)
            if abs(f) < 1e-3 and f != 0:
                return "%.2e" % f
            if f >= 1000:
                return "%.1f" % f
            return "%.4g" % f
        except Exception:
            return "-"

    def _judge(self, rule, val):
        """返回 (等级, 说明)"""
        try:
            f = float(val)
        except Exception:
            return "muted", u"未取到"
        _, _, _, good, bad, direction, _adv = rule
        if direction == "upper":
            if f <= good:
                return "good", u"合格"
            if f <= bad:
                return "warn", u"偏高"
            return "bad", u"超限"
        else:
            if f >= good:
                return "good", u"合格"
            if f >= bad:
                return "warn", u"偏低"
            return "bad", u"超限"

    def _report_page(self):
        if not bridge_alive():
            return self._html_page(
                "<h1>网格质量报告</h1><p class='alert'>未连接到 Mechanical。"
                "请先在 Mechanical 里点 <b>MeshKit_Check</b> 启动桥接，再刷新本页。</p>")
        try:
            st = call_bridge("status", timeout=20)
            q = {"metrics": list(_quality_items(st).values())}
            if not q["metrics"]:
                q["metrics"] = []
                q["empty"] = True
            qb = _QBODY.get("data") if _QBODY.get("els") == els_of(st) else None
            try:
                hq = hole_quality(st)
            except Exception:
                hq = {"error": "圆孔质量分析失败"}
            counts = call_bridge("body_counts", timeout=25)
            diag = call_bridge("diagnose", timeout=25)
        except Exception as exc:
            return self._html_page("<h1>报告生成失败</h1><p>%s</p>" % exc)

        def fmt(n):
            try:
                return "{:,}".format(int(n))
            except Exception:
                return str(n)

        metrics = {m.get("name"): m for m in (q.get("metrics") or [])}
        parts = []
        parts.append(u"<h1>网格质量报告</h1>")
        parts.append(u"<div class='meta'>%s ｜ 网格状态 <b>%s</b> ｜ "
                     u"规模：单元 %s / 节点 %s ｜ 失败体 %d</div>"
                     % (time.strftime("%Y-%m-%d %H:%M:%S"),
                        st.get("mesh_state") or "-",
                        fmt(q.get("elements") or st.get("elements")),
                        fmt(q.get("nodes") or st.get("nodes")),
                        len(st.get("failed_bodies") or [])))

        # ================= 一、质量结论 =================
        rows, worst = [], []
        for rule in self.QUALITY_RULES:
            nm = rule[0]
            m = metrics.get(nm)
            if not m:
                continue
            vmin, vmax, vavg = m.get("min"), m.get("max"), m.get("avg")
            key_val = vmin if rule[5] == "lower" else vmax
            level, verdict = self._judge(rule, key_val)
            rows.append(
                u"<tr class='%s'><td><b>%s</b><div class='sub'>%s</div></td>"
                u"<td>%s</td><td>%s</td><td>%s</td>"
                u"<td><span class='tag %s'>%s</span></td>"
                u"<td class='tip'>%s</td></tr>"
                % (level, rule[1], nm, self._fmt_num(vmin), self._fmt_num(vavg),
                   self._fmt_num(vmax), level, verdict, rule[6]))
            if level == "bad":
                worst.append((rule[1], key_val, rule[6]))

        # ================= 零、圆孔区域质量（全自动） =================
        if hq:
            parts.append(u"<h2>零、圆孔区域质量（全自动）</h2>")
            cats = hq.get("cats") or []
            if hq.get("error"):
                parts.append(u"<p class='muted'>%s</p>" % hq["error"])
            else:
                offs_h = hq.get("offenders") or []
                parts.append(u"<p class='meta'>分析范围：圆孔标签类别 <b>%s</b>　"
                             u"共 %d 体　不合格 %d 体</p>"
                             % (u"、".join(cats), hq.get("total") or 0, len(offs_h)))
                if offs_h:
                    parts.append(u"<table><tr><th>#</th><th>体</th><th>所属组</th>"
                                 u"<th>平均</th><th>最差</th><th>单元</th>"
                                 u"<th>判定</th><th>怎么改</th></tr>")
                    for i, o in enumerate(offs_h[:30], 1):
                        parts.append(
                            u"<tr class='%s'><td>%d</td><td>%s</td>"
                            u"<td class='sub'>%s</td><td>%s</td><td>%s</td>"
                            u"<td>%s</td><td><span class='tag %s'>%s</span></td>"
                            u"<td class='tip'>%s</td></tr>"
                            % (o.get("level"), i, o.get("name"),
                               groups_of.get(o.get("name"), u"-"),
                               self._fmt_num(o.get("avg")),
                               self._fmt_num(o.get("min")),
                               fmt(o.get("elements")), o.get("level"),
                               o.get("verdict"), hq.get("rule")))
                    parts.append(u"</table>")
                else:
                    parts.append(u"<p class='ok'>圆孔区域全部合格。</p>")

        # ================= 一、不合格体清单（当前指标） =================
        parts.append(u"<h2>一、不合格体清单（按体的真实网格质量）</h2>")
        groups_of = body_group_map()
        cur_metric = (qb or {}).get("current_metric") if isinstance(qb, dict) else None
        focus_n = (qb or {}).get("focus_count") if isinstance(qb, dict) else None
        one = (qb or {}).get("metric") if isinstance(qb, dict) else None
        if one and cur_metric:
            rule_dir = one.get("dir") or "avg_min"
            keyname = u"最差单元" if rule_dir.startswith("avg_min") else u"最大单元"
            offs = one.get("offenders") or []
            scope = (u"（只分析了风险最高的 %d 个区域）" % focus_n) if focus_n else ""
            parts.append(u"<p class='meta'>当前指标：<b>%s</b>　"
                         u"不合格 %d / %d 体（判定依据：该体平均值）%s</p>"
                         % (cur_metric, len(offs), one.get("total") or 0, scope))
            if offs:
                parts.append(u"<table><tr><th>#</th><th>体</th><th>所属组</th>"
                             u"<th>平均</th><th>最差</th><th>单元</th>"
                             u"<th>判定</th><th>怎么改</th></tr>")
                for i, o in enumerate(offs[:30], 1):
                    parts.append(
                        u"<tr class='%s'><td>%d</td><td>%s</td>"
                        u"<td class='sub'>%s</td><td>%s</td><td>%s</td>"
                        u"<td>%s</td><td><span class='tag %s'>%s</span></td>"
                        u"<td class='tip'>%s</td></tr>"
                        % (o.get("level"), i, o.get("name"),
                           groups_of.get(o.get("name"), u"-"),
                           self._fmt_num(o.get("avg")),
                           self._fmt_num(o.get("min") if rule_dir.startswith("avg_min")
                                         else o.get("max")),
                           fmt(o.get("elements")), o.get("level"),
                           o.get("verdict"), one.get("rule")))
                parts.append(u"</table>")
                if len(offs) > 30:
                    parts.append(u"<p class='muted'>还有 %d 个未列出（已按严重程度排序）。</p>"
                                 % (len(offs) - 30))
            else:
                parts.append(u"<p class='ok'>该指标下全部体合格。</p>")
            parts.append(u"<p class='muted'>要看其它指标：在 Mechanical 里 "
                         u"Mesh → Details → <b>Mesh Metric</b> 切换指标（这一步由 Mechanical "
                         u"自己计算），再回本页点「读取当前指标」。"
                         u"程序不会自动切换指标——那样会反复重算导致 Mechanical 内存溢出。</p>")
        else:
            parts.append(u"<p class='alert'>还没有质量数据。请先在 Mechanical 里把 "
                         u"Mesh → Details → <b>Mesh Metric</b> 选一个指标（如 Element Quality），"
                         u"等它算完，再点下面按钮读取。</p>")
        parts.append(u"<button id='qb' onclick='runQB()' style=\"padding:8px 16px;"
                     u"font-size:14px;background:#2563eb;color:#fff;border:0;"
                     u"border-radius:7px;cursor:pointer\">读取当前指标</button>"
                     u"<div id='qbs' class='muted' style='margin-top:8px'></div>"
                     u"<script>"
                     u"async function runQB(){"
                     u"  const b=document.getElementById('qb'), s=document.getElementById('qbs');"
                     u"  b.disabled=true; s.textContent='正在读取（纯读取，不会触发重算）…';"
                     u"  try{"
                     u"    const r=await fetch('/api/quality_bodies',{method:'POST',"
                     u"      headers:{'Content-Type':'application/json'},"
                     u"      body:JSON.stringify({force:true})});"
                     u"    let j=await r.json();"
                     u"    if(j.need_confirm){"
                     u"      if(confirm(j.message)){"
                     u"        const r2=await fetch('/api/quality_bodies',{method:'POST',"
                     u"          headers:{'Content-Type':'application/json'},"
                     u"          body:JSON.stringify({force:true,confirm:true})});"
                     u"        j=await r2.json();"
                     u"      } else { b.disabled=false; s.textContent='已取消'; return; }"
                     u"    }"
                     u"    if(j.ok){ s.textContent='完成，正在刷新…'; location.reload(); }"
                     u"    else { s.textContent='失败：'+(j.error||j.message||''); b.disabled=false; }"
                     u"  }catch(e){ s.textContent='异常：'+e; b.disabled=false; }"
                     u"}"
                     u"</script>")

        # ================= 二、全局指标汇总 =================
        parts.append(u"<h2>二、质量指标汇总（全模型）</h2>")
        if rows:
            parts.append(u"<table><tr><th>指标</th><th>最差</th><th>平均</th>"
                         u"<th>最好/最差端</th><th>判定</th><th>该怎么处理</th></tr>"
                         + u"".join(rows) + u"</table>")
        else:
            parts.append(u"<p class='muted'>未计算（点上面按钮即可，逐体清单会一并给出）。</p>")

        # ================= 三、问题位置 =================
        dbs = (diag.get("bodies") or []) if isinstance(diag, dict) else []
        parts.append(u"<h2>二、问题位置与处理方式</h2>")
        if dbs:
            parts.append(u"<table><tr><th>体</th><th>尺寸(mm)</th><th>形状</th>"
                         u"<th>原因</th><th>建议</th></tr>")
            for b in dbs[:30]:
                shape = u"%s 面 / %s 边" % (b.get("faces") or "-", b.get("edges") or "-")
                reg = u"规整" if b.get("regular") else u"不规则"
                parts.append(u"<tr class='bad'><td>%s</td><td>%s</td>"
                             u"<td class='sub'>%s · %s</td><td>%s</td>"
                             u"<td class='tip'>%s</td></tr>"
                             % (b.get("name"),
                                u"×".join(str(x) for x in (b.get("dims") or [])),
                                shape, reg, b.get("cause"), b.get("fix")))
            parts.append(u"</table>")
        else:
            # 没有失败体时，指出质量最可能差的地方（规模最大的薄壁件）
            rows2 = (counts.get("bodies") or [])[:8]
            if rows2:
                parts.append(u"<p>当前没有失败体。但从规模看，下列体单元最多、"
                             u"通常也是质量最差的区域，建议优先检查：</p>")
                parts.append(u"<table><tr><th>体</th><th>单元</th><th>建议</th></tr>")
                for r in rows2:
                    parts.append(u"<tr><td>%s</td><td>%s</td>"
                                 u"<td class='tip'>检查该处 Aspect Ratio / Skewness；"
                                 u"薄壁优先改扫掠，否则放宽尺寸</td></tr>"
                                 % (r.get("name"), fmt(r.get("elements"))))
                parts.append(u"</table>")
            else:
                parts.append(u"<p class='muted'>无失败体。</p>")

        # ================= 二点五、按体定位（风险排序） =================
        risk_rows = body_risk_rows(30)
        if risk_rows:
            parts.append(u"<h2>三、按体定位：最该检查的体（风险排序）</h2>")
            parts.append(u"<p class='muted'>口径说明：本版本的脚本接口无法直接读【逐个体】的"
                         u"质量数值（读数为 0），因此这里按几何与规模给出风险排序，"
                         u"用于快速定位，不替代机械端逐体指标。</p>")
            parts.append(u"<table><tr><th>#</th><th>体</th><th>所属组</th>"
                         u"<th>壁厚</th><th>长细比</th><th>单元</th>"
                         u"<th>风险点</th><th>建议</th></tr>")
            for i, r in enumerate(risk_rows, 1):
                adv = (u"优先改扫掠+Automatic Thin（形状规整时）" if r["aspect"] > 15
                       else u"局部尺寸降到 2.5×壁厚，或加大去特征")
                parts.append(u"<tr><td>%d</td><td>%s</td><td class='sub'>%s</td>"
                             u"<td>%.1f mm</td><td>%.0f</td><td>%s</td>"
                             u"<td class='tip'>%s</td><td class='tip'>%s</td></tr>"
                             % (i, r["name"], r["group"], r["wall"], r["aspect"],
                                fmt(r["elements"]), r["why"], adv))
            parts.append(u"</table>")

        # ================= 四、规模统计（非质量） =================
        rows3 = (counts.get("bodies") or [])
        if rows3:
            total = counts.get("total_elements") or 1
            parts.append(u"<h2>三、规模统计（仅看数量，不代表质量）</h2>")
            parts.append(u"<table><tr><th>#</th><th>体</th><th>单元</th>"
                         u"<th>节点</th><th>占比</th></tr>")
            for i, r in enumerate(rows3[:20], 1):
                if not r.get("elements"):
                    continue
                parts.append(u"<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td>"
                             u"<td>%.1f%%</td></tr>"
                             % (i, r.get("name"), fmt(r.get("elements")),
                                fmt(r.get("nodes")),
                                100.0 * r["elements"] / total))
            parts.append(u"</table>")

        return self._html_page(u"\n".join(parts))

    def _html_page(self, body):
        html = u"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>MeshKit 质量报告</title><style>
body{background:#0f1319;color:#e8eef7;font:14px/1.6 "Microsoft YaHei",sans-serif;
 margin:0;padding:26px 30px}
h1{font-size:22px;margin:0 0 6px}
h2{font-size:16px;margin:26px 0 8px;color:#9fd2ff;border-left:3px solid #2b6cb0;
 padding-left:9px}
.meta{color:#8fa3bd;font-size:13px;margin-bottom:14px}
table{border-collapse:collapse;width:100%;margin-bottom:10px;font-size:13px}
th{text-align:left;color:#8fa3bd;font-weight:600;border-bottom:1px solid #263242;
 padding:7px 9px;background:#141a22}
td{border-bottom:1px solid #1b2430;padding:6px 9px;vertical-align:top}
tr.bad td{color:#ffb4b4}
tr:hover td{background:#141a22}
.tip{color:#9fb8d4;font-size:12.5px}
.muted{color:#7f8fa6;font-size:12.5px}
.good td{color:#9ff0c4}
.warn td{color:#ffe08a}
.bad td{color:#ffb4b4}
.tag{padding:1px 7px;border-radius:9px;font-size:11.5px}
.tag.good{background:#123a26;color:#7ee2a8}
.tag.warn{background:#3a3212;color:#ffd48a}
.tag.bad{background:#3b1d1d;color:#ffb4b4}
.sub{color:#7f8fa6;font-size:11.5px;font-weight:400}
.alert{color:#ffb4b4;background:#3b1d1d;padding:8px 12px;border-radius:6px}
.ok{color:#7ee2a8;background:#123a26;padding:8px 12px;border-radius:6px}

</style></head><body>__BODY__</body></html>"""
        html = html.replace("__BODY__", body)
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/cancel":
                # 直接写文件下指令，不经过机械端，所以即使它正忙也能立刻生效
                try:
                    with open(CANCEL_PATH, "w", encoding="utf-8") as f:
                        f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
                    return self._json({"ok": True, "message": "已发送停止指令"})
                except Exception as exc:
                    return self._json({"ok": False, "error": str(exc)}, 500)

            if path == "/api/memory/save":
                body = self._body()
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                name = (body.get("name") or "").strip() or ("模型 %s" % time.strftime("%m%d-%H%M"))
                bodies = call_bridge("list_bodies", timeout=120)
                if not bodies.get("ok"):
                    return self._json({"ok": False, "error": bodies.get("error")})
                cats = call_bridge("scan_categories", timeout=120)
                if not cats.get("ok"):
                    return self._json({"ok": False, "error": cats.get("error")})
                sys.path.insert(0, APP_DIR)
                import model_memory
                prof = model_memory.build_profile(bodies.get("bodies") or [],
                                                  cats.get("categories") or [], name)
                model_memory.save_profile(prof)
                return self._json({"ok": True, "profile": prof,
                                   "items": model_memory.list_profiles()})

            if path == "/api/memory/use":
                body = self._body()
                name = (body.get("name") or "").strip()
                cfg = load_config()
                if name:
                    cfg["active_memory"] = name
                    save_config(cfg)
                return self._json({"ok": True, "active": cfg.get("active_memory")})

            if path == "/api/memory/plan":
                body = self._body()
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                sys.path.insert(0, APP_DIR)
                import model_memory
                prof = model_memory.get_profile(body.get("name") or "")
                if not prof:
                    return self._json({"ok": False, "error": "找不到该记忆"})
                bodies = call_bridge("list_bodies", timeout=120)
                if not bodies.get("ok"):
                    return self._json({"ok": False, "error": bodies.get("error")})
                res = model_memory.plan_for(bodies.get("bodies") or [], prof)
                if res.get("ok") and body.get("apply"):
                    plan = [{"category": p["category"], "size": p["size"],
                             "method": p["method"], "bodies": p["bodies"]}
                            for p in res["plan"]]
                    applied = call_bridge("controls_from_plan",
                                          {"plan": plan}, timeout=600)
                    res["applied"] = applied
                return self._json(res)

            if path == "/api/memory/capture":
                body = self._body()
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                name = (body.get("name") or "").strip() or ("我规划 %s" % time.strftime("%m%d-%H%M"))
                plan = call_bridge("read_plan", timeout=180)
                if not plan.get("ok"):
                    return self._json({"ok": False, "error": plan.get("error")})
                bodies = call_bridge("list_bodies", timeout=120)
                if not bodies.get("ok"):
                    return self._json({"ok": False, "error": bodies.get("error")})
                sys.path.insert(0, APP_DIR)
                import model_memory
                prof = model_memory.build_profile_from_plan(
                    bodies.get("bodies") or [], plan.get("controls") or [], name)
                model_memory.save_profile(prof)
                return self._json({"ok": True, "profile": prof,
                                   "controls": plan.get("controls") or [],
                                   "uncovered": plan.get("uncovered") or [],
                                   "items": model_memory.list_profiles()})

            if path == "/api/memory/detail":
                body = self._body()
                sys.path.insert(0, APP_DIR)
                import model_memory
                prof = model_memory.get_profile(body.get("name") or "")
                if not prof:
                    return self._json({"ok": False, "error": "找不到该记忆"})
                cats = [{"name": c.get("name"), "count": c.get("count"),
                         "size": c.get("size"), "method": c.get("method"),
                         "samples": len(c.get("samples") or []),
                         "mixed": c.get("mixed") or [],
                         "source_control": c.get("source_control")}
                        for c in (prof.get("categories") or [])]
                return self._json({"ok": True, "name": prof.get("name"),
                                   "body_count": prof.get("body_count"),
                                   "created": prof.get("created"),
                                   "categories": cats})

            if path == "/api/memory/rename":
                body = self._body()
                sys.path.insert(0, APP_DIR)
                import model_memory
                prof = model_memory.get_profile(body.get("name") or "")
                if not prof:
                    return self._json({"ok": False, "error": "找不到该记忆"})
                old = (body.get("from") or "").strip()
                new = (body.get("to") or "").strip()
                if not old or not new:
                    return self._json({"ok": False, "error": "缺少参数"})
                hit = False
                for c in (prof.get("categories") or []):
                    if (c.get("name") or "") == old:
                        c["name"] = new[:30]
                        hit = True
                if not hit:
                    return self._json({"ok": False, "error": "找不到该类别"})
                model_memory.save_profile(prof)
                return self._json({"ok": True, "categories": [
                    {"name": c.get("name"), "count": c.get("count")}
                    for c in (prof.get("categories") or [])]})

            if path == "/api/memory/delete":
                body = self._body()
                sys.path.insert(0, APP_DIR)
                import model_memory
                ok = model_memory.delete_profile(body.get("name") or "")
                return self._json({"ok": ok, "items": model_memory.list_profiles()})

            if path == "/api/sweep":
                body = self._body()
                cfg = load_config()
                ok = cfg.setdefault("sweep_ok", [])
                cat = (body.get("cat") or "").strip()
                if "mark" in body:
                    if body.get("mark") and cat not in ok:
                        ok.append(cat)
                    elif not body.get("mark") and cat in ok:
                        ok.remove(cat)
                    cfg["sweep_ok"] = ok
                    save_config(cfg)
                    try:
                        _flags_set("sweep_ok", ok)
                        cache_invalidate()
                    except Exception:
                        pass
                return self._json({"ok": True, "sweep_ok": ok})

            if path == "/api/hole_quality":
                body = self._body()
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                st = call_bridge("status", timeout=20)
                return self._json({"ok": True,
                                   "data": hole_quality(st, force=bool(body.get("force")))})

            if path == "/api/quality_bodies":
                body = self._body()
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                st = call_bridge("status", timeout=20)
                # 只读当前指标、且只读高风险区域，没有内存风险，不再需要确认
                data = quality_bodies(st, force=bool(body.get("force")))
                return self._json({"ok": True, "data": data})

            if path == "/api/quality_run":
                body = self._body()
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                st = call_bridge("status", timeout=60)
                names = body.get("names") or _QUALITY_DEFAULT
                if els_of(st) > 800000 and not body.get("confirm"):
                    return self._json({
                        "ok": False, "need_confirm": True,
                        "elements": els_of(st),
                        "message": "网格规模较大（%s 单元），质量计算较慢且占用内存。"
                                   "确认后再执行。" % "{:,}".format(els_of(st))})
                res = _quality_run(st, names)
                return self._json(res)

            if path == "/api/hole":
                body = self._body()
                cfg = load_config()
                cats = cfg.setdefault("hole_cats", [])
                cat = (body.get("cat") or "").strip()
                on = bool(body.get("hole"))
                if on and cat not in cats:
                    cats.append(cat)
                elif (not on) and cat in cats:
                    cats.remove(cat)
                cfg["hole_cats"] = cats
                save_config(cfg)
                try:
                    _flags_set("hole_cats", cats)
                    cache_invalidate()
                    _warm_categories()
                except Exception:
                    pass
                return self._json({"ok": True, "hole_cats": cats})

            if path == "/api/connect":
                body = self._body()
                try:
                    pid = int(body.get("pid") or 0)
                except Exception:
                    pid = 0
                if not pid:
                    return self._json({"ok": False, "error": "缺少 pid"})
                try:
                    _res = connect_instance(pid)
                except Exception as _e:
                    _res = {"ok": False, "error": "exception: %s" % str(_e)[:200]}
                try:
                    with open(os.path.join(APP_DIR, "ipc", "connect.log"), "a",
                              encoding="utf-8") as _f:
                        _f.write("%s pid=%s -> %s\n" % (
                            time.strftime("%H:%M:%S"), pid,
                            {k: _res.get(k) for k in ("ok", "connected", "activated", "clipboard", "error")}))
                except Exception:
                    pass
                return self._json(_res)

            if path == "/api/controls_dedupe":
                _r = call_bridge("dedupe_controls", {}, timeout=300)
                try:
                    cache_invalidate()
                except Exception:
                    pass
                return self._json(_r)

            if path == "/api/bridge/reset":
                _n = reset_bridge_locks()
                try:
                    cache_invalidate()
                except Exception:
                    pass
                return self._json({"ok": True, "released": _n,
                                   "message": "已重置桥接锁，可重新操作"})

            if path == "/api/agent/reload":
                body = self._body()
                try:
                    _pid_r = int(body.get("pid") or target_pid())
                except Exception:
                    _pid_r = target_pid()
                if not _pid_r:
                    return self._json({"ok": False, "error": "没有目标实例"})
                # 先移除注册文件，强制走一次真正的 Ctrl+F5 重载
                try:
                    _rf = os.path.join(IPC_DIR, "agents", "%d.json" % int(_pid_r))
                    if os.path.isfile(_rf):
                        os.remove(_rf)
                except Exception:
                    pass
                _r = _mech_auto_connect(_pid_r)
                return self._json({"ok": bool(_r.get("ok")),
                                   "pid": _pid_r, "stage": _r.get("stage"),
                                   "error": _r.get("error"),
                                   "message": "已重新加载代理" if _r.get("ok") else "自动加载未成功，请手动 Ctrl+F5"})

            if path == "/api/agent/select":
                body = self._body()
                try:
                    _pid_sel = int(body.get("pid"))
                    _TARGET["pid"] = _pid_sel
                    _target_save(_pid_sel)
                except Exception:
                    return self._json({"ok": False, "error": "pid 无效"})
                return self._json({"ok": True, "target": _TARGET["pid"],
                                   "agents": agents_list()})

            if path == "/api/lock":
                body = self._body()
                cfg = load_config()
                cats = cfg.setdefault("locked_cats", [])
                cat = (body.get("cat") or "").strip()
                on = bool(body.get("locked"))
                if on and cat not in cats:
                    cats.append(cat)
                elif (not on) and cat in cats:
                    cats.remove(cat)
                cfg["locked_cats"] = cats
                save_config(cfg)
                try:
                    _flags_set("locked_cats", cats)
                    cache_invalidate()
                    _warm_categories()
                except Exception:
                    pass
                return self._json({"ok": True, "locked_cats": cats})

            if path == "/api/ungroup":
                body = self._body()
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                names = body.get("names") or ["*"]
                r = call_bridge("ungroup_folders", {"names": names}, timeout=1800)
                return self._json(r)

            if path == "/api/group_restore":
                sys.path.insert(0, APP_DIR)
                import geo_recognize
                return self._json(geo_recognize.restore_last())

            if path == "/api/group_delete":
                body = self._body()
                sys.path.insert(0, APP_DIR)
                import geo_recognize
                lb = call_bridge("list_bodies", timeout=300)
                rec = geo_recognize.recognize(lb.get("bodies") or [], kb_extra_rules())
                r = geo_recognize.delete_group(body.get("name"),
                                               rec.get("groups") or {})
                return self._json(r)

            if path == "/api/group_rename":
                body = self._body()
                sys.path.insert(0, APP_DIR)
                import geo_recognize
                lb = call_bridge("list_bodies", timeout=300)
                rec = geo_recognize.recognize(lb.get("bodies") or [], kb_extra_rules())
                r = geo_recognize.rename_group(body.get("from"), body.get("to"),
                                               rec.get("groups") or {})
                return self._json(r)

            if path == "/api/group_new":
                body = self._body()
                sys.path.insert(0, APP_DIR)
                import geo_recognize
                return self._json(geo_recognize.add_group(body.get("name")))

            if path == "/api/body_move":
                body = self._body()
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                name = (body.get("body") or "").strip()
                to = (body.get("to") or "").strip()
                if not name or not to:
                    return self._json({"ok": False, "error": "参数不完整"})
                bodies = _bodies_once()
                bid = None
                for b in bodies:
                    if b.get("name") == name:
                        bid = b.get("id")
                        break
                if bid is None:
                    return self._json({"ok": False, "error": "找不到该体"})
                ov, _src = _ovr_load()
                ov[str(bid)] = to
                _ovr_save(ov)
                return self._json({"ok": True, "body": name, "id": bid, "to": to,
                                   "overrides": len(ov)})

            if path == "/api/body_move_reset":
                sys.path.insert(0, APP_DIR)
                import geo_recognize
                geo_recognize.clear_overrides()
                _ovr_save({})
                cache_invalidate()
                return self._json({"ok": True})

            if path == "/api/regroup":
                body = self._body()
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                return self._json(regroup_by_name(dry=bool(body.get("dry"))))

            if path == "/api/scan_apply":
                body = self._body()
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                try:
                    _guard = ensure_target_front(strict=True)
                except Exception:
                    _guard = {"ok": True}
                if not _guard.get("ok"):
                    return self._json({
                        "ok": False, "blocked": True,
                        "error": _guard.get("error") or "目标实例不在前台",
                        "front_pid": _guard.get("front_after"),
                        "target_pid": target_pid()})
                # 1) 纯几何识别（不看名字）
                lb = call_bridge("list_bodies", timeout=300)
                if not lb.get("ok"):
                    return self._json({"ok": False, "error": lb.get("error") or "读不到体"})
                sys.path.insert(0, APP_DIR)
                import geo_recognize
                # 与「① 扫描」用同一来源，避免两边不一致
                _sg = scan_groups()
                groups = dict(_sg.get("groups") or {})
                if not groups:
                    return self._json({"ok": False, "error": "分组结果为空"})

                # 剔除成员名在模型里不存在的组（知识包残留旧体名等）
                _allnames = set(b.get("name") for b in (lb.get("bodies") or []))
                _dropped = {}
                _clean = {}
                for _g, _ns in groups.items():
                    _keep = [n for n in _ns if n in _allnames]
                    if _keep:
                        if len(_keep) != len(_ns):
                            _dropped[_g] = len(_ns) - len(_keep)
                        _clean[_g] = _keep
                groups = _clean
                if not groups:
                    return self._json({"ok": False, "error": "分组结果为空（成员都不存在）"})

                out = {"ok": True, "renamed": 0, "geom_groups": 0, "errors": [],
                       "group_count": len(groups),
                       "total": _sg.get("total") or len(_allnames),
                       "source": _sg.get("source")}
                if _dropped:
                    out["dropped_members"] = _dropped
                    out["errors"].append(
                        "以下分组的部分成员在模型里不存在，已忽略：%s"
                        % ", ".join("%s(%d)" % (k, v)
                                    for k, v in list(_dropped.items())[:6]))
            
                # 2) 重命名：部位_序号（已符合命名的不动）
                if body.get("rename"):
                    items = []
                    for g, names in groups.items():
                        # 「其他」「未分组」里的体不改名（用户要求）
                        if g in ("其他", "未分组"):
                            continue
                        for n in names:
                            items.append({"body": n, "group": g})
                    rr = call_bridge("rename_bodies", {"mode": "prefix", "items": items},
                                     timeout=1800)
                    out["renamed"] = rr.get("count") or 0
                    out["rename_skipped"] = len(rr.get("skipped") or [])
                    if rr.get("failed"):
                        out["errors"].extend(rr["failed"][:5])
            
                    # 名字变了 → 重新扫描一次，保证分组用的是新名字（list_bodies 只调一次）
                    try:
                        _sg2 = scan_groups()
                        if _sg2.get("groups"):
                            _lb2 = call_bridge("list_bodies", timeout=300)
                            _names2 = set(b.get("name") for b in (_lb2.get("bodies") or []))
                            groups = {g: [n for n in ns if n in _names2]
                                      for g, ns in _sg2["groups"].items()}
                            groups = {g: ns for g, ns in groups.items() if ns}
                    except Exception:
                        pass
            
                # 解散前先记下当前分组（失败可回滚）
                _before = _snapshot_groups()
                out["snapshot_groups"] = len(_before)
                try:
                    call_bridge("ungroup_folders", {"names": ["*"]},
                                timeout=1800)
                    out["cleared"] = True
                except Exception as e:
                    out["errors"].append("清空旧分组失败: %s" % str(e)[:80])

                # 3) 在 Geometry 树里建分组（已存在的会自动跳过）
                if body.get("geom_group", True):
                    items = [{"group": g, "bodies": ns} for g, ns in groups.items() if ns]
                    gg = call_bridge("group_geometry", {"items": items}, timeout=3600)
                    out["geom_groups"] = len(gg.get("groups") or [])
                    out["geom_skipped"] = len(gg.get("skipped") or [])
                    if gg.get("failed"):
                        out["errors"].extend(gg["failed"][:5])

                    # 建完核对：缺组/成员不一致 → 重试一次 → 仍不行就回滚
                    _failed_groups = set()
                    for _m in (gg.get("failed") or []):
                        try:
                            _failed_groups.add(str(_m).split(":")[0].strip())
                        except Exception:
                            pass
                    _want = {g: ns for g, ns in groups.items()
                             if ns and g not in _failed_groups}
                    _bad = _verify_groups(_want)
                    if _bad:
                        out["verify_failed"] = _bad[:8]
                        try:
                            _items = [{"group": g, "bodies": ns}
                                      for g, ns in groups.items() if ns]
                            call_bridge("group_geometry", {"items": _items},
                                        timeout=3600)
                            _bad2 = _verify_groups(
                                {g: ns for g, ns in groups.items() if ns})
                        except Exception:
                            _bad2 = _bad
                        if _bad2:
                            out["errors"].append(
                                "分组重建未通过校验，已尝试回滚到原分组")
                            _rb = _restore_groups(_before)
                            out["rollback"] = _rb
                        else:
                            out["verify_failed"] = []
                            out["retried"] = True
            
                # 4) 自动保存（防崩溃丢结果）
                try:
                    cache_invalidate()
                except Exception:
                    pass
                out["saved"] = safe_save("scan_apply")
                return self._json(out)

            if path == "/api/op":
                body = self._body()
                op = body.get("op") or ""
                payload = body.get("payload") or {}
                is_async = bool(body.get("async"))
                _ran_on = None
                if op in MUTATING_OPS:
                    try:
                        _guard = ensure_target_front(strict=True)
                    except Exception:
                        _guard = {"ok": True}
                    if not _guard.get("ok"):
                        return self._json({
                            "ok": False, "blocked": True,
                            "error": _guard.get("error") or "目标实例不在前台",
                            "front_pid": _guard.get("front_after"),
                            "target_pid": target_pid()})
                    try:
                        _tp = target_pid()
                        _pn, _pp = _project_of_pid(_tp)
                        _ran_on = {"pid": _tp, "project": _pn}
                    except Exception:
                        _ran_on = {"pid": target_pid()}
                if not bridge_alive():
                    return self._json({"ok": False, "bridge_offline": True,
                                       "error": "桥接代理未运行（bridge_agent.py）"})

                def _run_op_sync():
                    # apply：先把"类别->体"用 Geometry 分组翻译成 plan，再用可靠接口建控制项
                    if op == "apply":
                        try:
                            _sz = (payload or {}).get("sizes") or {}
                            _mtd = (payload or {}).get("methods") or {}
                            _div = (payload or {}).get("divisions") or {}
                            if _sz or _mtd:
                                _gg = call_bridge("geom_groups", timeout=180)
                                _byg = {}
                                for _g in (_gg.get("groups") or []):
                                    _byg[_g.get("name")] = _g.get("bodies") or []
                                # 已有控制项的类别 -> 改原控制项；没有的 -> 才新建
                                _existing = set()
                                try:
                                    _rp = call_bridge("read_plan", timeout=120)
                                    if not _rp.get("ok"):
                                        return {
                                            "ok": False, "busy": bool(_rp.get("busy")),
                                            "error": "读不到现有控制项清单，已中止（避免建出重复/空控制项）：%s"
                                                     % (_rp.get("error") or "")}
                                    for _c in (_rp.get("controls") or []):
                                        _nm = (_c.get("name") or "").strip()
                                        for _pre in ("SIZE_FIX_", "SIZE_",
                                                     "METHOD_", "SWEEP_", "SHELL_"):
                                            if _nm.startswith(_pre):
                                                _nm = _nm[len(_pre):]
                                                break
                                        if _nm:
                                            _existing.add(_nm)
                                except Exception:
                                    pass

                                _names = set(_sz.keys()) | set(_mtd.keys())
                                _plan = []
                                for _n in _names:
                                    if _n in _existing:
                                        continue          # 已存在 -> 走 apply 修改
                                    _bs = _byg.get(_n) or []
                                    if not _bs:
                                        continue
                                    _item = {"category": _n,
                                             "bodies": _bs,
                                             "size": _sz.get(_n),
                                             "method": _mtd.get(_n) or "Automatic"}
                                    if _div.get(_n):
                                        _item["divisions"] = _div.get(_n)
                                    _plan.append(_item)
                                if _plan:
                                    _cr = call_bridge("controls_from_plan",
                                                      {"plan": _plan}, timeout=3600)
                                    _payload2 = dict(payload or {})
                                    _payload2["sizes"] = dict((k, v) for k, v in _sz.items()
                                                              if k in _existing)
                                    _payload2["methods"] = dict((k, v) for k, v in _mtd.items()
                                                                if k in _existing)
                                    _res2 = call_bridge("apply", _payload2, timeout=600)
                                    _res2["plan_created"] = len(_cr.get("created") or [])
                                    _res2.setdefault("settings", [])
                                    _res2.setdefault("errors", [])
                                    _res2["errors"].extend((_cr.get("errors") or [])[:5])
                                    _res2["sizes"] = list(_sz.keys())
                                    _res2["saved"] = safe_save("apply")
                                    if _ran_on and isinstance(_res2, dict):
                                        _res2["ran_on"] = _ran_on
                                    try:
                                        cache_invalidate()
                                    except Exception:
                                        pass
                                    return _res2
                        except Exception:
                            pass

                    # mesh 之前先记下"当前失败集合"（用来对比效果）
                    if op == "mesh":
                        try:
                            st0 = call_bridge("status", timeout=20)
                            _remember_failed(st0)
                        except Exception:
                            pass

                    res = call_bridge(op, payload)

                    # 记住这次应用的动作（供经验库归因）
                    if op == "apply" and res.get("ok"):
                        try:
                            favorite_bump_uses(payload.get("sizes") or {},
                                               payload.get("methods") or {})
                        except Exception:
                            pass
                        act = {"type": "apply"}
                        if payload.get("sizes"):
                            act["sizes"] = payload.get("sizes")
                        if payload.get("methods"):
                            act["methods"] = payload.get("methods")
                        if payload.get("settings"):
                            act["settings"] = payload.get("settings")
                        _EXP["last_action"] = act
                        _EXP["last_action_ts"] = time.time()

                    # mesh 之后：对比并记录经验
                    if op == "mesh" and res.get("ok"):
                        try:
                            new_failed = [b.get("name") for b in (res.get("failed_bodies") or [])]
                            info = _record_mesh_experience(new_failed)
                            res["experience"] = info
                            _EXP["failed_before"] = set(new_failed)
                        except Exception:
                            pass
                    if _ran_on and isinstance(res, dict):
                        res["ran_on"] = _ran_on
                    try:
                        if op in MUTATING_OPS or op in ("mesh", "apply", "fix_failed"):
                            cache_invalidate()
                    except Exception:
                        pass
                    return res

                if is_async:
                    jid = _start_async_job(op, _run_op_sync)
                    return self._json({"ok": True, "async": True, "job_id": jid,
                                       "op": op, "status": "running"})
                return self._json(_run_op_sync())

            if path == "/api/kb/capture":
                body = self._body()
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                return self._json(kb_capture(body.get("name") or ""))

            if path == "/api/kb/apply":
                body = self._body()
                return self._json(kb_apply_pack(body.get("name") or ""))

            if path == "/api/kb/toggle":
                body = self._body()
                return self._json(kb_toggle(body.get("name") or "",
                                            bool(body.get("on"))))

            if path == "/api/kb/delete":
                body = self._body()
                return self._json(kb_delete(body.get("name") or ""))

            if path == "/api/favorites/save":
                body = self._body()
                r = favorite_save(body.get("name") or "",
                                  body.get("sizes") or {}, body.get("methods") or {})
                r["items"] = favorites_list()
                return self._json(r)

            if path == "/api/favorites/delete":
                body = self._body()
                ok = favorite_delete(body.get("name") or "")
                return self._json({"ok": ok, "items": favorites_list()})

            if path == "/api/favorites/rename":
                body = self._body()
                ok = favorite_rename(body.get("from") or body.get("name") or "",
                                     body.get("to") or body.get("new") or "")
                return self._json({"ok": ok, "items": favorites_list()})

            if path == "/api/thin":
                body = self._body()
                cat = (body.get("cat") or "").strip()
                on = bool(body.get("thin"))
                cfg = load_config()
                d, _ok = _flags_load()
                cats = list(d["thin_cats"] if "thin_cats" in d
                            else _flags_get(cfg, "thin_cats"))
                off = list(d["thin_off"] if "thin_off" in d
                           else _flags_get(cfg, "thin_off"))
                methods = cfg.setdefault("methods", {})
                divs = cfg.setdefault("thin_divisions", {})
                if on:
                    if cat and cat not in cats:
                        cats.append(cat)
                    off = [x for x in off if x != cat]
                    methods[cat] = "Sweep"
                    divs[cat] = 2
                else:
                    cats = [x for x in cats if x != cat]
                    if cat and cat not in off:
                        off.append(cat)
                    methods[cat] = "Automatic"
                    divs.pop(cat, None)
                cfg["methods"] = methods
                cfg["thin_divisions"] = divs
                save_config(cfg)
                d["thin_cats"] = cats
                d["thin_off"] = off
                _flags_save(d)
                try:
                    cache_invalidate()
                    _warm_categories()
                except Exception:
                    pass
                return self._json({"ok": True, "thin_cats": cats,
                                   "thin_off": off,
                                   "methods": methods, "divisions": divs})

            if path == "/api/clear":
                body = self._body()
                if not bridge_alive():
                    return self._json({"ok": False, "bridge_offline": True,
                                       "error": "桥接代理未运行"})
                res = call_bridge("clear_all", timeout=120)
                if res.get("ok"):
                    cfg = load_config()
                    cfg["sizes"] = dict(DEFAULT_SIZES)
                    cfg["methods"] = {}
                    cfg["advanced"] = {}
                    save_config(cfg)
                    res["config_reset"] = True
                    try:
                        cache_invalidate()
                    except Exception:
                        pass
                return self._json(res)

            if path == "/api/ai":
                body = self._body()
                status = body.get("status")
                if status is None:
                    if not bridge_alive():
                        status = {"ok": False}
                    else:
                        status = call_bridge("status", timeout=30)
                quality = body.get("quality") or ""
                sys.path.insert(0, APP_DIR)
                import ai_engine
                return self._json({"ok": True,
                                   "analysis": ai_engine.analyze(status, quality, APP_DIR)})

            if path == "/api/params":
                body = self._body()
                if not bridge_alive():
                    return self._json({"ok": False, "bridge_offline": True,
                                       "error": "桥接代理未运行"})
                cat = body.get("category") or ""
                api = body.get("api") or ""
                value = body.get("value")
                if not api:
                    return self._json({"ok": False, "error": "缺少 api"})
                res = call_bridge("set_param",
                                  {"category": cat, "api": api, "value": value},
                                  timeout=60)
                if res.get("ok"):
                    cfg = load_config()
                    adv = cfg.setdefault("advanced", {})
                    adv.setdefault(cat, {})[api] = value
                    save_config(cfg)
                return self._json(res)

            if path == "/api/body_fix":
                body = self._body()
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                return self._json(call_bridge("fix_body", body, timeout=300))

            if path == "/api/config":
                body = self._body()
                cfg = load_config()
                cfg.update({k: v for k, v in body.items()
                            if k in ("sizes", "methods", "settings", "budget_nodes")})
                if "llm" in body and isinstance(body["llm"], dict):
                    cfg["llm"].update(body["llm"])
                save_config(cfg)
                try:
                    cache_invalidate()
                except Exception:
                    pass
                return self._json({"ok": True, "config": cfg})

            return self._json({"ok": False, "error": "not found"}, 404)
        except Exception:
            return self._json({"ok": False, "error": traceback.format_exc()}, 500)

    # ---------- 静态文件 ----------
    def _static(self, rel: str):
        rel = rel.replace("\\", "/").lstrip("/")
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
            return self._json({"ok": False, "error": "static not found: " + rel}, 404)
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".json": "application/json; charset=utf-8",
        }.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)


def main():
    os.makedirs(IPC_DIR, exist_ok=True)
    os.makedirs(STATIC_DIR, exist_ok=True)
    if not os.path.isfile(CFG_PATH):
        save_config(load_config())

    # 后台监视器：记录网格失败体
    try:
        t = threading.Thread(target=monitor_loop, daemon=True)
        t.start()
    except Exception:
        pass

    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print("=" * 66)
    print("  MeshKit AI  —  ANSYS 网格智能助手")
    print("=" * 66)
    print(f"  界面地址: {url}")
    print(f"  工程目录: {ROOT}")
    print()
    print("  提示：先打开 Mechanical，在 Scripting 面板运行")
    print('        execfile("D:/ai/meshkit/app/bridge_agent.py")')
    print("=" * 66)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


def _boot_warm():
    time.sleep(5)
    try:
        _warm_cache()
    except Exception:
        pass


# 不在启动时自动预热：Mechanical 刚起来时连续命令容易引起闪退
# （需要时页面会自行触发首次读取）


if __name__ == "__main__":
    main()
