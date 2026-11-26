# -*- coding: utf-8 -*-
"""SpaceClaim 后台体检脚本（IronPython 2.7，命令行 /RunScript 调用）

    SpaceClaim.exe "<模型>" /RunScript="<安装目录>/app/scdm_batch.py"
        /Headless=True /ExitAfterScript=True /Splash=False

读取 %TEMP%/meshkit_scdm_job.json：
    {"op": "all|parts|symmetry|gaps|interference",
     "result": "<结果 json 路径>", "tol_mm": 1.0, "max_gap_mm": 3.0,
     "whitelist": ["其他"]}

只读；单位统一换算为 mm（SpaceClaim 内部单位是米）。
"""

import os
import sys
import json
import time
import traceback

TEMP_DIR = os.environ.get("TEMP") or os.environ.get("TMP") or r"C:\Temp"
JOB_PATH = os.path.join(TEMP_DIR, "meshkit_scdm_job.json")
TRACE_PATH = os.path.join(TEMP_DIR, "meshkit_scdm_trace.log")
MM = 1000.0          # 米 -> 毫米
MM3 = 1e9            # 立方米 -> 立方毫米


def _trace(msg):
    try:
        f = open(TRACE_PATH, "a")
        f.write(u"%s %s\n" % (time.strftime("%H:%M:%S"), msg))
        f.close()
    except Exception:
        pass


def _read(path):
    try:
        f = open(path, "r")
        try:
            return json.load(f)
        finally:
            f.close()
    except Exception:
        return None


def _write(path, obj):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        try:
            os.makedirs(d)
        except Exception:
            pass
    f = open(path, "w")
    try:
        json.dump(obj, f)
    finally:
        f.close()


def load_api():
    import clr
    from Microsoft.Win32 import Registry
    key = Registry.ClassesRoot.OpenSubKey("SpaceClaim.AnsysSCDM.221")
    ver = key.GetValue("Version") if key is not None else None
    root = (os.environ.get("AWP_ROOT221") or os.environ.get("AWP_ROOT")
            or r"D:\Program Files\ANSYS Inc\v221")
    scdm = os.path.join(root, "scdm")
    if scdm not in sys.path:
        sys.path.append(scdm)
    full = ("spaceclaim.api.v22, Version=22.1.%s.0, Culture=neutral, "
            "PublicKeyToken=7210645d4d5e3a39" % ver)
    clr.LoadAssemblyByName(full)
    clr.AddReference(full)
    clr.LoadAssemblyFromFile("SpaceClaim.Api.V22.Scripting")
    clr.AddReference("SpaceClaim.Api.V22.Scripting")
    from SpaceClaim.Api.V22 import Window
    from SpaceClaim.Api.V22.Geometry import Matrix, Direction, Collision
    return {"Window": Window, "Matrix": Matrix,
            "Direction": Direction, "Collision": Collision}


# ---------------------------------------------------------------- 遍历
def collect_bodies(part):
    out = []

    def walk(p, path, depth):
        if depth > 8:
            return
        try:
            comps = list(p.Components or [])
        except Exception:
            comps = []
        for c in comps:
            try:
                nm = u"%s" % (c.Name or u"")
            except Exception:
                nm = u""
            sub = (path + u"/" + nm) if nm else path
            try:
                walk(c.Content, sub, depth + 1)
            except Exception:
                pass
        try:
            bs = list(p.Bodies or [])
        except Exception:
            bs = []
        for b in bs:
            out.append((path, b))

    walk(part, u"", 0)
    return out


def body_info(path, b, api):
    info = {"name": u"%s" % (b.Name or u""), "path": path}
    try:
        info["volume_mm3"] = round(float(b.Shape.Volume) * MM3, 1)
    except Exception:
        info["volume_mm3"] = None
    bb = None
    err = None
    try:
        box = b.Shape.GetBoundingBox(api["Matrix"].Identity)
        corners = list(box.Corners)
        xs = [float(p.X) for p in corners]
        ys = [float(p.Y) for p in corners]
        zs = [float(p.Z) for p in corners]
        bb = {"min": [min(xs) * MM, min(ys) * MM, min(zs) * MM],
              "max": [max(xs) * MM, max(ys) * MM, max(zs) * MM]}
    except Exception:
        err = (u"%s" % traceback.format_exc())[-200:]
    if bb:
        info["dims"] = [round(bb["max"][i] - bb["min"][i], 2) for i in range(3)]
        info["center"] = [round((bb["max"][i] + bb["min"][i]) / 2.0, 2) for i in range(3)]
        info["bbox"] = {"min": [round(v, 2) for v in bb["min"]],
                        "max": [round(v, 2) for v in bb["max"]]}
    else:
        info["dims"] = None
        info["center"] = None
        info["bbox"] = None
        info["bbox_error"] = err
    return info


# ---------------------------------------------------------------- 对称
def check_symmetry(infos, tol=1.0, whitelist=None):
    white = whitelist or []
    items = []
    skipped = 0
    for it in infos:
        if not it.get("dims"):
            skipped += 1
            continue
        if any(x and x in it["name"] for x in white):
            skipped += 1
            continue
        items.append(it)

    used = [False] * len(items)
    groups = []
    for i in range(len(items)):
        if used[i]:
            continue
        base = sorted(items[i]["dims"])
        grp = [i]
        used[i] = True
        for j in range(i + 1, len(items)):
            if used[j]:
                continue
            d = sorted(items[j]["dims"])
            if all(abs(d[k] - base[k]) <= 0.5 for k in range(3)):
                grp.append(j)
                used[j] = True
        if len(grp) >= 2:
            groups.append(grp)

    issues = []
    checked = 0
    for grp in groups:
        for axis in (0, 1, 2):
            vals = sorted(items[i]["center"][axis] for i in grp)
            mid = (vals[0] + vals[-1]) / 2.0
            taken = {}
            for ii in grp:
                if ii in taken:
                    continue
                ca = items[ii]["center"][axis]
                expect = 2 * mid - ca
                best, bd = None, None
                for jj in grp:
                    if jj == ii or jj in taken:
                        continue
                    dd = abs(items[jj]["center"][axis] - expect)
                    if bd is None or dd < bd:
                        best, bd = jj, dd
                if best is None:
                    continue
                taken[ii] = 1
                taken[best] = 1
                checked += 1
                if bd > tol:
                    issues.append({
                        "type": "asymmetry", "axis": "XYZ"[axis],
                        "a": items[ii]["name"], "b": items[best]["name"],
                        "expected": round(expect, 2),
                        "actual": round(items[best]["center"][axis], 2),
                        "deviation": round(bd, 2),
                        "suggest_shift": round(expect - items[best]["center"][axis], 2),
                    })
    return {"groups": len(groups), "checked": checked,
            "skipped": skipped, "issues": issues}


# ---------------------------------------------------------------- 距离/干涉
def _bbox_gap(a, b):
    out = []
    for k in range(3):
        out.append(max(a["min"][k] - b["max"][k], b["min"][k] - a["max"][k]))
    return out


def check_pairs(bodies, infos, api, max_gap=3.0):
    gaps, interf, errs = [], [], []
    n = len(infos)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = infos[i], infos[j]
            if not a.get("bbox") or not b.get("bbox"):
                continue
            g = _bbox_gap(a["bbox"], b["bbox"])
            if max(g) > max_gap:
                continue
            sa = bodies[i][1].Shape
            sb = bodies[j][1].Shape
            try:
                col = sa.GetCollision(sb)
                if bool(col & api["Collision"].Intersect):
                    interf.append({"a": a["name"], "b": b["name"],
                                   "bbox_overlap_mm": [round(-x, 2) for x in g]})
                    continue
            except Exception:
                errs.append(u"collision %s/%s" % (a["name"], b["name"]))
            try:
                sep = sa.GetClosestSeparation(sb)
                dist = float(sep.Distance) * MM
                if dist <= max_gap:
                    pa, pb = sep.PointA, sep.PointB
                    gaps.append({
                        "a": a["name"], "b": b["name"],
                        "distance_mm": round(dist, 3),
                        "point_a": [round(float(pa.X) * MM, 2),
                                    round(float(pa.Y) * MM, 2),
                                    round(float(pa.Z) * MM, 2)],
                        "point_b": [round(float(pb.X) * MM, 2),
                                    round(float(pb.Y) * MM, 2),
                                    round(float(pb.Z) * MM, 2)],
                    })
            except Exception:
                errs.append(u"sep %s/%s" % (a["name"], b["name"]))
    return {"gaps": gaps, "interference": interf,
            "pair_errors": errs[:10]}


# ---------------------------------------------------------------- 主流程
def main():
    _trace(u"==== start ====")
    res = {"ok": False, "agent": "scdm_batch", "version": u"2026-11-26.2"}
    job = _read(JOB_PATH) or {}
    out_path = job.get("result") or os.path.join(TEMP_DIR, "meshkit_scdm_result.json")
    op = job.get("op") or "all"
    try:
        api = load_api()
        _trace(u"api loaded")
        win = api["Window"].ActiveWindow
        doc = win.Document if win is not None else None
        if doc is None:
            raise RuntimeError(u"no active document")
        for attr in ("Name", "Title", "FilePath", "Path"):
            try:
                v = getattr(doc, attr)
                if v:
                    res["doc"] = u"%s" % v
                    break
            except Exception:
                continue
        bodies = collect_bodies(doc.MainPart)
        res["body_count"] = len(bodies)
        _trace(u"bodies=%d" % len(bodies))
        infos = [body_info(p, b, api) for p, b in bodies]
        _trace(u"infos done")
        if op in ("all", "parts", "probe"):
            res["parts"] = infos
        if op in ("all", "symmetry"):
            res["symmetry"] = check_symmetry(
                infos, tol=float(job.get("tol_mm") or 1.0),
                whitelist=job.get("whitelist") or [])
        if op in ("all", "gaps", "interference"):
            res["pairs"] = check_pairs(bodies, infos, api,
                                       max_gap=float(job.get("max_gap_mm") or 3.0))
        res["ok"] = True
    except Exception:
        res["error"] = (u"%s" % traceback.format_exc())[-1500:]
    res["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        _write(out_path, res)
        _trace(u"written %s" % out_path)
    except Exception:
        _trace(u"write failed")
    _trace(u"==== end ok=%s ====" % res.get("ok"))


try:
    main()
except Exception:
    _trace(u"top-level error %s" % (u"%s" % traceback.format_exc())[-300:])
