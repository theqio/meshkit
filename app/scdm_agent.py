# -*- coding: utf-8 -*-
"""
SpaceClaim 只读体检代理（IronPython 2.7，在 SpaceClaim 的 Scripting 面板里运行）

架构与 Mechanical 的 bridge_agent 相同：文件队列通信
    app/ipc/scdm_cmd.json      服务写 → 本代理取走
    app/ipc/scdm_result.json   本代理写 → 服务读取（按 seq 匹配）
    app/ipc/scdm_state.json    心跳（含版本/文档名）

当前只做"只读"操作，绝不修改模型：
    ping        连通性
    probe       环境与 API 能力探测（文档、组件、体、可用属性）
    list_parts  列出装配体里的组件与体（名称/深度/体积/包围盒）
    symmetry    对称/位置检查（同尺寸配对 + 镜像期望位置）
    gaps        接触面间隙检查（组件之间的最近距离，仅报告）
    interference 干涉检查（仅报告：组件对 + 干涉体积，用临时副本做布尔，不改原模型）

加载命令（由页面「加载命令」按钮生成）：
    import os;os.environ["MESHKIT_ROOT"]=r"<安装目录>";execfile(r"<安装目录>/app/scdm_agent.py")
"""

import os
import sys
import json
import time
import traceback

SCDM_AGENT_VERSION = u"2026-12-02.1"


# ---------------------------------------------------------------- 路径
def _resolve_root():
    cands = []
    try:
        env = os.environ.get("MESHKIT_ROOT")
        if env:
            cands.append(env)
    except Exception:
        pass
    try:
        cands.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    except Exception:
        pass
    try:
        cands.append(os.getcwd())
    except Exception:
        pass
    for c in cands:
        try:
            if c and os.path.isdir(os.path.join(c, "app")):
                return c
        except Exception:
            continue
    return r"D:\ai\meshkit"


ROOT = _resolve_root()
APP_DIR = os.path.join(ROOT, "app")
IPC_DIR = os.path.join(APP_DIR, "ipc")
CMD_PATH = os.path.join(IPC_DIR, "scdm_cmd.json")
RES_PATH = os.path.join(IPC_DIR, "scdm_result.json")
STATE_PATH = os.path.join(IPC_DIR, "scdm_state.json")

try:
    os.makedirs(IPC_DIR)
except Exception:
    pass

_keep = globals().get("_keep") or []
_busy = globals().get("_busy") or []


# ---------------------------------------------------------------- 工具
def _read_json(p):
    try:
        with open(p, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(p, obj):
    try:
        with open(p, "w") as f:
            json.dump(obj, f)
    except Exception:
        pass


def _tail(s, n=600):
    try:
        return (s or u"")[-n:]
    except Exception:
        return u""


def _txt(v):
    try:
        return u"%s" % v
    except Exception:
        return u""


def _state(extra=None):
    d = {"pid": os.getpid(), "version": SCDM_AGENT_VERSION,
         "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        from SpaceClaim.Api.V22 import Window
        w = Window.ActiveWindow
        doc = w.Document if w is not None else None
        if doc is not None:
            d["doc"] = _txt(getattr(doc, "Name", u""))
            try:
                d["doc_type"] = _txt(doc.GetType().Name)
            except Exception:
                pass
    except Exception:
        pass
    if extra:
        d.update(extra)
    _write_json(STATE_PATH, d)
    return d


# ---------------------------------------------------------------- SCDM 访问
def _api():
    """返回 SpaceClaim API 模块（V22），失败抛异常"""
    import clr  # noqa
    mod = __import__("SpaceClaim.Api.V22", {}, {}, ["*"])
    return mod


def _doc():
    api = _api()
    w = api.Window.ActiveWindow
    if w is None:
        raise RuntimeError(u"没有活动窗口（请先打开一个 SpaceClaim 文档）")
    doc = w.Document
    if doc is None:
        raise RuntimeError(u"当前窗口没有文档")
    return doc


def _main_part():
    doc = _doc()
    part = getattr(doc, "MainPart", None)
    if part is None:
        raise RuntimeError(u"取不到 MainPart")
    return part


def _iter_components(part, max_depth=6):
    """深度优先遍历组件，返回 [(depth, component)]"""
    out = []

    def walk(node, depth):
        if depth > max_depth:
            return
        try:
            children = list(node.Components or [])
        except Exception:
            children = []
        for c in children:
            out.append((depth, c))
            walk(c, depth + 1)

    walk(part, 0)
    return out


def _bodies_of(comp):
    """组件下的体：优先 GetAllBodies，其次 Bodies，其次 Shapes"""
    for attr in ("GetAllBodies", "Bodies", "Shapes"):
        try:
            v = getattr(comp, attr)
            if callable(v):
                v = v()
            if v is None:
                continue
            lst = list(v)
            if lst:
                return lst
        except Exception:
            continue
    return []


def _body_info(b):
    """体的可读信息：尽量用可用属性，取不到的返回 None"""
    info = {"name": _txt(getattr(b, "Name", u"?"))}
    for key in ("Volume", "Mass", "Density"):
        try:
            info[key.lower()] = float(getattr(b, key))
        except Exception:
            info[key.lower()] = None
    # 包围盒：SCDM 里常见是 Shape / BoundingBox / Bounds
    bb = None
    for chain in (("Shape", "BoundingBox"), ("Shape", "Bounds"),
                  ("BoundingBox",), ("Bounds",)):
        try:
            obj = b
            for step in chain:
                obj = getattr(obj, step)
            if obj is None:
                continue
            # RECT 风格：MinPoint/MaxPoint 或 Left/Right...
            mn = mx = None
            for a, c in (("MinPoint", "MaxPoint"), ("Min", "Max"),
                         ("Start", "End")):
                if hasattr(obj, a) and hasattr(obj, c):
                    mn, mx = getattr(obj, a), getattr(obj, c)
                    break
            if mn is not None:
                bb = {
                    "min": [float(getattr(mn, k)) for k in ("X", "Y", "Z")],
                    "max": [float(getattr(mx, k)) for k in ("X", "Y", "Z")],
                }
                break
        except Exception:
            continue
    info["bbox"] = bb
    try:
        info["type"] = _txt(b.GetType().Name)
    except Exception:
        pass
    return info


def op_ping(payload):
    return {"ok": True, "version": SCDM_AGENT_VERSION, "pid": os.getpid(),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S")}


def op_probe(payload):
    """环境能力探测：返回文档信息 + 组件/体数量 + 可用属性样例"""
    out = {"ok": True, "version": SCDM_AGENT_VERSION, "pid": os.getpid(),
           "root": ROOT}
    try:
        api = _api()
        out["api"] = "SpaceClaim.Api.V22"
        for tname in ("Interference", "Tessellation", "DesignBody", "Component",
                      "Body", "Face", "Window", "Document"):
            try:
                out["has_" + tname] = hasattr(api, tname)
            except Exception:
                out["has_" + tname] = False
    except Exception:
        out["api_error"] = _tail(traceback.format_exc())
        return out

    try:
        doc = _doc()
        out["doc_name"] = _txt(getattr(doc, "Name", u""))
        try:
            out["doc_type"] = _txt(doc.GetType().Name)
        except Exception:
            pass
    except Exception:
        out["doc_error"] = _tail(traceback.format_exc())
        return out

    try:
        part = _main_part()
        comps = _iter_components(part)
        out["component_count"] = len(comps)
        out["top_level"] = len(list(getattr(part, "Components", []) or []))
        total_bodies = 0
        for depth, c in comps:
            total_bodies += len(_bodies_of(c))
        out["body_total"] = total_bodies
        # 样例：前 3 个组件的属性与体
        samples = []
        for depth, c in comps[:3]:
            item = {"depth": depth, "name": _txt(getattr(c, "Name", u"?"))}
            try:
                item["type"] = _txt(c.GetType().Name)
            except Exception:
                pass
            bs = _bodies_of(c)
            item["bodies"] = len(bs)
            if bs:
                item["body_sample"] = _body_info(bs[0])
                try:
                    attrs = [a for a in dir(bs[0]) if not a.startswith("_")][:40]
                    item["body_attrs"] = attrs
                except Exception:
                    pass
            samples.append(item)
        out["samples"] = samples
    except Exception:
        out["model_error"] = _tail(traceback.format_exc())
    return out


def op_list_parts(payload):
    """列出装配体：组件树 + 每个组件的体（名称/体积/包围盒）"""
    out = {"ok": True, "components": [], "body_total": 0}
    try:
        part = _main_part()
        comps = _iter_components(part)
        for depth, c in comps:
            bs = _bodies_of(c)
            item = {"depth": depth,
                    "name": _txt(getattr(c, "Name", u"?")),
                    "bodies": [_body_info(b) for b in bs]}
            out["body_total"] += len(bs)
            out["components"].append(item)
        out["ok"] = True
    except Exception:
        out["ok"] = False
        out["error"] = _tail(traceback.format_exc())
    return out


def op_symmetry(payload):
    """对称/位置检查：按"尺寸签名"配对，判断质心是否关于某平面镜像对称
       payload: {"tol_mm": 1.0, "whitelist": ["其他", ...]}
    """
    payload = payload or {}
    tol = float(payload.get("tol_mm") or 1.0)
    white = [u"%s" % w for w in (payload.get("whitelist") or [])]
    out = {"ok": True, "groups": [], "issues": [], "checked": 0, "skipped": 0}
    try:
        part = _main_part()
        comps = _iter_components(part)
        items = []
        for depth, c in comps:
            name = _txt(getattr(c, "Name", u"?"))
            if any(w and w in name for w in white):
                out["skipped"] += 1
                continue
            bs = _bodies_of(c)
            if len(bs) != 1:
                out["skipped"] += 1
                continue
            bi = _body_info(bs[0])
            bb = bi.get("bbox")
            if not bb:
                out["skipped"] += 1
                continue
            mn, mx = bb["min"], bb["max"]
            dims = [round(mx[i] - mn[i], 2) for i in range(3)]
            cen = [round((mx[i] + mn[i]) / 2.0, 2) for i in range(3)]
            items.append({"name": name, "dims": dims, "center": cen,
                          "volume": bi.get("volume")})
        # 按尺寸签名分组（±0.5mm 视为同尺寸）
        groups = {}
        for it in items:
            key = tuple(sorted(it["dims"]))
            hit = None
            for k in groups:
                if all(abs(a - b) <= 0.5 for a, b in zip(key, k)):
                    hit = k
                    break
            groups.setdefault(hit or key, []).append(it)
        for key, arr in groups.items():
            out["groups"].append({"dims": [round(x, 1) for x in key],
                                  "count": len(arr),
                                  "names": [a["name"] for a in arr][:20]})
            if len(arr) < 2:
                continue
            # 对每个轴，检查成对质心是否关于该组中位面镜像对称
            for axis in (0, 1, 2):
                vals = sorted(a["center"][axis] for a in arr)
                mid = (vals[0] + vals[-1]) / 2.0
                used = set()
                for i, a in enumerate(arr):
                    if i in used:
                        continue
                    ca = a["center"][axis]
                    expect = 2 * mid - ca
                    best, bd = None, None
                    for j, b in enumerate(arr):
                        if j == i or j in used:
                            continue
                        d = abs(b["center"][axis] - expect)
                        if bd is None or d < bd:
                            best, bd = j, d
                    if best is None:
                        continue
                    used.add(i)
                    used.add(best)
                    out["checked"] += 1
                    if bd is not None and bd > tol:
                        out["issues"].append({
                            "type": "asymmetry", "axis": "XYZ"[axis],
                            "a": a["name"], "b": arr[best]["name"],
                            "expected": round(expect, 2),
                            "actual": round(arr[best]["center"][axis], 2),
                            "deviation": round(bd, 2),
                            "suggest_shift": round(expect - arr[best]["center"][axis], 2),
                        })
    except Exception:
        out["ok"] = False
        out["error"] = _tail(traceback.format_exc())
    return out


def op_gaps(payload):
    """接触间隙检查（只报告）：组件包围盒邻近 + 建议人工确认
       payload: {"max_gap_mm": 5.0}
    """
    payload = payload or {}
    max_gap = float(payload.get("max_gap_mm") or 5.0)
    out = {"ok": True, "pairs": [], "count": 0}
    try:
        part = _main_part()
        comps = _iter_components(part)
        boxes = []
        for depth, c in comps:
            bs = _bodies_of(c)
            if len(bs) != 1:
                continue
            bi = _body_info(bs[0])
            if not bi.get("bbox"):
                continue
            boxes.append({"name": _txt(getattr(c, "Name", u"?")),
                          "min": bi["bbox"]["min"], "max": bi["bbox"]["max"]})
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                gap = []
                overlap = []
                for k in range(3):
                    g = max(a["min"][k] - b["max"][k], b["min"][k] - a["max"][k])
                    gap.append(g)
                if max(gap) > max_gap:
                    continue
                out["pairs"].append({
                    "a": a["name"], "b": b["name"],
                    "gap_xyz": [round(g, 2) for g in gap],
                    "min_gap": round(max(gap), 2),
                })
        out["count"] = len(out["pairs"])
        out["note"] = u"当前仅做包围盒邻近筛选；精确面-面距离在下一版实现"
    except Exception:
        out["ok"] = False
        out["error"] = _tail(traceback.format_exc())
    return out


OPS = {
    "ping": op_ping,
    "probe": op_probe,
    "list_parts": op_list_parts,
    "symmetry": op_symmetry,
    "gaps": op_gaps,
}


def _dispatch(cmd):
    op = (cmd or {}).get("op") or ""
    fn = OPS.get(op)
    if fn is None:
        return {"ok": False, "error": u"未知命令: %s" % op}
    return fn((cmd or {}).get("payload") or {})


def _poll():
    if _busy:
        return
    cmd = _read_json(CMD_PATH)
    if not cmd:
        _state()
        return
    try:
        os.remove(CMD_PATH)
    except Exception:
        pass
    _busy.append(1)
    try:
        res = _dispatch(cmd)
        if not isinstance(res, dict):
            res = {"ok": False, "error": u"命令返回格式异常"}
    except Exception:
        res = {"ok": False, "error": _tail(traceback.format_exc(), 2000)}
    finally:
        try:
            _busy.remove(1)
        except Exception:
            pass
    res["seq"] = cmd.get("seq")
    res["op"] = cmd.get("op")
    res["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_json(RES_PATH, res)


def start():
    """启动轮询：依次尝试 DispatcherTimer / WinForms Timer / System.Timers"""
    _state({"msg": u"starting"})

    # 方式一：WPF DispatcherTimer（与 SCDM UI 同线程，最安全）
    try:
        from System.Windows.Threading import DispatcherTimer, Dispatcher
        from System import TimeSpan
        d = DispatcherTimer()
        d.Interval = TimeSpan.FromSeconds(1.0)

        def on_tick(sender, args):
            if _busy:
                return
            try:
                _poll()
            except Exception:
                pass

        d.Tick += on_tick
        d.Start()
        _keep.append(d)
        _state({"msg": u"agent started (DispatcherTimer)"})
        print(u"[scdm-bridge] 已启动（DispatcherTimer）：%s" % IPC_DIR)
        return True
    except Exception:
        pass

    # 方式二：WinForms Timer
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import Timer
        t = Timer()
        t.Interval = 1000

        def on_tick2(sender, args):
            if _busy:
                return
            try:
                _poll()
            except Exception:
                pass

        t.Tick += on_tick2
        t.Start()
        _keep.append(t)
        _state({"msg": u"agent started (WinForms Timer)"})
        print(u"[scdm-bridge] 已启动（WinForms Timer）：%s" % IPC_DIR)
        return True
    except Exception:
        pass

    # 方式三：System.Timers（兜底）
    try:
        from System.Timers import Timer as SysTimer

        tm = SysTimer()
        tm.Interval = 1000.0
        tm.AutoReset = True

        def on_elapsed(sender, args):
            if _busy:
                return
            _busy.append(1)
            try:
                _poll2 = _poll
                cmd = _read_json(CMD_PATH)
                if cmd:
                    try:
                        os.remove(CMD_PATH)
                    except Exception:
                        pass
                    res = _dispatch(cmd)
                    res["seq"] = cmd.get("seq")
                    res["op"] = cmd.get("op")
                    res["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    _write_json(RES_PATH, res)
            except Exception:
                pass
            finally:
                try:
                    _busy.remove(1)
                except Exception:
                    pass

        tm.Elapsed += on_elapsed
        tm.Start()
        _keep.append(tm)
        _state({"msg": u"agent started (Timers)"})
        print(u"[scdm-bridge] 已启动（Timers）：%s" % IPC_DIR)
        return True
    except Exception:
        _state({"msg": u"start failed", "error": _tail(traceback.format_exc())})
        return False


try:
    if not globals().get("_keep"):
        start()
except Exception:
    pass
