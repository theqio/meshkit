# -*- coding: utf-8 -*-
"""
bridge_agent.py —— 运行在 ANSYS Mechanical 内部的桥接代理（IronPython 2.7）

作用：让外部 AI 软件（本地网页应用）能实时读取模型状态、下发命令。

原理：
    Mechanical 里用 DispatcherTimer 每 1 秒在 **UI 线程**轮询命令文件
        <IPC>/cmd.json     ← 外部写入
        <IPC>/result.json  ← 本代理写回
    用 UI 线程执行，避免跨线程调用 Mechanical API 导致崩溃。

安装（只需一次，每次重启 Mechanical 后要重新执行）：
    在 Mechanical 的 Automation → Scripting 里粘贴：
        execfile("D:/ai/meshkit/app/bridge_agent.py")
    Ctrl+F5 运行，看到 "[bridge] 已启动" 即成功。

支持的命令（cmd.json 里 op 字段）：
    ping                          连通性测试
    status                        模型状态：单位/设置/单元节点数/体状态/控制项
    apply                         应用全局设置与类别尺寸（参数见 payload）
    mesh                          清空并重新生成网格
    fix_failed                    自动修复失败体（减半尺寸 + 重划，最多 N 轮）
    quality                       质量报告
    exec                          执行指定脚本
"""
from __future__ import division

import codecs
import json
import os
import sys
import time
import traceback

IPC_DIR = r"D:\ai\meshkit\app\ipc"
AGENT_PID = os.getpid()
AGENTS_DIR = os.path.join(IPC_DIR, "agents")
try:
    os.makedirs(AGENTS_DIR)
except Exception:
    pass
CMD_PATH = os.path.join(IPC_DIR, "cmd_%d.json" % AGENT_PID)
RES_PATH = os.path.join(IPC_DIR, "result_%d.json" % AGENT_PID)
REG_PATH = os.path.join(AGENTS_DIR, "%d.json" % AGENT_PID)
STATE_PATH = os.path.join(IPC_DIR, "bridge_state.json")
ROOT = r"D:\ai\meshkit"
AGENT_VERSION = u"2026-12-02.5"   # 每次改代理代码都要改这里，用来确认是否已重载

# 允许的类别尺寸上下限（mm）
SIZE_MIN = 2.0
SIZE_MAX = 60.0

_keep = []          # 持有 timer 引用，防止被 GC
_busy = []          # 简单重入保护
_hb = [0.0]         # 心跳时间戳

CANCEL_PATH = os.path.join(IPC_DIR, "cancel.flag")
PROGRESS_PATH = os.path.join(IPC_DIR, "progress.json")


def _clear_cancel():
    try:
        if os.path.isfile(CANCEL_PATH):
            os.remove(CANCEL_PATH)
    except Exception:
        pass


def _cancelled():
    """用户点"停止"后，服务器会写这个文件"""
    try:
        return os.path.isfile(CANCEL_PATH)
    except Exception:
        return False


def _progress(op, step, done=None, total=None, note=u""):
    try:
        _write_json(PROGRESS_PATH, {
            "op": op, "step": step, "done": done, "total": total,
            "note": note, "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
    except Exception:
        pass


# ============================================================================
#  基础 IO
# ============================================================================
def _read_json(path):
    if not os.path.isfile(path):
        return None
    try:
        f = codecs.open(path, "r", encoding="utf-8")
        try:
            return json.loads(f.read())
        finally:
            f.close()
    except Exception:
        return None


def _write_json(path, obj):
    tmp = path + ".tmp"
    try:
        f = codecs.open(tmp, "w", encoding="utf-8")
        try:
            f.write(json.dumps(obj, ensure_ascii=False, default=str))
        finally:
            f.close()
        if os.path.isfile(path):
            os.remove(path)
        os.rename(tmp, path)
        return True
    except Exception:
        return False


def _tail(text, n=3000):
    t = text or u""
    return t[-n:]


# ============================================================================
#  取对象
# ============================================================================
def _extapi():
    return globals().get("ExtAPI")


def _model():
    return _extapi().DataModel.Project.Model


def _mesh():
    return _model().Mesh


def _num(v):
    """Quantity / 字符串 / 数字 -> float（先按显示字符串解析，再退回 .Value）"""
    if v is None:
        return 0.0
    try:
        return float(v)
    except Exception:
        pass
    try:
        s = _txt(v).strip()
        if s:
            return float(s.split()[0])
    except Exception:
        pass
    try:
        return float(v.Value)
    except Exception:
        return 0.0


def _safe(obj, attr, default=None):
    try:
        return getattr(obj, attr)
    except Exception:
        return default


def _counts():
    """单元/节点数：本版本必须用 Mesh.Elements / Mesh.Nodes（MeshData 不可靠）"""
    mesh = _mesh()
    return (int(_num(_safe(mesh, "Elements", 0))),
            int(_num(_safe(mesh, "Nodes", 0))))


_EXTRA_BODIES = {}


def _bodies():
    """所有体：常规递归 + 分组文件夹（可嵌套）里的体，按名字去重"""
    out, seen = [], set()

    def cat_of(o):
        try:
            return _txt(o.DataModelObjectCategory)
        except Exception:
            return u""

    def add(lst):
        for b in lst:
            c = cat_of(b)
            if "Body" not in c:
                continue
            nk = u"n:" + _txt(_safe(b, "Name", u""))
            if nk in seen:
                continue
            seen.add(nk)
            out.append(b)

    def bodies_in(node):
        acc = []
        try:
            kids = list(node.Children)
        except Exception:
            kids = []
        for k in kids:
            c = cat_of(k)
            if "Body" in c:
                acc.append(k)
            elif "Part" in c:
                try:
                    bs = [x for x in k.Children if "Body" in cat_of(x)]
                except Exception:
                    bs = []
                if bs:
                    acc.extend(bs)
                else:
                    acc.append(k)      # 没有体的 Part 也保留，避免漏
            elif "roupingFolder" in c:
                acc.extend(bodies_in(k))
        return acc

    def walk(nodes):
        for x in nodes:
            c = cat_of(x)
            if "roupingFolder" in c:
                add(bodies_in(x))
            else:
                try:
                    kids = list(x.Children)
                except Exception:
                    kids = []
                if kids:
                    walk(kids)

    try:
        geo = _model().Geometry
    except Exception:
        return []

    try:
        from Ansys.Mechanical.DataModel.Enums import DataModelObjectCategory as cats
        add(list(geo.GetChildren(cats.Body, True)))
    except Exception:
        pass

    try:
        walk(list(geo.Children))
    except Exception:
        pass

    # 追加由分组扫描发现的体（嵌套文件夹里的）
    try:
        for _n, _o in list(_EXTRA_BODIES.items()):
            out.append(_o)
    except Exception:
        pass

    # 补充：遍历所有 Part，把其中的体都收进来（最不容易漏）
    try:
        from Ansys.Mechanical.DataModel.Enums import DataModelObjectCategory as _cats3
        _geo3 = _model().Geometry
        for _part in _geo3.GetChildren(_cats3.Part, True):
            try:
                _kids = list(_part.Children)
            except Exception:
                _kids = []
            for _kb in _kids:
                _kc = _txt(_safe(_kb, "DataModelObjectCategory", u""))
                if "Body" in _kc:
                    out.append(_kb)
    except Exception:
        pass

    # 最终去重：按对象 id + 当前名字（重命名后旧缓存名字会失效）
    if out:
        _uniq, _seen_key = [], set()
        for _b in out:
            _k = None
            try:
                _k = int(_b.GetGeoBody().Id)
            except Exception:
                _k = None
            if _k is None:
                _k = (u"%s|%s|%s|%s" % (
                    _txt(_safe(_b, "Name", u"")),
                    round(_mm_value(_safe(_b, "CentroidX", 0)), 1),
                    round(_mm_value(_safe(_b, "CentroidY", 0)), 1),
                    round(_mm_value(_safe(_b, "CentroidZ", 0)), 1)))
            if _k in _seen_key:
                continue
            _seen_key.add(_k)
            _uniq.append(_b)
        return _uniq
    try:
        return [x for x in geo.Children if "Body" in cat_of(x)]
    except Exception:
        return []



def _txt(v):
    try:
        return u"%s" % v
    except Exception:
        return u"?"


def _mm(value):
    """把带单位的量统一换算成 mm 数值（工程单位可能是 m 或 mm）"""
    try:
        s = u"%s" % value
    except Exception:
        return 0.0
    try:
        num = float(s.split(u"[")[0].strip())
    except Exception:
        return 0.0
    if u"m]" in s.replace(u" ", u"") and u"mm]" not in s.replace(u" ", u""):
        # 单位是 m
        if u"[m]" in s.replace(u" ", u""):
            return num * 1000.0
    return num


# ============================================================================
#  命令实现
# ============================================================================
def _units_enum_names():
    """列出单位系统枚举的全部成员名"""
    try:
        import System
        proj = _extapi().DataModel.Project
        cur = proj.UnitSystem
        t = cur.GetType()
        return list(System.Enum.GetNames(t)), _txt(cur)
    except Exception:
        return [], u"?"


def _set_units_mm():
    """把项目单位切成 mm / kg / N（枚举成员名 StandardNMM）"""
    try:
        import System
        proj = _extapi().DataModel.Project
        names, cur = _units_enum_names()
        if cur == u"StandardNMM":
            return True, u"已是 StandardNMM", names
        want = None
        for cand in (u"StandardNMM", u"Metric_mm_kg_N", u"Metric_mm_t_N"):
            if cand in names:
                want = cand
                break
        if want is None:
            return False, u"枚举里没有 mm 成员：%s" % u",".join(names[:10]), names
        t = proj.UnitSystem.GetType()
        proj.UnitSystem = System.Enum.Parse(t, want, True)
        now = _txt(proj.UnitSystem)
        return (now == want), u"设置为 %s（现为 %s）" % (want, now), names
    except Exception:
        return False, _tail(traceback.format_exc(), 300), []


def op_project_info(payload):
    """读取当前工程信息：名称 / 路径 / 单位"""
    out = {"ok": True}
    try:
        proj = _extapi().DataModel.Project
    except Exception as e:
        return {"ok": False, "error": _txt(e)}

    for attr in ("Name", "ProjectName", "FileName", "ProjectDirectory",
                 "Directory", "Path", "UserFilesDirectory"):
        try:
            out[attr] = _txt(getattr(proj, attr))
        except Exception:
            pass

    # 模型对象路径上再兜一次
    try:
        model = _model()
        for attr in ("Name", "FileName", "Path"):
            try:
                k = "model_" + attr
                if k not in out:
                    out[k] = _txt(getattr(model, attr))
            except Exception:
                pass
    except Exception:
        pass

    # 若只有目录，取最后一段作为工程名
    if not out.get("Name") and out.get("ProjectDirectory"):
        try:
            p = out["ProjectDirectory"].rstrip("\\/")
            out["Name"] = p.split("\\")[-1].split("/")[-1]
        except Exception:
            pass

    return out


def op_set_units(payload):
    ok, msg, names = _set_units_mm()
    return {"ok": ok, "message": msg, "available": names[:20]}


def op_probe_messages(payload):
    out = {"paths": [], "messages": []}
    try:
        dm = _extapi().DataModel
    except Exception:
        return {"ok": False, "error": "no DataModel"}

    # 依次尝试几种常见位置
    cands = []
    try:
        cands.append(("DataModel.Messages", list(dm.Messages)))
    except Exception as e:
        out["paths"].append("DataModel.Messages: %s" % e)
    try:
        proj = dm.Project
        cands.append(("Project.Messages", list(proj.Messages)))
    except Exception as e:
        out["paths"].append("Project.Messages: %s" % e)
    try:
        tree = dm.Tree
        cands.append(("Tree.Messages", list(tree.Messages)))
    except Exception as e:
        out["paths"].append("Tree.Messages: %s" % e)

    for label, msgs in cands:
        out["paths"].append("%s: %d 条" % (label, len(msgs)))
        for m in msgs[-40:]:
            try:
                item = {"where": label,
                        "severity": _txt(getattr(m, "Severity", u"")),
                        "text": _txt(getattr(m, "Text", u"")),
                        "time": _txt(getattr(m, "Time", u""))}
                out["messages"].append(item)
            except Exception:
                pass
    return {"ok": True, "result": out}


def op_save_project(payload):
    """保存工程（防崩溃）；多种入口依次尝试"""
    errs = []
    proj = None
    try:
        proj = _extapi().DataModel.Project
    except Exception as e:
        errs.append("Project: " + _txt(e)[:60])

    for how in ("Save", "SaveProject"):
        if proj is None:
            break
        try:
            getattr(proj, how)()
            return {"ok": True, "how": how, "file": _txt(_safe(proj, "Name", u""))}
        except Exception as e:
            errs.append(how + ": " + _txt(e)[:60])

    try:
        _model().Save()
        return {"ok": True, "how": "model.Save", "file": ""}
    except Exception as e:
        errs.append("model.Save: " + _txt(e)[:60])
    return {"ok": False, "error": u" | ".join(errs)}


def op_probe_all_bodies(payload):
    """遍历整棵树，统计所有 Body 节点（用于对比 _bodies() 漏了谁）"""
    model = _model()
    found = []
    seen = set()

    def cat_of(o):
        try:
            return _txt(o.DataModelObjectCategory)
        except Exception:
            return u"?"

    def walk(nodes, depth):
        if depth > 8:
            return
        for x in nodes:
            try:
                xid = id(x)
            except Exception:
                xid = 0
            if xid in seen:
                continue
            seen.add(xid)
            c = cat_of(x)
            if "Body" in c:
                found.append({"name": _txt(_safe(x, "Name", u"")),
                              "cat": c,
                              "sup": bool(_safe(x, "Suppressed", False)),
                              "state": _txt(_safe(x, "ObjectState", u""))})
            try:
                kids = list(x.Children)
            except Exception:
                kids = []
            if kids:
                walk(kids, depth + 1)

    try:
        walk([model], 0)
    except Exception as e:
        return {"ok": False, "error": _txt(e)}

    cur = set(_txt(_safe(b, "Name", u"")) for b in _bodies())
    names = set(f["name"] for f in found)
    return {"ok": True,
            "tree_bodies": len(found),
            "func_bodies": len(cur),
            "only_in_tree": sorted(names - cur)[:30],
            "only_in_func": sorted(cur - names)[:30],
            "suppressed": sum(1 for f in found if f["sup"])}


def op_fix_dup_names(payload):
    """修复重名体：给重复名字的体加后缀 _b/_c…（保留原始序号）"""
    bodies = _bodies()
    from collections import Counter
    names = [_txt(_safe(b, "Name", u"")) for b in bodies]
    cnt = Counter(names)
    dup = {n: c for n, c in cnt.items() if c > 1 and n}
    if not dup:
        return {"ok": True, "fixed": 0, "dup_groups": 0}
    suffix = "abcdefghijklmnopqrstuvwxyz"
    seen = {}
    items = []
    for b in bodies:
        n = _txt(_safe(b, "Name", u""))
        if n not in dup:
            continue
        k = seen.get(n, 0)
        seen[n] = k + 1
        if k == 0:
            continue          # 第一个保持原名
        items.append({"body": n, "name": u"%s_%s" % (n, suffix[(k - 1) % 26])})
    # 逐个改名（同名会冲突，用对象直改）
    by_name = {}
    for b in bodies:
        by_name.setdefault(_txt(_safe(b, "Name", u"")), []).append(b)
    fixed = 0
    for b in bodies:
        n = _txt(_safe(b, "Name", u""))
        if n not in dup:
            continue
        lst = by_name.get(n) or []
        idx = lst.index(b) if b in lst else -1
        if idx <= 0:
            continue
        try:
            b.Name = u"%s_%s" % (n, suffix[(idx - 1) % 26])
            fixed += 1
        except Exception:
            pass
    return {"ok": True, "fixed": fixed, "dup_groups": len(dup),
            "dups": list(dup.items())[:10]}


def op_dedupe_names(payload):
    """把重名体改成唯一名字（名字只作标识，不参与分类）"""
    bodies = _bodies()
    from collections import defaultdict
    groups = defaultdict(list)
    for b in bodies:
        groups[_txt(_safe(b, "Name", u""))].append(b)

    fixed, failed = [], []
    for name, items in groups.items():
        if len(items) <= 1:
            continue
        for i, b in enumerate(items[1:], 2):
            new = u"%s#%d" % (name, i)
            try:
                b.Name = new
                fixed.append(u"%s -> %s" % (name, new))
            except Exception as e:
                failed.append(u"%s: %s" % (name, _txt(e)[:60]))
    return {"ok": not failed, "fixed": len(fixed),
            "samples": fixed[:10], "failed": failed[:5]}


def op_probe_save_api(payload):
    """找 Mechanical 里可用的保存接口"""
    out = {"ok": True, "candidates": {}}
    targets = {}
    try:
        ext = _extapi()
        targets["application"] = ext.Application
        targets["datamodel"] = ext.DataModel
        targets["project"] = ext.DataModel.Project
        targets["model"] = _model()
    except Exception as e:
        return {"ok": False, "error": _txt(e)}

    for name, obj in targets.items():
        try:
            names = [n for n in dir(obj) if "ave" in n or "rite" in n]
            out["candidates"][name] = names[:20]
        except Exception as e:
            out["candidates"][name] = ["ERR " + _txt(e)[:40]]
    return out


def op_probe_fix_props(payload):
    """探测：SIZE_FIX 控制项上可用的属性（是否支持 Defeature / Method）"""
    out = {"ok": True, "controls": []}
    mesh = _mesh()
    for c in (mesh.Children or []):
        nm = _txt(_safe(c, "Name", u""))
        if not nm.startswith(u"SIZE_FIX_"):
            continue
        props = []
        try:
            for p in c.GetVisibleProperties():
                try:
                    props.append(_txt(p.Name))
                except Exception:
                    pass
        except Exception:
            pass
        out["controls"].append({"name": nm, "props": props[:40]})
        if len(out["controls"]) >= 3:
            break
    # 全局 Mesh 上与方法/去特征相关的属性
    try:
        g = [n for n in dir(mesh) if "efeature" in n or "Metric" in n]
    except Exception:
        g = []
    out["mesh_props"] = g[:20]
    return out


def op_dedupe_controls(payload):
    """删除重复控制项：同一 (名字, 类型) 只保留一个
       保留策略：作用域体数多的优先；相同时保留先出现的
       返回 {ok, removed:[...], kept:[...]}
    """
    try:
        _touch_ctrl()
    except Exception:
        pass
    mesh = _mesh()
    groups = {}
    removed, kept, errors = [], [], []
    for c in list(mesh.Children or []):
        nm = _txt(_safe(c, "Name", u""))
        cat = _txt(_safe(c, "DataModelObjectCategory", u"")) or u""
        low = cat.lower()
        is_sizing = (u"sizing" in low) or nm.startswith(u"SIZE_")
        is_method = (u"method" in low) or nm.startswith(u"METHOD_")
        if not (is_sizing or is_method):
            continue
        kind = "sizing" if is_sizing else "method"
        try:
            ids = [int(x) for x in (_safe(_safe(c, "Location"), "Ids") or [])]
        except Exception:
            ids = []
        groups.setdefault((nm, kind), []).append((len(ids), c))
    for (nm, kind), items in groups.items():
        if len(items) <= 1:
            continue
        items.sort(key=lambda x: -x[0])
        for cnt, c in items[1:]:
            try:
                c.Delete()
                removed.append(nm)
            except Exception as e:
                errors.append(u"%s: %s" % (nm, _txt(e)[:50]))
        kept.append(nm)
    return {"ok": True, "removed": removed, "kept": kept,
            "removed_count": len(removed), "errors": errors,
            "groups": len(groups)}


def op_fix_body(payload):
    """对单个失败体设置 尺寸 / 方法 / 去特征值"""
    payload = payload or {}
    name = _txt(payload.get("name") or u"")
    size = payload.get("size")
    method = _txt(payload.get("method") or u"")
    defeature = payload.get("defeature")
    out = {"ok": True, "name": name, "actions": [], "errors": []}
    try:
        _touch_ctrl()
    except Exception:
        pass

    body = None
    for b in _bodies():
        if _txt(_safe(b, "Name", u"")) == name:
            body = b
            break
    if body is None:
        return {"ok": False, "error": u"找不到体: %s" % name}

    try:
        gid = int(body.GetGeoBody().Id)
    except Exception:
        gid = None
    if gid is None:
        return {"ok": False, "error": u"取不到几何 Id"}

    mesh = _mesh()

    # ---- 去特征（逐体：Sizing 的 LocalDefeaturingTolerance）----
    if defeature not in (None, u"", 0):
        try:
            v = float(defeature)
            cname = u"SIZE_FIX_%s" % name
            ctrl = None
            for c in (mesh.Children or []):
                if _txt(_safe(c, "Name", u"")) == cname:
                    ctrl = c
                    break
            if ctrl is None:
                # 没有尺寸控制项就建一个（沿用全局单元尺寸）
                try:
                    manager = _extapi().SelectionManager
                    info = manager.CreateSelectionInfo(_sel_enum())
                    info.Ids = [gid]
                    ctrl = mesh.AddSizing()
                    ctrl.Location = info
                    ctrl.Name = cname
                except Exception as e:
                    out["errors"].append(u"建尺寸控制项失败: %s" % _txt(e)[:70])
                    ctrl = None
            if ctrl is not None:
                prop = None
                for p in ctrl.Properties:
                    if _txt(_safe(p, "Name", u"")) == "LocalDefeaturingTolerance":
                        prop = p
                        break
                if prop is None:
                    out["errors"].append(u"该控制项没有 LocalDefeaturingTolerance")
                else:
                    prop.InternalValue = v
                    out["actions"].append(u"去特征=%.2fmm（仅该体）" % v)
        except Exception as e:
            out["errors"].append(u"逐体去特征失败: %s" % _txt(e)[:80])

    # ---- 尺寸（逐体）----
    if size not in (None, u"", 0):
        try:
            v = float(size)
            ctrl = None
            cname = u"SIZE_FIX_%s" % name
            for c in (mesh.Children or []):
                if _txt(_safe(c, "Name", u"")) == cname:
                    ctrl = c
                    break
            if ctrl is None:
                from Ansys.Mechanical.DataModel.Enums import DataModelObjectCategory as _cats
                manager = _extapi().SelectionManager
                info = manager.CreateSelectionInfo(_sel_enum())
                info.Ids = [gid]
                ctrl = mesh.AddSizing()
                ctrl.Location = info
                ctrl.Name = cname
            from Ansys.Core.Units import Quantity
            ctrl.ElementSize = Quantity(u"%.3f [mm]" % v)
            out["actions"].append(u"尺寸=%.2fmm" % v)
        except Exception as e:
            out["errors"].append(u"尺寸设置失败: %s" % _txt(e)[:80])

    # ---- 方法（逐体）----
    if method:
        try:
            mname = u"METHOD_%s" % name
            ctrl = None
            for c in (mesh.Children or []):
                if _txt(_safe(c, "Name", u"")) == mname:
                    ctrl = c
                    break
            if method == u"Automatic":
                if ctrl is not None:
                    try:
                        ctrl.Delete()
                    except Exception:
                        pass
                out["actions"].append(u"方法=自动")
            else:
                if ctrl is None:
                    manager = _extapi().SelectionManager
                    info = manager.CreateSelectionInfo(_sel_enum())
                    info.Ids = [gid]
                    ctrl = mesh.AddAutomaticMethod()
                    ctrl.Name = mname
                    ctrl.Location = info
                from Ansys.Mechanical.DataModel.Enums import MethodType
                target = u"Sweep" if method.startswith(u"Sweep") else u"Automatic"
                try:
                    ctrl.Method = getattr(MethodType, target)
                except Exception as e:
                    out["errors"].append(u"方法设置失败: %s" % _txt(e)[:60])
                if method == u"Sweep + Automatic Thin":
                    _set_auto_thin(ctrl)
                out["actions"].append(u"方法=%s" % method)
        except Exception as e:
            out["errors"].append(u"方法异常: %s" % _txt(e)[:80])

    # 单建了控制项后，把这个体从类别控制项里摘掉，避免重复/冲突
    if out.get("actions"):
        try:
            removed, errs = _exclude_body_from_categories(gid, name)
            if removed:
                out["excluded_from"] = removed
            if errs:
                out["errors"].extend(errs)
        except Exception as e:
            out["errors"].append(u"摘除类别范围失败: %s" % _txt(e)[:70])

    out["ok"] = not out["errors"]
    return out


def op_probe_defeature(payload):
    """探测：Sizing / Mesh 上是否有逐体的去特征属性"""
    out = {"ok": True}
    mesh = _mesh()
    try:
        out["mesh_defeature"] = _txt(_safe(mesh, "DefeatureTolerance", u""))
    except Exception as e:
        out["mesh_defeature"] = "ERR " + _txt(e)[:40]
    # 找任意一个 Sizing 控制项，列出含 feat 的属性
    found = []
    for c in (mesh.Children or []):
        nm = _txt(_safe(c, "Name", u""))
        try:
            props = [_txt(p.Name) for p in c.GetVisibleProperties()]
        except Exception:
            continue
        hit = [p for p in props if "feat" in p.lower()]
        if hit:
            found.append({"control": nm, "defeature_props": hit,
                          "all": props[:30]})
        if len(found) >= 3:
            break
    out["with_defeature"] = found
    return out


def op_probe_body_defeature(payload):
    """探测体对象上是否有 Defeature 相关属性"""
    out = {"ok": True, "bodies": []}
    for b in _bodies()[:5]:
        item = {"name": _txt(_safe(b, "Name", u""))}
        try:
            props = [_txt(p.Name) for p in b.GetVisibleProperties()]
        except Exception as e:
            props = ["ERR " + _txt(e)[:40]]
        item["defeature_props"] = [p for p in props if "feat" in p.lower()]
        item["sample_props"] = props[:18]
        out["bodies"].append(item)
    # 方法控制项上是否有 Defeature
    m = _mesh()
    for c in (m.Children or []):
        nm = _txt(_safe(c, "Name", u""))
        if nm.startswith(u"METHOD_"):
            try:
                props = [_txt(p.Name) for p in c.GetVisibleProperties()]
            except Exception:
                continue
            out["method_control"] = {"name": nm,
                                     "defeature_props": [p for p in props if "feat" in p.lower()],
                                     "props": props[:25]}
            break
    return out


def op_probe_mesh_methods(payload):
    """探测 Mesh 上可用于"只重划某个体"的方法"""
    m = _mesh()
    names = []
    for n in dir(m):
        low = n.lower()
        if ("mesh" in low and ("generat" in low or "clear" in low)):
            names.append(n)
    extra = [n for n in dir(m) if n in (
        "GenerateMesh", "ClearGeneratedMesh", "ClearGeneratedMeshData",
        "GenerateMeshOnSelection", "ClearGeneratedMeshAndData")]
    return {"ok": True, "candidates": sorted(set(names + extra))[:30]}


def op_probe_sizing_defeature(payload):
    """在 Sizing 控制项上试 DefeatureSize"""
    mesh = _mesh()
    out = {"ok": True, "controls": []}
    for c in (mesh.Children or []):
        nm = _txt(_safe(c, "Name", u""))
        if not nm.startswith(u"SIZE_FIX_"):
            continue
        item = {"name": nm}
        item["dir_defeature"] = [n for n in dir(c) if "efeat" in n]
        for attr in ("DefeatureSize", "Defeature", "DefeatureTolerance"):
            try:
                v = getattr(c, attr)
                item[attr] = _txt(v)
            except Exception as e:
                item[attr] = "ERR " + _txt(e)[:40]
        out["controls"].append(item)
        if len(out["controls"]) >= 3:
            break
    if not out["controls"]:
        out["note"] = "当前没有 SIZE_FIX 控制项"
    return out


def op_probe_sizing_reflect(payload):
    """"反射探测 Sizing 的真实类型与属性列表"""
    mesh = _mesh()
    out = {"ok": True}
    for c in (mesh.Children or []):
        nm = _txt(_safe(c, "Name", u""))
        if not nm.startswith(u"SIZE_FIX_"):
            continue
        out["name"] = nm
        try:
            out["type"] = _txt(c.GetType().Name)
        except Exception as e:
            out["type"] = "ERR " + _txt(e)[:40]
        props = []
        try:
            for p in c.GetType().GetProperties():
                try:
                    pn = _txt(p.Name)
                except Exception:
                    continue
                if "efeat" in pn.lower() or "dvanc" in pn.lower():
                    props.append(pn)
        except Exception as e:
            props.append("ERR " + _txt(e)[:50])
        out["reflect_defeature"] = props
        # 全部属性名（截断）
        allp = []
        try:
            for p in c.GetType().GetProperties():
                try:
                    allp.append(_txt(p.Name))
                except Exception:
                    pass
        except Exception:
            pass
        out["all_props_count"] = len(allp)
        out["all_props"] = allp[:60]
        break
    return out


def op_probe_props_list(payload):
    """"遍历 c.Properties 集合，找 Defeature 项（GUI 里的 Details 项）"""
    mesh = _mesh()
    out = {"ok": True, "items": [], "count": 0}
    for c in (mesh.Children or []):
        nm = _txt(_safe(c, "Name", u""))
        if not nm.startswith(u"SIZE_FIX_"):
            continue
        try:
            plist = list(c.Properties)
        except Exception as e:
            out["error"] = _txt(e)[:80]
            return out
        out["count"] = len(plist)
        for p in plist:
            try:
                pn = _txt(p.Name)
            except Exception:
                continue
            if "efeat" in pn.lower():
                item = {"name": pn}
                try:
                    item["value"] = _txt(p.Value)
                except Exception as e:
                    item["value"] = "ERR " + _txt(e)[:40]
                try:
                    item["readonly"] = bool(p.ReadOnly)
                except Exception:
                    pass
                out["items"].append(item)
            elif "dvanc" in pn.lower():
                out.setdefault("advanced_groups", []).append(pn)
        if not out["items"]:
            out["all_names"] = []
            for p in plist[:40]:
                try:
                    out["all_names"].append(_txt(p.Name))
                except Exception:
                    pass
        break
    return out


def op_probe_local_defeature(payload):
    """读取/设置 Sizing 上的 LocalDefeaturingTolerance"""
    mesh = _mesh()
    out = {"ok": True}
    for c in (mesh.Children or []):
        nm = _txt(_safe(c, "Name", u""))
        if not nm.startswith(u"SIZE_FIX_"):
            continue
        for p in c.Properties:
            pn = _txt(_safe(p, "Name", u""))
            if pn != "LocalDefeaturingTolerance":
                continue
            out["found_on"] = nm
            out["prop_attrs"] = [n for n in dir(p) if not n.startswith("_")][:25]
            for attr in ("Value", "ValueString", "InternalValue", "StringValue",
                         "DoubleValue", "Quantity", "DisplayString"):
                try:
                    out[attr] = _txt(getattr(p, attr))
                except Exception:
                    pass
            break
        break
    return out


def op_set_local_defeature(payload):
    """设置 Sizing 上的 LocalDefeaturingTolerance（逐体去特征）"""
    payload = payload or {}
    name = _txt(payload.get("name") or u"")
    value = payload.get("value")
    mesh = _mesh()
    ctrl = None
    for c in (mesh.Children or []):
        if _txt(_safe(c, "Name", u"")) == u"SIZE_FIX_%s" % name:
            ctrl = c
            break
    if ctrl is None:
        return {"ok": False, "error": u"该体还没有 SIZE_FIX 控制项，请先设尺寸"}
    prop = None
    for p in ctrl.Properties:
        if _txt(_safe(p, "Name", u"")) == "LocalDefeaturingTolerance":
            prop = p
            break
    if prop is None:
        return {"ok": False, "error": u"没有 LocalDefeaturingTolerance 属性"}
    out = {"ok": True, "before": _txt(_safe(prop, "StringValue", u"")),
           "api_name": _txt(_safe(prop, "RecordableAPIName", u""))}
    if value not in (None, u""):
        try:
            prop.InternalValue = float(value)
            out["set"] = float(value)
        except Exception as e:
            out["ok"] = False
            out["error"] = _txt(e)[:120]
    out["after"] = _txt(_safe(prop, "StringValue", u""))
    return out


def _ctl_ids(ctrl):
    """取控制项 Location 里的几何 Id 列表"""
    try:
        loc = _safe(ctrl, "Location")
        ids = _safe(loc, "Ids")
        return [int(x) for x in (ids or [])]
    except Exception:
        return []


def _set_ctl_ids(ctrl, ids):
    """把控制项 Location 设为给定 Id 列表"""
    try:
        manager = _extapi().SelectionManager
        info = manager.CreateSelectionInfo(_sel_enum())
        info.Ids = list(ids)
        ctrl.Location = info
        return True
    except Exception:
        return False


def _is_sizing_or_method(ctrl):
    """判断控制项是否属于"尺寸/方法"类（不依赖名字）"""
    try:
        cat = _txt(_safe(ctrl, "DataModelObjectCategory", u""))
    except Exception:
        cat = u""
    if cat:
        low = cat.lower()
        for k in ("sizing", "method", "sweep", "hexdominant", "tetrahedral",
                  "quadrilateral", "triangular", "cartesian", "multiZone"):
            if k in low:
                return True
    try:
        tn = _txt(ctrl.GetType().Name)
    except Exception:
        tn = u""
    low = tn.lower()
    return ("sizing" in low) or ("method" in low)


def _exclude_body_from_categories(gid, body_name=u"", keep_prefixes=None):
    """把这个体从所有类别级尺寸/方法控制项里摘掉
       - 按"控制项类型"识别，不依赖名字
       - 该体自己的 SIZE_FIX_<体名> / METHOD_<体名> 保持不动
    """
    removed, errors = [], []
    own = set()
    if body_name:
        own.add(u"SIZE_FIX_%s" % body_name)
        own.add(u"METHOD_%s" % body_name)
    mesh = _mesh()
    for c in (mesh.Children or []):
        nm = _txt(_safe(c, "Name", u""))
        if nm in own:
            continue
        if not _is_sizing_or_method(c):
            continue
        ids = _ctl_ids(c)
        if not ids or gid not in ids:
            continue
        rest = [x for x in ids if x != gid]
        if not rest:
            try:
                c.Delete()
                removed.append(nm + u"(整项删除)")
            except Exception as e:
                errors.append(u"%s: %s" % (nm, _txt(e)[:50]))
            continue
        if _set_ctl_ids(c, rest):
            removed.append(u"%s(%d→%d)" % (nm, len(ids), len(rest)))
        else:
            errors.append(u"%s: 更新范围失败" % nm)
    return removed, errors

def op_probe_ctrl_scope(payload):
    """列出控制项及其包含的体 Id，检查某体是否在其中"""
    name = _txt((payload or {}).get("body") or u"")
    gid = None
    for b in _bodies():
        if _txt(_safe(b, "Name", u"")) == name:
            try:
                gid = int(b.GetGeoBody().Id)
            except Exception:
                pass
            break
    out = {"ok": True, "body": name, "gid": gid, "controls": []}
    for c in (_mesh().Children or []):
        nm = _txt(_safe(c, "Name", u""))
        if not (nm.startswith(u"SIZE_") or nm.startswith(u"METHOD_")):
            continue
        ids = _ctl_ids(c)
        out["controls"].append({"name": nm, "count": len(ids),
                                "has_body": (gid in ids) if gid else None})
    return out


def op_probe_states(payload):
    from collections import Counter
    cnt = Counter()
    detail = []
    for b in _bodies():
        st = _txt(_safe(b, 'ObjectState', u''))
        cnt[st] += 1
        if st != u'Meshed' and len(detail) < 12:
            detail.append({'name': _txt(_safe(b, 'Name', u'')), 'state': st})
    out = {'ok': True, 'counts': dict(cnt), 'non_meshed': detail}
    try:
        out['mesh_state'] = _txt(_safe(_mesh(), 'ObjectState', u''))
    except Exception:
        pass
    return out


_STAMP_PATH = os.path.join(IPC_DIR, "mesh_stamp.json")


def _stamp_read():
    try:
        with open(_STAMP_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _stamp_write(**kw):
    d = _stamp_read()
    d.update(kw)
    try:
        with open(_STAMP_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def _touch_ctrl():
    """控制项被改动：网格变过期"""
    _stamp_write(ctrl_ts=time.time())


def _touch_mesh():
    """网格刚生成"""
    _stamp_write(mesh_ts=time.time())


def op_probe_app_messages(payload):
    out = {'ok': True, 'paths': []}
    ext = _extapi()
    cands = [('Application', ext.Application),
             ('Model', _model()),
             ('Mesh', _mesh())]
    for nm, obj in cands:
        for attr in ('Messages', 'MessageList', 'GetMessages'):
            try:
                v = getattr(obj, attr)
                items = []
                try:
                    for m in v:
                        items.append(_txt(_safe(m, 'DisplayString') or _safe(m, 'Text') or m))
                except Exception:
                    items = [_txt(v)]
                out['paths'].append({'obj': nm, 'attr': attr, 'count': len(items),
                                     'sample': items[:6]})
            except Exception as e:
                out['paths'].append({'obj': nm, 'attr': attr, 'err': _txt(e)[:50]})
    return out


def op_probe_msg_fields(payload):
    out = {'ok': True, 'items': []}
    try:
        msgs = list(_extapi().Application.Messages)
    except Exception as e:
        return {'ok': False, 'error': _txt(e)[:100]}
    for m in msgs[:6]:
        item = {}
        item['attrs'] = [n for n in dir(m) if not n.startswith('_')][:25]
        for a in ('DisplayString', 'Text', 'DetailText', 'Severity', 'Source',
                  'ObjectName', 'Object', 'EntityName', 'TimeStamp'):
            try:
                item[a] = _txt(getattr(m, a))[:120]
            except Exception:
                pass
        out['items'].append(item)
    return out


def op_probe_controls_all(payload):
    out = {'ok': True, 'items': []}
    mesh = _mesh()
    for c in (mesh.Children or []):
        nm = _txt(_safe(c, 'Name', u''))
        try:
            cat = _txt(_safe(c, 'DataModelObjectCategory', u''))
        except Exception:
            cat = u''
        ids = _ctl_ids(c)
        out['items'].append({'name': nm, 'cat': cat, 'ids': len(ids)})
    return out


def op_mesh_messages(payload):
    """只返回本次网格运行之后产生的消息
       since 可由 payload 指定；默认用 mesh_start_ts（点生成网格时记录）
    """
    payload = payload or {}
    since = payload.get("since")
    src_note = "payload"
    if since in (None, u"", 0):
        st = _stamp_read()
        since = float(st.get("mesh_start_ts") or 0)
        src_note = "mesh_start_ts"
    try:
        since = float(since)
    except Exception:
        since = 0.0

    out = {"ok": True, "items": [], "since_source": src_note,
           "since": since, "raw_total": 0, "skipped_old": 0}
    try:
        msgs = list(_extapi().Application.Messages)
    except Exception as e:
        return {"ok": False, "error": _txt(e)[:120]}
    out["raw_total"] = len(msgs)

    import datetime as _dt
    for m in msgs:
        try:
            text = _txt(_safe(m, "DisplayString", u""))
            sev = _txt(_safe(m, "Severity", u""))
            ts = _txt(_safe(m, "TimeStamp", u""))
        except Exception:
            continue
        # 只保留本次运行之后的消息
        if since > 0 and ts:
            try:
                _t = _dt.datetime.strptime(ts.strip(), "%Y/%m/%d %H:%M:%S")
                if _t.timestamp() < since - 5:
                    out["skipped_old"] += 1
                    continue
            except Exception:
                pass
        sname = u""
        try:
            s = _safe(m, "Source")
            if s is not None:
                sname = _txt(_safe(s, "Name", u""))
        except Exception:
            pass
        out["items"].append({"text": text[:300], "severity": sev,
                             "source": sname, "time": ts})
    return out

def op_ping(payload):
    return {"ok": True, "msg": u"bridge alive", "version": AGENT_VERSION,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")}


def op_status(payload):
    mesh = _mesh()
    out = {"ok": True}
    # 全局设置
    settings = {}
    for attr in ("ElementSize", "MinimumSize", "DefeatureTolerance",
                 "CurvatureNormalAngle", "CaptureCurvature", "CaptureProximity",
                 "GrowthRate"):
        try:
            settings[attr] = _txt(getattr(mesh, attr))
        except Exception:
            settings[attr] = u"-"
    out["settings"] = settings
    out["mesh_state"] = _txt(getattr(mesh, "ObjectState", u"-"))
    out["mesh_detail_state"] = _txt(getattr(mesh, "State", u"-"))
    out["agent_version"] = AGENT_VERSION

    # 单位系统
    try:
        out["unit_system"] = _txt(_extapi().DataModel.Project.UnitSystem)
    except Exception:
        out["unit_system"] = u"-"

    # 规模
    try:
        out["elements"], out["nodes"] = _counts()
    except Exception:
        out["elements"], out["nodes"] = 0, 0

    # 体状态
    counts = {}
    failed = []
    total = 0
    try:
        bodies = _bodies()
    except Exception:
        bodies = []
    for b in (bodies or []):
        try:
            if b.Suppressed:
                counts[u"抑制"] = counts.get(u"抑制", 0) + 1
                continue
        except Exception:
            pass
        total += 1
        st = u"?"
        try:
            st = _txt(b.ObjectState)
        except Exception:
            pass
        counts[st] = counts.get(st, 0) + 1
        if st != u"Meshed":
            failed.append({"name": _txt(b.Name), "state": st})
    out["body_total"] = total
    out["body_states"] = counts
    out["failed_bodies"] = failed

    # 控制项
    ctrls = []
    try:
        for c in (_model().Mesh.Children or []):
            item = {"name": _txt(getattr(c, "Name", u"?")),
                    "type": _txt(getattr(c, "Type", u""))}
            try:
                item["size"] = _txt(c.ElementSize)
            except Exception:
                item["size"] = u""
            try:
                item["scope"] = len(list(c.Location.Ids))
            except Exception:
                item["scope"] = 0
            ctrls.append(item)
    except Exception:
        pass
    out["controls"] = ctrls
    out["control_count"] = len(ctrls)
    return out


def _geom_group_ids():
    """Geometry 分组名 -> 体中 id 列表（用名字映射，避免复杂枚举）"""
    out = {}
    try:
        id_by_name = {}
        for b in _bodies():
            try:
                id_by_name[_txt(_safe(b, "Name", u""))] = int(b.Id)
            except Exception:
                pass
    except Exception:
        return out

    def cat_of(o):
        try:
            return _txt(o.DataModelObjectCategory)
        except Exception:
            return u""

    def names_in(node):
        acc = []
        try:
            kids = list(node.Children)
        except Exception:
            kids = []
        for k in kids:
            c = cat_of(k)
            if "Body" in c:
                acc.append(_txt(_safe(k, "Name", u"")))
            elif "Part" in c:
                try:
                    for x in k.Children:
                        if "Body" in cat_of(x):
                            acc.append(_txt(_safe(x, "Name", u"")))
                except Exception:
                    pass
                if not acc:
                    acc.append(_txt(_safe(k, "Name", u"")))
            elif "roupingFolder" in c:
                acc.extend(names_in(k))
        return acc

    def walk(nodes, depth):
        if depth > 4:
            return
        for x in nodes:
            c = cat_of(x)
            if "roupingFolder" in c:
                g = _txt(_safe(x, "Name", u""))
                ids = [id_by_name[n] for n in names_in(x) if n in id_by_name]
                if g and ids:
                    out[g] = ids
            try:
                kids = list(x.Children)
            except Exception:
                kids = []
            if kids:
                walk(kids, depth + 1)

    try:
        walk(list(_model().Geometry.Children), 0)
    except Exception:
        pass
    return out



def op_dump_geom_ids(payload):
    """调试：Geometry 分组 -> 体 id 的解析结果"""
    m = _geom_group_ids()
    return {"ok": True, "count": len(m),
            "groups": [{"name": k, "ids": len(v)} for k, v in m.items()]}


def op_probe_folder(payload):
    """探测：分组文件夹在哪一层、子节点是什么类别"""
    model = _model()
    out = {"ok": True, "paths": []}

    def cat_of(o):
        try:
            return _txt(o.DataModelObjectCategory)
        except Exception:
            return u"?"

    def nm(o):
        try:
            return _txt(o.Name)
        except Exception:
            return u"?"

    def walk(nodes, path, depth):
        if depth > 5 or len(out["paths"]) > 40:
            return
        for x in nodes:
            c = cat_of(x)
            p = path + "/" + nm(x)
            if "roupingFolder" in c:
                kids = []
                try:
                    for k in x.Children:
                        kids.append({"cat": cat_of(k), "name": nm(k)})
                except Exception as e:
                    kids = [{"err": _txt(e)[:60]}]
                out["paths"].append({"path": p, "cat": c, "kids": kids[:6],
                                     "kid_count": len(kids)})
            try:
                kids2 = list(x.Children)
            except Exception:
                kids2 = []
            if kids2:
                walk(kids2, p, depth + 1)

    try:
        walk([model], u"Model", 0)
    except Exception as e:
        return {"ok": False, "error": _txt(e)}
    return out


def op_apply(payload):
    try:
        _touch_ctrl()
    except Exception:
        pass
    """应用全局设置 + 类别尺寸
    payload = {
      "settings": {"ElementSize": 40, "MinimumSize": 10, "DefeatureTolerance": 1,
                   "CurvatureNormalAngle": 18, "CaptureCurvature": true,
                   "CaptureProximity": false},
      "sizes": {"垫板": 40, "横梁": 35, ...}
    }
    """
    from Ansys.Core.Units import Quantity
    mesh = _mesh()
    applied = {"settings": [], "sizes": [], "errors": []}

    settings = (payload or {}).get("settings") or {}
    unit_map = {
        "ElementSize": u"%s [mm]",
        "MinimumSize": u"%s [mm]",
        "DefeatureTolerance": u"%s [mm]",
        "CurvatureNormalAngle": u"%s [deg]",
    }
    for key, value in settings.items():
        try:
            if key in unit_map:
                setattr(mesh, key, Quantity(unit_map[key] % value))
            else:
                setattr(mesh, key, bool(value))
            applied["settings"].append(key)
        except Exception:
            applied["errors"].append(u"%s: %s" % (key, _tail(traceback.format_exc(), 200)))

    sizes = (payload or {}).get("sizes") or {}
    if sizes:
        # 先补建缺失的控制项（新模型 / 一键清理后都能用）；
        # 已存在同名控制项（含你自建的 Body Sizing）则直接改它的尺寸
        try:
            _recs = _body_records()
            _grouped, _owners = _plan_groups(_recs)
            cat_ids = {}
            for _cat, _items in _grouped.items():
                cat_ids[_cat] = [r["id"] for r in _items if r.get("id") is not None]
            # 控制项里没有的类别，从 Geometry 分组补上体
            try:
                for _g, _ids in _geom_group_ids().items():
                    if _g and _ids and not cat_ids.get(_g):
                        cat_ids[_g] = _ids
            except Exception:
                pass
            existing = {}
            for _cat, _ctrls in _owners.items():
                for _c in _ctrls:
                    _nm = _txt(_safe(_c, "Name", u""))
                    if _nm.startswith(u"METHOD_"):
                        continue
                    if _ctrl_size(_c) is not None or _nm.startswith(u"SIZE_"):
                        existing[_cat] = _c
                        break
            _total_new = len([c for c in sizes
                              if c not in existing and c not in cat_ids])
            _done_new = 0
            for cat, val in sizes.items():
                if cat in existing:
                    continue
                if _cancelled():
                    applied["errors"].append(u"已被用户停止（补建控制项阶段）")
                    applied["cancelled"] = True
                    break
                ids = cat_ids.get(cat) or []
                if not ids:
                    continue
                _done_new += 1
                _progress(u"apply", u"补建控制项", _done_new, max(_total_new, 1))
                ctrl, err = _new_sizing(cat, ids, val)
                if ctrl is None:
                    applied["errors"].append(u"新建 SIZE_%s 失败: %s" % (cat, err))
                else:
                    applied.setdefault("created", []).append(u"SIZE_%s=%g" % (cat, val))
        except Exception:
            applied["errors"].append(u"补建控制项异常: %s" % _tail(traceback.format_exc(), 300))

        for c in (_model().Mesh.Children or []):
            try:
                nm = _txt(c.Name)
            except Exception:
                continue
            if nm.startswith(u"SIZE_FIX_") or nm.startswith(u"METHOD_"):
                continue      # 方法控制项没有 ElementSize
            cat = _ctrl_category(nm)
            if cat not in sizes:
                continue
            if _cancelled():
                applied["errors"].append(u"已被用户停止")
                applied["cancelled"] = True
                break
            try:
                v = float(sizes[cat])
                v = max(SIZE_MIN, min(SIZE_MAX, v))
                c.ElementSize = Quantity(u"%.3f [mm]" % v)
                if cat not in applied["sizes"]:
                    applied["sizes"].append(cat)
            except Exception:
                applied["errors"].append(u"%s: %s" % (cat, _tail(traceback.format_exc(), 200)))

    methods = (payload or {}).get("methods") or {}
    if methods:
        try:
            applied["methods"] = _apply_methods(methods, (payload or {}).get("divisions") or {})
        except Exception:
            applied["errors"].append(u"methods: %s" % _tail(traceback.format_exc(), 400))

    applied["mesh_state"] = _txt(getattr(mesh, "ObjectState", u"-"))
    applied["ok"] = True
    # 自动去重：避免"点了两次应用"产生两套控制项
    try:
        _dd = op_dedupe_controls({})
        if _dd.get("removed_count"):
            applied["deduped"] = _dd.get("removed")
    except Exception:
        pass
    return applied


def op_set_by_label(payload):
    """payload={"control","prop","label","max":N}"""
    cname = _txt((payload or {}).get("control") or u"")
    pname = _txt((payload or {}).get("prop") or u"")
    label = _txt((payload or {}).get("label") or u"")
    maxn = int((payload or {}).get("max") or 12)
    ctrl = None
    for c in (_mesh().Children or []):
        if _txt(_safe(c, "Name", u"")) == cname:
            ctrl = c
            break
    if ctrl is None:
        return {"ok": False, "error": u"找不到 %s" % cname}
    try:
        prop = ctrl.PropertyByAPIName(pname)
    except Exception as e:
        return {"ok": False, "error": _txt(e)}

    original = prop.InternalValue
    original_label = _txt(prop.StringValue)
    mapping = []

    # 先把当前值也记进映射
    mapping.append({"raw": _txt(original), "label": original_label})

    found = None
    # 1) 先试字符串
    try:
        prop.InternalValue = label
        cur = _txt(prop.StringValue)
        mapping.append({"raw": label, "label": cur})
        if cur.strip().lower() == label.strip().lower():
            found = label
    except Exception:
        pass

    # 2) 逐个数值枚举，读回标签
    if found is None:
        for i in range(0, maxn + 1):
            try:
                prop.InternalValue = i
                cur = _txt(prop.StringValue)
                mapping.append({"raw": i, "label": cur})
                if cur.strip().lower() == label.strip().lower():
                    found = i
                    break
            except Exception:
                continue

    if found is None:
        # 恢复原值
        try:
            prop.InternalValue = original
        except Exception:
            pass
        return {"ok": False, "error": u"没有找到标签为 %s 的取值" % label,
                "mapping": mapping, "restored": original_label}

    return {"ok": True, "set": found, "label_now": _txt(prop.StringValue),
            "mapping": mapping}


def op_set_string_value(payload):
    """按 StringValue 设置控制项属性；payload={"control","props":{"API名":"界面文字"}}"""
    cname = _txt((payload or {}).get("control") or u"")
    wants = (payload or {}).get("props") or {}
    ctrl = None
    for c in (_mesh().Children or []):
        if _txt(_safe(c, "Name", u"")) == cname:
            ctrl = c
            break
    if ctrl is None:
        return {"ok": False, "error": u"找不到 %s" % cname}

    results = []
    ok_all = True
    for pn, val in wants.items():
        entry = {"prop": pn, "want": val}
        try:
            prop = ctrl.PropertyByAPIName(_txt(pn))
            entry["before"] = _txt(prop.StringValue)
            prop.StringValue = _txt(val)
            entry["after"] = _txt(prop.StringValue)
            entry["ok"] = (entry["after"].strip().lower() == _txt(val).strip().lower())
            try:
                entry["caption"] = _txt(prop.Caption)
            except Exception:
                pass
        except Exception as e:
            entry["ok"] = False
            entry["error"] = _txt(e)
        ok_all = ok_all and entry.get("ok", False)
        results.append(entry)
    return {"ok": ok_all, "control": cname, "results": results}


def op_prop_dir(payload):
    """列出属性对象成员，并尝试读取合法取值"""
    cname = _txt((payload or {}).get("control") or u"")
    pname = _txt((payload or {}).get("prop") or u"")
    ctrl = None
    for c in (_mesh().Children or []):
        if _txt(_safe(c, "Name", u"")) == cname:
            ctrl = c
            break
    if ctrl is None:
        return {"ok": False, "error": u"找不到 %s" % cname}
    try:
        prop = ctrl.PropertyByAPIName(pname)
    except Exception as e:
        return {"ok": False, "error": _txt(e)}

    out = {"control": cname, "prop": pname, "members": [], "values": None,
           "display": None}
    try:
        out["members"] = [n for n in dir(prop) if not n.startswith("_")]
    except Exception as e:
        out["members_error"] = _txt(e)
    for attr in ("DisplayValue", "Value", "ValueString", "StringValue",
                 "SelectedValue", "DisplayString"):
        try:
            out["display_%s" % attr] = _txt(getattr(prop, attr))
        except Exception:
            pass
    for fn in ("GetValidValues", "GetValues", "GetAllowedValues",
               "GetPossibleValues", "GetEnumValues"):
        try:
            f = getattr(prop, fn)
            out["values"] = [_txt(v) for v in f()]
            out["values_via"] = fn
            break
        except Exception:
            pass
    for attr in ("ValidValues", "Options", "AllowedValues", "OptionsList"):
        try:
            v = getattr(prop, attr)
            vals = []
            for x in v:
                try:
                    vals.append(_txt(x))
                except Exception:
                    vals.append(repr(x))
            out["values"] = vals
            out["values_via"] = attr
            break
        except Exception:
            pass
    return {"ok": True, "result": out}


def op_src_target(payload):
    """读写 SourceTargetSelection（Src/Trg Selection）"""
    cname = _txt((payload or {}).get("control") or u"")
    set_to = _txt((payload or {}).get("set_to") or u"")
    ctrl = None
    for c in (_mesh().Children or []):
        if _txt(_safe(c, "Name", u"")) == cname:
            ctrl = c
            break
    if ctrl is None:
        return {"ok": False, "error": u"找不到 %s" % cname}

    out = {"control": cname}
    try:
        prop = ctrl.PropertyByAPIName(u"SourceTargetSelection")
    except Exception as e:
        return {"ok": False, "error": u"取不到属性: %s" % _txt(e)}

    for attr in ("DisplayValue", "InternalValue", "ParameterType", "Name"):
        try:
            v = getattr(prop, attr)
            out[attr] = _txt(v)
        except Exception:
            pass

    # 枚举取值
    options = []
    try:
        iv = prop.InternalValue
        import System
        t = iv.GetType()
        out["enum_type"] = _txt(t)
        options = list(System.Enum.GetNames(t))
        out["options"] = options
    except Exception as e:
        out["options_error"] = _txt(e)

    if set_to:
        done = False
        # 1) 枚举名精确/包含匹配
        for opt in options:
            if opt.lower() == set_to.lower() or set_to.lower() in opt.lower():
                try:
                    import System
                    iv = prop.InternalValue
                    v = System.Enum.Parse(iv.GetType(), opt, True)
                    prop.InternalValue = v
                    done = True
                    out["set_to"] = opt
                    break
                except Exception as e:
                    out["set_error"] = _txt(e)
        # 2) 直接写字符串
        if not done:
            try:
                prop.InternalValue = set_to
                done = True
                out["set_to"] = set_to
            except Exception as e:
                out["set_error"] = _txt(e)
        out["set_ok"] = done
        try:
            out["now_display"] = _txt(prop.DisplayValue)
        except Exception:
            pass
    return {"ok": True, "result": out}


def op_ui_props(payload):
    """列出控制项的可见属性（界面名）、显示值，以及指定属性的合法取值"""
    cname = _txt((payload or {}).get("control") or u"")
    want = (payload or {}).get("names") or []
    ctrl = None
    for c in (_mesh().Children or []):
        if _txt(_safe(c, "Name", u"")) == cname:
            ctrl = c
            break
    if ctrl is None:
        return {"ok": False, "error": u"找不到 %s" % cname}

    out = {"control": cname, "visible_names": [], "props": {}}
    try:
        out["visible_names"] = list(ctrl.GetVisiblePropertyNames())
    except Exception as e:
        out["visible_error"] = _txt(e)

    for nm in (want or out["visible_names"][:40]):
        entry = {}
        try:
            prop = ctrl.PropertyByName(_txt(nm))
        except Exception as e:
            out["props"][nm] = {"error": _txt(e)}
            continue
        for attr in ("DisplayValue", "InternalValue", "Name"):
            try:
                entry[attr] = _txt(getattr(prop, attr))
            except Exception:
                pass
        for attr in ("Options", "ValidValues", "AllowedValues", "Range"):
            try:
                v = getattr(prop, attr)
                entry[attr] = [_txt(x) for x in v] if hasattr(v, "__iter__") else _txt(v)
            except Exception:
                pass
        # 有些版本提供 GetValidValues()
        try:
            entry["valid"] = [_txt(x) for x in prop.GetValidValues()]
        except Exception:
            pass
        out["props"][nm] = entry
    return {"ok": True, "result": out}


def op_dir_mesh(payload):
    """列出 Mesh 对象上的可用成员（可按关键字过滤）"""
    keys = [k.lower() for k in ((payload or {}).get("filters")
                                or ["add", "sweep", "method", "size", "defeat"])]
    mesh = _mesh()
    out = {}
    try:
        names = [n for n in dir(mesh) if not n.startswith("_")]
    except Exception as e:
        return {"ok": False, "error": _txt(e)}
    for k in keys:
        out[k] = sorted(n for n in names if k in n.lower())
    return {"ok": True, "count": len(names), "groups": out}


def op_dir_control(payload):
    """列出控制项的 .NET 属性名（可按关键字过滤）"""
    cname = _txt((payload or {}).get("control") or u"")
    key = (_txt((payload or {}).get("filter") or u"")).lower()
    ctrl = None
    for c in (_mesh().Children or []):
        if _txt(_safe(c, "Name", u"")) == cname:
            ctrl = c
            break
    if ctrl is None:
        return {"ok": False, "error": u"找不到 %s" % cname}
    names = []
    try:
        for n in dir(ctrl):
            if n.startswith(u"_"):
                continue
            if key and key not in n.lower():
                continue
            names.append(n)
    except Exception as e:
        return {"ok": False, "error": _txt(e)}
    return {"ok": True, "names": sorted(names), "count": len(names)}


def op_set_src_thin(payload):
    """尝试多种属性名/取值，把 Sweep 的源面选择设为 Automatic Thin"""
    cname = _txt((payload or {}).get("control") or u"")
    ctrl = None
    for c in (_mesh().Children or []):
        if _txt(_safe(c, "Name", u"")) == cname:
            ctrl = c
            break
    if ctrl is None:
        return {"ok": False, "error": u"找不到 %s" % cname}

    names = [u"SrcSelection", u"SrcTrgSelection", u"SrcTrgSelectionType",
             u"SourceSelection", u"SweepSrcSelection"]
    values = [u"AutomaticThin", u"Automatic_Thin", u"AutoThin",
              u"Automatic Thin", u"Thin"]
    tried, ok_info = [], None

    # 先看看每个属性能不能枚举出候选值
    avail = {}
    for pn in names:
        try:
            obj = ctrl.PropertyByAPIName(pn)
            cur = obj.InternalValue
            entry = {"current": _txt(cur)}
            try:
                import System
                entry["options"] = list(System.Enum.GetNames(cur.GetType()))
            except Exception:
                pass
            avail[pn] = entry
        except Exception as e:
            avail[pn] = {"error": _txt(e)}

    for pn, info in avail.items():
        if "options" not in info:
            continue
        for v in values:
            if v not in info["options"]:
                continue
            try:
                obj = ctrl.PropertyByAPIName(pn)
                import System
                cur = obj.InternalValue
                t = cur.GetType()
                obj.InternalValue = System.Enum.Parse(t, v, True)
                tried.append(u"%s=%s 成功" % (pn, v))
                ok_info = {"prop": pn, "value": v}
                break
            except Exception as e:
                tried.append(u"%s=%s 失败: %s" % (pn, v, _txt(e)[:60]))
        if ok_info:
            break

    return {"ok": ok_info is not None, "set": ok_info,
            "available": avail, "tried": tried}


def op_method_props(payload):
    """读取方法控制项的可设置属性与枚举值；可选按 payload["set"] 赋值"""
    cname = _txt((payload or {}).get("control") or u"")
    ctrl = None
    for c in (_mesh().Children or []):
        if _txt(_safe(c, "Name", u"")) == cname:
            ctrl = c
            break
    if ctrl is None:
        return {"ok": False, "error": u"找不到控制项 %s" % cname}

    out = {"control": cname, "props": {}, "set_result": []}
    # 常见的扫掠属性
    prop_names = (payload or {}).get("names") or [
        u"Method", u"SrcSelection", u"FreeFaceMeshType",
        u"SweepNumberDivisions", u"SweepESizeType", u"SweepSizeBehavior",
        u"SweepThickness", u"SweepBiasType", u"SweepBiasValue",
        u"ElementOrder", u"Algorithm", u"SweepElementSize"]
    for pn in prop_names:
        try:
            val = getattr(ctrl, pn)
            txt = _txt(val)
            entry = {"value": txt}
            try:
                import System
                ent = System.Enum.GetNames(val.GetType())
                entry["options"] = list(ent)
            except Exception:
                pass
            out["props"][pn] = entry
        except Exception as e:
            out["props"][pn] = {"error": _txt(e)}

    wants = (payload or {}).get("set") or {}
    for pn, target in wants.items():
        done = False
        # 1) 直接按枚举名赋值
        try:
            cur = getattr(ctrl, pn)
            import System
            t = cur.GetType()
            v = System.Enum.Parse(t, _txt(target), True)
            setattr(ctrl, pn, v)
            out["set_result"].append(u"%s = %s（枚举）" % (pn, target))
            done = True
        except Exception:
            done = False
        if not done:
            # 2) 通过 PropertyByAPIName 写 InternalValue
            try:
                obj = ctrl.PropertyByAPIName(_txt(pn))
                obj.InternalValue = target
                out["set_result"].append(u"%s = %s（PropertyByAPIName）" % (pn, target))
                done = True
            except Exception as e:
                out["set_result"].append(u"%s 设置失败: %s" % (pn, _txt(e)))
    return {"ok": True, "result": out}


def _prop_string(ctrl, api_name):
    """读取属性：返回 (是否存在, 界面值, API名)"""
    try:
        prop = ctrl.PropertyByAPIName(api_name)
        return True, _txt(prop.StringValue), api_name
    except Exception:
        return False, u"", api_name


def op_cat_params_v2(payload):
    """精简参数面板：
       网格级: Defeature Size / Capture Curvature / Min(MinimumSize) / Nor(CurvatureNormalAngle)
       扫掠时: Src/Trg Selection（全选项）+ 层数(SweepNumberDivisions)
       payload={"category","control","method","set":{"api":value}}
    """
    cat = _txt((payload or {}).get("category") or u"")
    cname = _txt((payload or {}).get("control") or u"")
    method = _txt((payload or {}).get("method") or u"")
    mesh = _mesh()

    ctrl = None
    if cname:
        for c in (_mesh().Children or []):
            if _txt(_safe(c, "Name", u"")) == cname:
                ctrl = c
                break
    if ctrl is None and cat:
        for c in (_mesh().Children or []):
            if _txt(_safe(c, "Name", u"")) == cat:
                ctrl = c
                break
    if method in (u"", u"Automatic") and ctrl is not None:
        try:
            method = _txt(_safe(ctrl, "Method", u"Automatic"))
        except Exception:
            pass

    out = {"ok": True, "category": cat, "method": method, "params": []}

    def add(api, label, kind, value, options=None, scope="mesh"):
        item = {"api": api, "label": label, "kind": kind, "value": value,
                "scope": scope}
        if options:
            item["options"] = options
        out["params"].append(item)

    # ---- 网格级参数 ----
    try:
        add(u"DefeatureTolerance", u"Defeature Size",
            "length", _txt(getattr(mesh, "DefeatureTolerance")))
    except Exception:
        pass
    try:
        add(u"CaptureCurvature", u"Capture Curvature", "bool",
            bool(getattr(mesh, "CaptureCurvature")))
    except Exception:
        pass
    try:
        add(u"MinimumSize", u"Min（最小尺寸）", "length", _txt(getattr(mesh, "MinimumSize")))
    except Exception:
        pass
    try:
        add(u"CurvatureNormalAngle", u"Nor（曲率法向角）", "angle",
            _txt(getattr(mesh, "CurvatureNormalAngle")))
    except Exception:
        pass

    # ---- 扫掠参数（只要方法=扫掠就显示；没有控制项时给默认值） ----
    if method == u"Sweep":
        opts = [u"Automatic", u"Manual Source",
                u"Manual Source and Target", u"Manual Target",
                u"Automatic Thin"]
        val = u"Automatic"
        if ctrl is not None:
            try:
                ok, v2, _ = _prop_string(ctrl, u"SourceTargetSelection")
                if ok and v2:
                    val = v2
            except Exception:
                pass
        add(u"SourceTargetSelection", u"Src/Trg Selection", "enum", val,
            options=opts, scope="control" if ctrl is not None else "pending")

        div = u"2"                     # 扫掠默认 2 层
        if ctrl is not None:
            try:
                _d = _txt(getattr(ctrl, "SweepNumberDivisions")).strip()
                # 0 / 空 都视为"没设过"，显示默认 2
                if _d and not _d.startswith("0"):
                    div = _d
            except Exception:
                pass
        add(u"SweepNumberDivisions", u"层数（Number of Divisions）", "int", div,
            scope="control" if ctrl is not None else "pending")

    # ---- 应用修改 ----
    sets = (payload or {}).get("set") or {}
    applied, errors = [], []
    if sets and ctrl is None:
        return {"ok": False,
                "error": u"该类别还没有扫掠控制项：请先把方法设为扫掠并点「应用设置与尺寸」"}
    for api, val in sets.items():
            try:
                api = _txt(api)
                if api == u"SourceTargetSelection":
                    prop = ctrl.PropertyByAPIName(api)
                    done = False
                    for i in range(0, 8):
                        try:
                            prop.InternalValue = i
                            if _txt(prop.StringValue).strip().lower() == _txt(val).strip().lower():
                                done = True
                                break
                        except Exception:
                            break
                    if not done:
                        raise Exception("no matching label")
                    applied.append(api)
                elif api == u"SweepNumberDivisions":
                    setattr(ctrl, api, int(val))
                    applied.append(api)
                elif api == u"CaptureCurvature":
                    setattr(mesh, api, bool(val))
                    applied.append(api)
                else:
                    from Ansys.Core.Units import Quantity
                    unit = u" [deg]" if api == u"CurvatureNormalAngle" else u" [mm]"
                    setattr(mesh, api, Quantity(u"%s%s" % (val, unit)))
                    applied.append(api)
            except Exception:
                errors.append(u"%s: %s" % (api, _tail(traceback.format_exc(), 160)))
    out["applied"] = applied
    out["errors"] = errors
    return out


def _norm(s):
    out = []
    for ch in _txt(s).lower():
        if ch.isalnum():
            out.append(ch)
    return u"".join(out)


def op_quality_one(payload):
    """只计算一个质量指标（避免一次切换多个指标导致 Mechanical 内存爆掉）"""
    mesh = _mesh()
    nm = _txt((payload or {}).get("name") or u"Element Quality")
    out = {"ok": True, "name": nm, "elements": 0, "nodes": 0}

    try:
        out["elements"] = int(_safe(mesh, "Elements", 0))
        out["nodes"] = int(_safe(mesh, "Nodes", 0))
    except Exception:
        pass

    # 枚举名匹配（'Element Quality' -> ElementQuality）
    def _n(x):
        s = []
        for ch in _txt(x).lower():
            if ch.isalnum():
                s.append(ch)
        return u"".join(s)

    enum_name = None
    try:
        import System
        from Ansys.Mechanical.DataModel.Enums import MeshMetricType
        want = _n(nm)
        names = list(System.Enum.GetNames(MeshMetricType))
        for en in names:
            if _n(en) == want:
                enum_name = en
                break
        if enum_name is None:
            for en in names:
                if want and (want in _n(en) or _n(en) in want):
                    enum_name = en
                    break
    except Exception:
        enum_name = None

    set_ok = False
    if enum_name:
        try:
            from Ansys.Mechanical.DataModel.Enums import MeshMetricType
            mesh.MeshMetric = getattr(MeshMetricType, enum_name)
            set_ok = True
            out["enum"] = enum_name
        except Exception:
            set_ok = False
    if not set_ok:
        try:
            mesh.MeshMetric = nm
            set_ok = True
        except Exception as e:
            return {"ok": False, "error": _txt(e), "name": nm}

    for attr, key in ((u"Minimum", u"min"), (u"Maximum", u"max"),
                      (u"Average", u"avg"), (u"StandardDeviation", u"std")):
        try:
            out[key] = _txt(getattr(mesh, attr))
        except Exception:
            pass
    return out


def op_quality_metrics(payload):
    """读取网格质量指标（Element Quality / Aspect Ratio / Skewness / Jacobian ...）"""
    mesh = _mesh()
    out = {"ok": True, "metrics": [], "unavailable": [], "elements": 0, "nodes": 0,
           "available_names": []}

    try:
        out["elements"] = int(_safe(mesh, "Elements", 0))
        out["nodes"] = int(_safe(mesh, "Nodes", 0))
    except Exception:
        pass

    # 1) 界面上的指标名
    names = []
    try:
        n = int(getattr(mesh, "NumberOfMeshQualityMetrics"))
        for i in range(n):
            try:
                nm = _txt(mesh.GetVolumeMeshQualityName(i))
                if nm:
                    names.append(nm)
            except Exception:
                pass
    except Exception:
        pass
    out["available_names"] = names
    only = (payload or {}).get("only")
    if only:
        wanted = set(_norm(x) for x in only)
        names = [x for x in names if _norm(x) in wanted]

    # 2) 枚举成员名（用于匹配）
    enum_pairs = []
    try:
        import System
        from Ansys.Mechanical.DataModel.Enums import MeshMetricType
        for en in System.Enum.GetNames(MeshMetricType):
            enum_pairs.append((_norm(en), en))
    except Exception:
        pass

    def resolve(nm):
        key = _norm(nm)
        for nk, en in enum_pairs:
            if nk == key:
                return en
        for nk, en in enum_pairs:
            if key and (key in nk or nk in key):
                return en
        return None

    for nm in names:
        item = {"name": nm}
        try:
            item["warning_limit"] = _txt(mesh.GetVolumeMeshQualityWarningLimit(nm))
            item["error_limit"] = _txt(mesh.GetVolumeMeshQualityErrorLimit(nm))
        except Exception:
            pass
        en = resolve(nm)
        set_ok = False
        if en:
            try:
                from Ansys.Mechanical.DataModel.Enums import MeshMetricType
                mesh.MeshMetric = getattr(MeshMetricType, en)
                set_ok = True
                item["enum"] = en
            except Exception:
                set_ok = False
        if not set_ok:
            try:
                mesh.MeshMetric = nm
                set_ok = True
            except Exception as e:
                out["unavailable"].append({"name": nm, "error": _txt(e)})
                continue
        for attr, key in ((u"Minimum", u"min"), (u"Maximum", u"max"),
                          (u"Average", u"avg"), (u"StandardDeviation", u"std")):
            try:
                item[key] = _txt(getattr(mesh, attr))
            except Exception:
                pass
        out["metrics"].append(item)

    return out


def op_probe_perbody2(payload):
    """再试几种读取顺序，看哪种能拿到逐体数值"""
    mesh = _mesh()
    res = {"tries": []}

    def snap(tag, b):
        d = {"tag": tag, "body": _txt(_safe(b, "Name", u""))}
        for attr in ("Minimum", "Maximum", "Average"):
            try:
                d[attr] = _txt(getattr(b, attr))
            except Exception as e:
                d[attr + "_err"] = _txt(e)
        res["tries"].append(d)

    try:
        from Ansys.Mechanical.DataModel.Enums import MeshMetricType
        mesh.MeshMetric = MeshMetricType.Skewness
    except Exception as e:
        res["set_err"] = _txt(e)

    body = None
    for b in _bodies():
        if _txt(_safe(b, "ObjectState", u"")) == u"Meshed":
            body = b
            break
    if body is None:
        return {"ok": False, "error": u"没有已划网格的体"}

    snap("set后立即读", body)
    time.sleep(1.0)
    snap("等1秒再读", body)
    try:
        res["body_metric_getter"] = _txt(body.MeshMetric)
    except Exception as e:
        res["body_metric_getter_err"] = _txt(e)
    snap("读MeshMetric后再读", body)
    try:
        _ = mesh.Minimum
    except Exception:
        pass
    snap("读全局后再读", body)
    try:
        body.GenerateMesh()
    except Exception as e:
        res["genmesh_err"] = _txt(e)[:80]
    snap("GenerateMesh后读", body)
    return {"ok": True, "result": res}


def op_probe_perbody(payload):
    out = {"steps": []}
    mesh = _mesh()

    def rec(k, v):
        out["steps"].append({k: _txt(v)})

    # 全局设 Skewness
    try:
        from Ansys.Mechanical.DataModel.Enums import MeshMetricType
        mesh.MeshMetric = MeshMetricType.Skewness
        rec("global_set", "Skewness")
        rec("global_min", mesh.Minimum)
        rec("global_max", mesh.Maximum)
    except Exception as e:
        rec("global_error", e)

    # 取一个已划网格的体
    body = None
    for b in _bodies():
        if _txt(_safe(b, "ObjectState", u"")) == u"Meshed":
            body = b
            break
    if body is not None:
        rec("body", _safe(body, "Name", u""))
        try:
            from Ansys.Mechanical.DataModel.Enums import MeshMetricType
            body.MeshMetric = MeshMetricType.Skewness
            rec("body_metric_set", "Skewness")
        except Exception as e:
            rec("body_metric_error", e)
        for attr in ("Minimum", "Maximum", "Average", "StandardDeviation",
                     "Elements", "Nodes"):
            try:
                rec("body_" + attr, getattr(body, attr))
            except Exception as e:
                rec("body_" + attr + "_err", e)

    # 其它入口
    for path in ("DataModel.MeshData", "DataModel.Project.Model.MeshData"):
        try:
            obj = _extapi()
            for part in path.split("."):
                obj = getattr(obj, part)
            names = [n for n in dir(obj) if not n.startswith("_")]
            out["meshdata_path"] = path
            out["meshdata_members"] = [n for n in names
                                       if "etric" in n or "quality" in n.lower()][:20]
            break
        except Exception as e:
            out.setdefault("meshdata_try", []).append({path: _txt(e)})

    return {"ok": True, "result": out}


def op_quality_for_bodies(payload):
    """给指定体做质量分析：只设一次指标，只读这些体（范围小、开销低）"""
    mesh = _mesh()
    nm = _txt((payload or {}).get("metric") or u"Element Quality")
    names = [_txt(x) for x in ((payload or {}).get("names") or [])]
    out = {"ok": True, "metric": nm, "bodies": [], "elements": 0,
           "set_metric": False}

    def _n(x):
        s = []
        for ch in _txt(x).lower():
            if ch.isalnum():
                s.append(ch)
        return u"".join(s)

    enum_name = None
    try:
        import System
        from Ansys.Mechanical.DataModel.Enums import MeshMetricType
        want = _n(nm)
        names_all = list(System.Enum.GetNames(MeshMetricType))
        for en in names_all:
            if _n(en) == want:
                enum_name = en
                break
        if enum_name is None:
            for en in names_all:
                if want and (want in _n(en) or _n(en) in want):
                    enum_name = en
                    break
        if enum_name:
            mesh.MeshMetric = getattr(MeshMetricType, enum_name)
            out["set_metric"] = True
            out["enum"] = enum_name
    except Exception as e:
        out["metric_error"] = _txt(e)

    try:
        out["elements"] = int(_safe(mesh, "Elements", 0))
    except Exception:
        pass

    want_set = set(names)
    for b in _bodies():
        _sup = bool(_safe(b, "Suppressed", False))
        bn = _txt(_safe(b, "Name", u""))
        if want_set and bn not in want_set:
            continue
        item = {"name": bn}
        try:
            item["min"] = _num(_safe(b, "Minimum"))
            item["max"] = _num(_safe(b, "Maximum"))
            item["avg"] = _num(_safe(b, "Average"))
            item["std"] = _num(_safe(b, "StandardDeviation"))
        except Exception as e:
            item["error"] = _txt(e)
        try:
            item["elements"] = int(_safe(b, "Elements", 0))
        except Exception:
            item["elements"] = 0
        try:
            item["meshed"] = (_txt(_safe(b, "ObjectState", u"")) == u"Meshed")
        except Exception:
            item["meshed"] = False
        out["bodies"].append(item)
    return out


def op_read_body_quality(payload):
    """纯读取：不设置 MeshMetric，只读当前指标及各体的 min/max/avg。
       Mechanical 里已选好的指标才会被算过；本操作不触发任何重算。
    """
    mesh = _mesh()
    out = {"ok": True, "metric": None, "bodies": [], "elements": 0}

    try:
        out["metric"] = _txt(mesh.MeshMetric)
    except Exception as e:
        out["metric_error"] = _txt(e)
    try:
        out["elements"] = int(_safe(mesh, "Elements", 0))
    except Exception:
        pass
    try:
        out["global_min"] = _num(_safe(mesh, "Minimum"))
        out["global_max"] = _num(_safe(mesh, "Maximum"))
    except Exception:
        pass

    limit = int((payload or {}).get("limit") or 0)
    only = (payload or {}).get("names")
    only_set = set(_txt(x) for x in only) if only else None
    n = 0
    for b in _bodies():
        if _safe(b, "Suppressed", False):
            continue
        if only_set is not None and _txt(_safe(b, "Name", u"")) not in only_set:
            continue
        if limit and n >= limit:
            break
        n += 1
        item = {"name": _txt(_safe(b, "Name", u""))}
        try:
            item["min"] = _num(_safe(b, "Minimum"))
            item["max"] = _num(_safe(b, "Maximum"))
            item["avg"] = _num(_safe(b, "Average"))
            item["std"] = _num(_safe(b, "StandardDeviation"))
        except Exception as e:
            item["error"] = _txt(e)
        try:
            item["elements"] = int(_safe(b, "Elements", 0))
        except Exception:
            item["elements"] = 0
        try:
            item["meshed"] = (_txt(_safe(b, "ObjectState", u"")) == u"Meshed")
        except Exception:
            item["meshed"] = False
        out["bodies"].append(item)
    return out


def op_body_quality(payload):
    """按体读取质量指标：payload={"metric":"Element Quality"}
       全局只设置一次指标，然后逐个体读 Minimum/Maximum/Average（避免反复重算）
    """
    mesh = _mesh()
    nm = _txt((payload or {}).get("metric") or u"Element Quality")
    out = {"ok": True, "metric": nm, "bodies": [], "elements": 0}

    def _n(x):
        s = []
        for ch in _txt(x).lower():
            if ch.isalnum():
                s.append(ch)
        return u"".join(s)

    enum_name = None
    try:
        import System
        from Ansys.Mechanical.DataModel.Enums import MeshMetricType
        want = _n(nm)
        for en in System.Enum.GetNames(MeshMetricType):
            if _n(en) == want:
                enum_name = en
                break
        if enum_name is None:
            for en in System.Enum.GetNames(MeshMetricType):
                if want and (want in _n(en) or _n(en) in want):
                    enum_name = en
                    break
    except Exception:
        enum_name = None

    try:
        if enum_name:
            from Ansys.Mechanical.DataModel.Enums import MeshMetricType
            mesh.MeshMetric = getattr(MeshMetricType, enum_name)
            out["enum"] = enum_name
        else:
            mesh.MeshMetric = nm
    except Exception as e:
        return {"ok": False, "error": _txt(e), "metric": nm}

    try:
        out["elements"] = int(_safe(mesh, "Elements", 0))
    except Exception:
        pass
    # 关键：先读一次全局统计，触发 Mechanical 完成该指标的计算
    #      （否则逐体 Minimum/Maximum 会是 0）
    try:
        _gmin = mesh.Minimum
        _gmax = mesh.Maximum
        out["global_min"] = _num(_gmin)
        out["global_max"] = _num(_gmax)
    except Exception as e:
        out["global_error"] = _txt(e)
    time.sleep(0.8)

    limit = int((payload or {}).get("limit") or 0)
    seen = 0
    for b in _bodies():
        if _safe(b, "Suppressed", False):
            continue
        if limit and seen >= limit:
            break
        seen += 1
        item = {"name": _txt(_safe(b, "Name", u""))}
        try:
            item["min"] = _num(_safe(b, "Minimum"))
            item["max"] = _num(_safe(b, "Maximum"))
            item["avg"] = _num(_safe(b, "Average"))
            item["std"] = _num(_safe(b, "StandardDeviation"))
            if item["min"] is None:
                item["error"] = u"Minimum 不可读"
        except Exception as _e:
            item["error"] = _txt(_e)
        try:
            item["mesh_metric_now"] = _txt(b.MeshMetric)
        except Exception as _e2:
            item["mesh_metric_err"] = _txt(_e2)
        try:
            item["elements"] = int(_safe(b, "Elements", 0))
        except Exception:
            item["elements"] = 0
        try:
            item["meshed"] = (_txt(_safe(b, "ObjectState", u"")) == u"Meshed")
        except Exception:
            item["meshed"] = False
        out["bodies"].append(item)

    return out


def op_dir_body(payload):
    """列出体对象成员（默认过滤 metric/quality/element/node）"""
    want = _txt((payload or {}).get("body") or u"")
    keys = [k.lower() for k in ((payload or {}).get("filters")
                                or ["metric", "quality", "element", "node", "mesh"])]
    target = None
    for b in _bodies():
        if _txt(_safe(b, "Name", u"")) == want:
            target = b
            break
    if target is None:
        return {"ok": False, "error": u"找不到体 %s" % want}
    try:
        names = [n for n in dir(target) if not n.startswith("_")]
    except Exception as e:
        return {"ok": False, "error": _txt(e)}
    out = {}
    for k in keys:
        out[k] = sorted(n for n in names if k in n.lower())
    return {"ok": True, "count": len(names), "groups": out}


def op_body_counts(payload):
    """每个体的单元数/节点数，按单元数降序"""
    rows = []
    total_e = 0
    for b in _bodies():
        if _safe(b, "Suppressed", False):
            continue
        nm = _txt(_safe(b, "Name", u""))
        e = n = 0
        got = False
        for attr in (u"ElementCount", u"Elements"):
            try:
                e = int(getattr(b, attr))
                got = True
                break
            except Exception:
                continue
        for attr in (u"NodeCount", u"Nodes"):
            try:
                n = int(getattr(b, attr))
                break
            except Exception:
                continue
        if not got:
            # 估算：体积 / (当前类尺寸)^3
            vol = _vol_value(_safe(b, "Volume", 0))
            e = int(vol / (10.0 ** 3)) if vol else 0
        total_e += e
        rows.append({"name": nm, "elements": e, "nodes": n,
                     "meshed": (_txt(_safe(b, "ObjectState", u"")) == u"Meshed")})
    rows.sort(key=lambda d: -d["elements"])
    return {"ok": True, "count": len(rows), "total_elements": total_e, "bodies": rows}


def _shape_stats(body):
    """返回 (面数, 边数)；失败时 (None, None)"""
    try:
        geo = body.GetGeoBody()
    except Exception:
        return None, None
    try:
        nf = len(list(geo.Faces))
    except Exception:
        nf = None
    try:
        ne = len(list(geo.Edges))
    except Exception:
        ne = None
    return nf, ne


def op_diagnose(payload):
    """网格失败诊断：结合形状规整度给出原因与建议（不盲目推荐扫掠）"""
    limit = int((payload or {}).get("shape_limit") or 40)
    rows = []
    count = 0
    for b in _bodies():
        if _safe(b, "Suppressed", False):
            continue
        if _txt(_safe(b, "ObjectState", u"")) == u"Meshed":
            continue
        count += 1
        nm = _txt(_safe(b, "Name", u""))
        dims = sorted([_mm_value(_safe(b, "LengthX", 0)),
                       _mm_value(_safe(b, "LengthY", 0)),
                       _mm_value(_safe(b, "LengthZ", 0))])
        vol = _vol_value(_safe(b, "Volume", 0))
        bbox = dims[0] * dims[1] * dims[2] or 1.0
        fill = max(0.0, min(1.0, vol / bbox)) if vol else 0.0
        d1, d2, d3 = dims
        wall = d1 if fill >= 0.6 else max(0.3, 0.5 * d2 * (1.0 - max(0.0, 1.0 - fill) ** 0.5))
        aspect = (d3 / d1) if d1 else 0

        faces = edges = None
        if count <= limit:
            faces, edges = _shape_stats(b)

        # 形状规整度：面少、边少、有一定长细比 -> 扫掠成功率高
        regular = None
        if faces is not None and edges is not None:
            regular = (faces <= 30 and edges <= 90 and aspect >= 4.0)

        cause, fix, level = u"", u"", u"warn"
        if regular:
            level = u"high"
            cause = u"规整件（%d 面/%d 边）划不出：壁厚 %.1fmm 与单元尺寸不匹配" % (
                faces, edges, wall)
            fix = u"改扫掠 + Automatic Thin（形状规整，成功率高）"
        elif faces is not None and not regular:
            level = u"high"
            cause = u"形状不规则（%d 面/%d 边，长细比 %.0f）" % (faces, edges, aspect)
            fix = u"不建议扫掠；优先把局部尺寸降到 %.1fmm，或加大 Defeature Size / 虚拟拓扑" % max(
                6.0, round(2.5 * wall, 1))
        elif fill < 0.6 and wall < 3.0 and aspect >= 12:
            level = u"high"
            cause = u"极薄壁长梁（壁厚 %.1fmm、长细比 %.0f）" % (wall, aspect)
            fix = u"确认形状是否规整：规整则扫掠+Automatic Thin；否则加厚壁或做几何简化"
        elif fill < 0.6 and wall < 8.0:
            level = u"high"
            cause = u"薄壁件（壁厚 %.1fmm）壁内放不下单元" % wall
            fix = u"局部尺寸降到 %.1fmm 或加去特征" % max(6.0, round(2.5 * wall, 1))
        elif aspect >= 20:
            cause = u"长细比过大（%.0f）" % aspect
            fix = u"规整则扫掠；否则放大局部尺寸减少长条单元"
        else:
            cause = u"几何局部特征（面数多/小面多）"
            fix = u"加大 Defeature Size 或做虚拟拓扑"

        rows.append({"name": nm, "level": level, "cause": cause, "fix": fix,
                     "wall": round(wall, 2), "aspect": round(aspect, 1),
                     "faces": faces, "edges": edges, "regular": regular,
                     "dims": [round(x, 1) for x in dims]})
    return {"ok": True, "count": len(rows), "bodies": rows,
            "shape_checked": min(count, limit)}


def op_set_methods_thin(payload):
    """批量设 Sweep + Automatic Thin；payload={"categories":[...]}"""
    cats = (payload or {}).get("categories") or []
    recs = _body_records()
    grouped, _owners = _plan_groups(recs)
    ok_list, bad = [], []
    for cat in cats:
        ids = [r["id"] for r in grouped.get(cat, []) if r.get("id") is not None]
        if not ids:
            bad.append(u"%s：找不到体" % cat)
            continue
        ok, err = _set_method_for(cat, ids, u"Sweep")
        if not ok:
            bad.append(u"%s：%s" % (cat, err))
            continue
        # 设 Automatic Thin
        ctrl = None
        for c in (_mesh().Children or []):
            if _txt(_safe(c, "Name", u"")) == u"METHOD_" + cat:
                ctrl = c
                break
        if ctrl is None:
            bad.append(u"%s：方法控制项未创建" % cat)
            continue
        try:
            prop = ctrl.PropertyByAPIName(u"SourceTargetSelection")
            done = False
            for i in range(0, 8):
                prop.InternalValue = i
                if _txt(prop.StringValue).strip().lower() == u"automatic thin":
                    done = True
                    break
            if done:
                ok_list.append(cat)
            else:
                bad.append(u"%s：设不了 Automatic Thin" % cat)
        except Exception:
            bad.append(u"%s：%s" % (cat, _tail(traceback.format_exc(), 120)))
    return {"ok": not bad, "done": ok_list, "failed": bad}


def op_validate_controls(payload):
    """逐个校验 Mesh 里已有的尺寸/方法控制项
       返回每个控制项：类别、体数、尺寸、方法、状态、问题列表（fatal/warn）
    """
    out = {"ok": True, "controls": [], "fatal": [], "warn": [],
           "settings": {}, "errors": []}
    mesh = _mesh()

    def mm(obj, name, default=0.0):
        try:
            return _mm_value(_safe(obj, name, 0))
        except Exception:
            return default

    g_min = mm(mesh, "MinimumSize")
    g_def = mm(mesh, "DefeatureTolerance")
    g_size = mm(mesh, "ElementSize")
    out["settings"] = {"ElementSize": g_size, "MinimumSize": g_min,
                       "DefeatureTolerance": g_def}

    ok_states = (u"FullyDefined", u"Solved", u"UpToDate", u"NotSolved", u"")

    for c in list(mesh.Children or []):
        nm = _txt(_safe(c, "Name", u""))
        cat = _txt(_safe(c, "DataModelObjectCategory", u"")) or u""
        low = cat.lower()
        is_sizing = (u"sizing" in low) or nm.startswith(u"SIZE_")
        is_method = (u"method" in low) or nm.startswith(u"METHOD_")
        if not (is_sizing or is_method):
            continue
        item = {"name": nm, "category": cat,
                "kind": ("sizing" if is_sizing else "method"),
                "bodies": 0, "size_mm": None, "method": None,
                "state": u"", "problems": [], "fatal": [], "warn": []}
        try:
            ids = [int(x) for x in (_safe(_safe(c, "Location"), "Ids") or [])]
            item["bodies"] = len(ids)
        except Exception:
            ids = []
            item["bodies"] = 0
        if not ids:
            item["fatal"].append(u"作用域为空（控制项没有指定任何体）")

        if is_sizing:
            sz = mm(c, "ElementSize")
            item["size_mm"] = round(sz, 2) if sz > 0 else None
            if sz <= 0:
                item["fatal"].append(u"未设置尺寸（ElementSize 为空）")
            else:
                if g_min > 0 and sz < g_min:
                    item["warn"].append(
                        u"尺寸 %.2f 小于全局最小尺寸 %.2f（会被顶住）" % (sz, g_min))
                if g_def > 0 and sz < g_def:
                    item["warn"].append(
                        u"尺寸 %.2f 小于去特征尺寸 %.2f（细碎特征会被吃掉）" % (sz, g_def))
                if g_size > 0 and sz > g_size * 2.5:
                    item["warn"].append(
                        u"尺寸 %.2f 明显大于全局 %.2f（网格会很粗）" % (sz, g_size))
            try:
                beh = _txt(_safe(c, "Behavior", u""))
                if not beh:
                    item["warn"].append(u"Behavior 为空")
            except Exception:
                pass

        if is_method:
            mth = u""
            try:
                mth = _txt(_safe(c, "Method", u""))
            except Exception:
                mth = u""
            item["method"] = mth or None
            if not mth:
                item["fatal"].append(u"方法类型未设置")
            else:
                ml = mth.lower()
                if "sweep" in ml:
                    # 扫掠需要源/目标面或可扫掠拓扑
                    try:
                        has_src = bool(_safe(_safe(c, "SweepNumberDivisions"), None) is not None)
                    except Exception:
                        has_src = True
                    try:
                        nm_div = int(_mm_value(_safe(c, "SweepNumberDivisions", 0)) or 0)
                    except Exception:
                        nm_div = 0
                    if nm_div < 1:
                        item["warn"].append(u"扫掠层数未设置（穿过厚度方向的分段数=空）；薄板建议 2 层，点「应用设置与尺寸」会自动写入")
                if "hex" in ml or "multizone" in ml:
                    pass
            try:
                _eo = _txt(_safe(c, "ElementOrder", u""))
            except Exception:
                _eo = u""

        st = u""
        for key in ("ObjectState", "State"):
            try:
                st = _txt(_safe(c, key, u""))
                if st:
                    break
            except Exception:
                continue
        item["state"] = st
        if st and st not in ok_states:
            item["fatal"].append(u"控制项状态异常：%s" % st)

        item["problems"] = item["fatal"] + item["warn"]
        if item["fatal"]:
            out["fatal"].append({"name": nm, "problems": item["fatal"]})
        elif item["warn"]:
            out["warn"].append({"name": nm, "problems": item["warn"]})
        out["controls"].append(item)

    # 网格对象本身的状态
    try:
        out["mesh_state"] = [_txt(_safe(mesh, "ObjectState", u"")),
                             _txt(_safe(mesh, "State", u""))]
    except Exception:
        out["mesh_state"] = []
    return out


def op_pre_mesh_check(payload):
    """网格化前检查；payload={"fix": true} 表示自动修正可修项"""
    do_fix = bool((payload or {}).get("fix", True))
    mesh = _mesh()
    res = {"ok": True, "errors": [], "warnings": [], "fixes": [],
           "settings": {}, "states": {}}

    def mm(obj, name):
        try:
            return _mm_value(_safe(obj, name, 0))
        except Exception:
            return 0.0

    esize = mm(mesh, "ElementSize")
    mini = mm(mesh, "MinimumSize")
    defe = mm(mesh, "DefeatureTolerance")
    res["settings"] = {"ElementSize": esize, "MinimumSize": mini,
                       "DefeatureTolerance": defe}

    # --- 1) 黄色项：MinimumSize 必须 >= DefeatureTolerance ---
    if mini > 0 and defe > 0 and mini < defe:
        if do_fix:
            try:
                from Ansys.Core.Units import Quantity
                new_mini = round(defe + 0.5, 2)
                mesh.MinimumSize = Quantity(u"%.3f [mm]" % new_mini)
                res["fixes"].append(
                    u"最小尺寸 %.2f < 去特征 %.2f -> 已改为 %.2f" % (mini, defe, new_mini))
                mini = new_mini
                res["settings"]["MinimumSize"] = mini
            except Exception:
                res["errors"].append(u"最小尺寸 < 去特征，自动修正失败")
        else:
            res["errors"].append(
                u"最小尺寸 %.2f < 去特征尺寸 %.2f（黄色项，网格会生成失败）" % (mini, defe))

    # --- 2) 网格状态 ---
    st = _txt(_safe(mesh, "ObjectState", u""))
    sd = _txt(_safe(mesh, "State", u""))
    res["states"]["mesh"] = [st, sd]
    if st == u"UnderDefined" or sd == u"Unfulfilled":
        res["errors"].append(
            u"网格对象状态 %s/%s（必填项不满足，通常是上面的黄色项）" % (st, sd))

    # --- 3) 控制项检查 ---
    bad_scope, not_defined = [], []
    sizes_below_min = []
    for c in list(mesh.Children or []):
        nm = _txt(_safe(c, "Name", u""))
        ost = _txt(_safe(c, "ObjectState", u""))
        if ost not in (u"FullyDefined", u"Solved", u"UpToDate", u"NotSolved", u""):
            not_defined.append(u"%s(%s)" % (nm, ost))
        try:
            n = len(list(_safe(_safe(c, "Location"), "Ids") or []))
        except Exception:
            n = 0
        if n == 0:
            bad_scope.append(nm)
        if nm.startswith(u"SIZE_") or (not nm.startswith(u"METHOD_")):
            sz = mm(c, "ElementSize")
            if sz > 0 and mini > 0 and sz < mini:
                sizes_below_min.append(u"%s(%.1f<%.1f)" % (nm, sz, mini))

    if not_defined:
        res["warnings"].append(u"状态异常的控制项: " + u", ".join(not_defined[:6]))
    if bad_scope:
        res["errors"].append(u"作用域为空的控制项: " + u", ".join(bad_scope[:6]))
    if sizes_below_min:
        res["warnings"].append(
            u"局部尺寸小于最小尺寸（会被顶住）: " + u", ".join(sizes_below_min[:6]))

    # --- 4) 覆盖检查 ---
    try:
        plan = op_read_plan({})
        if plan.get("uncovered_count"):
            res["warnings"].append(u"有 %d 个体未被任何控制项覆盖"
                                   % plan["uncovered_count"])
    except Exception:
        pass

    res["ok"] = not res["errors"]
    return res


def op_mesh(payload):
    """清空并生成网格（含黄色项前置校验与超时保护）"""
    mesh = _mesh()
    started = time.time()
    fixes = []

    # ---- 前置检查：任何 error 直接拒绝网格化，避免长时间空转 ----
    try:
        chk = op_pre_mesh_check({"fix": True})
        fixes.extend(chk.get("fixes") or [])
        if not chk.get("ok"):
            return {"ok": False, "elements": 0, "nodes": 0,
                    "fixes": fixes,
                    "precheck": chk,
                    "error": u"网格前检查未通过：" + u"；".join(chk.get("errors") or [])}
    except Exception:
        pass

    # ---- 黄色项校验：MinimumSize >= DefeatureTolerance ----
    try:
        mini = _mm_value(_safe(mesh, "MinimumSize", 0))
        defe = _mm_value(_safe(mesh, "DefeatureTolerance", 0))
        if mini > 0 and defe > 0 and mini < defe:
            from Ansys.Core.Units import Quantity
            new_mini = round(defe + 0.5, 2)
            mesh.MinimumSize = Quantity(u"%.3f [mm]" % new_mini)
            fixes.append(u"最小尺寸 %.2f < 去特征 %.2f，已自动提到 %.2f（去特征+0.5）" % (mini, defe, new_mini))
    except Exception:
        pass

    # ---- 若网格仍处于 UnderDefined/Unfulfilled，说明还有必填项缺失 ----
    st0 = _txt(_safe(mesh, "ObjectState", u""))
    sd0 = _txt(_safe(mesh, "State", u""))
    if st0 == u"UnderDefined" or sd0 == u"Unfulfilled":
        return {"ok": False, "elements": 0, "nodes": 0,
                "mesh_state": st0, "mesh_detail": sd0, "fixes": fixes,
                "error": u"网格处于 %s/%s（有必填项不满足，通常是 最小尺寸 < 去特征尺寸），"
                         u"已停止以免长时间空转" % (st0, sd0)}
    if (payload or {}).get("clear"):
        try:
            mesh.ClearGeneratedData()
        except Exception:
            pass
    try:
        _stamp_write(mesh_start_ts=time.time())
    except Exception:
        pass
    timeout = int((payload or {}).get("timeout", 1800))
    try:
        mesh.GenerateMesh()
    except Exception:
        return {"ok": False, "error": _tail(traceback.format_exc(), 1500)}

    elements = 0
    nodes = 0
    idle_limit = min(timeout, 240)      # 无单元时最多等 4 分钟
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            elements, nodes = _counts()
        except Exception:
            elements, nodes = 0, 0
        if elements > 0:
            break
        if _cancelled():
            return {"ok": False, "cancelled": True, "elements": elements,
                    "nodes": nodes, "error": u"已被用户停止"}
        time.sleep(2)

    # 体状态复查
    counts = {}
    failed = []
    try:
        for b in _bodies():
            try:
                if b.Suppressed:
                    continue
            except Exception:
                pass
            st = u"?"
            try:
                st = _txt(b.ObjectState)
            except Exception:
                pass
            counts[st] = counts.get(st, 0) + 1
            if st != u"Meshed":
                failed.append({"name": _txt(b.Name), "state": st})
    except Exception:
        pass

    if elements > 0:
        try:
            _touch_mesh()
        except Exception:
            pass

    return {"ok": elements > 0, "elements": elements, "nodes": nodes,
            "seconds": round(time.time() - started, 1),
            "body_states": counts, "failed_bodies": failed,
            "mesh_state": _txt(getattr(mesh, "ObjectState", u"-"))}


def op_fix_failed(payload):
    """自动修复失败体：减半尺寸并重划，最多 rounds 轮

    重要：只有**网格已生成且状态为 Solved** 时，ObjectState 才代表"真的失败"。
    网格过期（NotSolved/OutOfDate）时所有体都不是 Meshed，绝不能当成失败体批量建控制项。
    """
    from Ansys.Core.Units import Quantity
    rounds = int((payload or {}).get("rounds", 2))
    floor_mm = float((payload or {}).get("floor_mm", 2.0))
    max_fix = int((payload or {}).get("max_fix", 40))
    manager = _extapi().SelectionManager

    log = []
    unfixable = []          # 已到尺寸下限仍失败的体（尺寸无法修复）
    # ---- 前置检查：网格必须是已求解状态，否则先重划
    mesh = _mesh()
    state = _txt(_safe(mesh, "ObjectState", u""))
    detail = _txt(_safe(mesh, "State", u""))
    if state != u"Solved" or detail != u"UpToDate":
        log.append(u"网格状态为 %s/%s（未求解或已过期），先重新生成再判断失败体" % (state, detail))
        try:
            compat_clear = _safe(mesh, "ClearGeneratedData")
            if callable(compat_clear):
                compat_clear()
        except Exception:
            pass
        try:
            _safe(mesh, "GenerateMesh")()
        except Exception:
            log.append(u"  生成网格异常: %s" % _tail(traceback.format_exc(), 300))
        state = _txt(_safe(mesh, "ObjectState", u""))
        detail = _txt(_safe(mesh, "State", u""))
        log.append(u"  重生成后状态：%s/%s（失败体数量会在下面按上限判断）" % (state, detail))

    for round_no in range(1, rounds + 1):
        if _cancelled():
            log.append(u"已被用户停止（第 %d 轮之前）" % round_no)
            return {"ok": False, "cancelled": True, "rounds_log": log,
                    "still_failed": []}
        # 找失败体
        failed = []
        try:
            for b in _bodies():
                try:
                    if b.Suppressed:
                        continue
                except Exception:
                    pass
                if _txt(getattr(b, "ObjectState", u"")) == u"Meshed":
                    continue
                failed.append(b)
        except Exception:
            pass
        if not failed:
            log.append(u"第 %d 轮：没有失败体" % round_no)
            break
        # 安全上限：失败体异常多说明是误判（如网格过期），不要批量建控制项
        if len(failed) > max_fix:
            log.append(u"第 %d 轮：失败体 %d 个，超过上限 %d，判定为异常，已中止（未创建任何控制项）"
                       % (round_no, len(failed), max_fix))
            log.append(u"  若网格刚改过尺寸尚未生成，请先点「生成网格」再修复")
            return {"ok": False, "rounds_log": log, "still_failed": [],
                    "error": u"失败体数量 %d 超过安全上限 %d，已中止" % (len(failed), max_fix)}
        log.append(u"第 %d 轮：发现 %d 个失败体" % (round_no, len(failed)))

        # 为每个失败体设置局部尺寸（在已有 SIZE_FIX 上减半）
        for _idx, b in enumerate(failed, 1):
            if _cancelled():
                log.append(u"  已被用户停止（处理到第 %d/%d 个失败体）" % (_idx, len(failed)))
                return {"ok": False, "cancelled": True, "rounds_log": log,
                        "still_failed": []}
            _progress(u"fix_failed", u"处理失败体", _idx, len(failed))
            try:
                bid = int(b.GetGeoBody().Id)
            except Exception:
                continue
            target = None
            for c in (_model().Mesh.Children or []):
                try:
                    if not _txt(c.Name).startswith(u"SIZE_FIX_"):
                        continue
                    ids = [int(x) for x in c.Location.Ids]
                except Exception:
                    continue
                if bid in ids:
                    target = c
                    break
            try:
                if target is not None:
                    cur = _mm_value(_safe(target, "ElementSize", 0))
                    if cur <= floor_mm + 0.01:
                        # 已在下限仍失败 -> 尺寸无法修复，不再反复减半
                        log.append(u"  %s: 已是下限 %g mm，尺寸无法修复（需换方法/修几何）"
                                   % (_txt(b.Name)[:40], floor_mm))
                        unfixable.append(_txt(b.Name))
                        continue
                    newval = max(floor_mm, cur * 0.5)
                    from Ansys.Core.Units import Quantity
                    target.ElementSize = Quantity(u"%.3f [mm]" % newval)
                    log.append(u"  %s: %.1f -> %.1f mm" % (_txt(b.Name)[:40], cur, newval))
                    try:
                        _rm2, _er2 = _exclude_body_from_categories(
                            bid, _txt(b.Name))
                        if _rm2:
                            log.append(u"  %s: 已从 %s 摘除"
                                       % (_txt(b.Name)[:30], u"、".join(_rm2[:3])))
                    except Exception:
                        pass
                else:
                    # 新建 SIZE_FIX_ 控制项
                    new_name = u"SIZE_FIX_" + _txt(b.Name).replace(u"\\", u"_")[:24]
                    ctrl, err = _new_sizing_named(new_name, [bid], 10.0)
                    if ctrl is None:
                        log.append(u"  %s: 新建失败 %s" % (_txt(b.Name)[:30], err))
                    else:
                        log.append(u"  %s: 新建 10 mm" % (_txt(b.Name)[:40]))
                        # 关键：把它从类别级尺寸/方法控制项里摘掉，
                        # 否则类别控制项仍覆盖该体，局部尺寸不生效（一直失败）
                        try:
                            _rm, _er = _exclude_body_from_categories(
                                bid, _txt(b.Name))
                            if _rm:
                                log.append(u"  %s: 已从 %s 摘除"
                                           % (_txt(b.Name)[:30], u"、".join(_rm[:3])))
                            if _er:
                                log.append(u"  %s: 摘除告警 %s"
                                           % (_txt(b.Name)[:30], u"、".join(_er[:2])))
                        except Exception as _e:
                            log.append(u"  %s: 摘除失败 %s"
                                       % (_txt(b.Name)[:30], _txt(_e)[:60]))
            except Exception:
                log.append(u"  %s: 处理失败 %s" % (_txt(b.Name)[:30],
                                                  _tail(traceback.format_exc(), 200)))

        # 重划
        try:
            _mesh().ClearGeneratedData()
        except Exception:
            pass
        try:
            _mesh().GenerateMesh()
        except Exception:
            log.append(u"  GenerateMesh 异常: %s" % _tail(traceback.format_exc(), 300))

    # 最终统计
    elements = nodes = 0
    try:
        elements, nodes = _counts()
    except Exception:
        pass
    still = []
    try:
        for b in _bodies():
            try:
                if b.Suppressed:
                    continue
            except Exception:
                pass
            if _txt(getattr(b, "ObjectState", u"")) != u"Meshed":
                still.append(_txt(b.Name))
    except Exception:
        pass
    return {"ok": not still, "rounds_log": log, "elements": elements, "nodes": nodes,
            "still_failed": still, "unfixable_by_size": unfixable}


def op_quality(payload):
    """质量报告：调用 run_mesh.py 并回传报告正文"""
    path = os.path.join(ROOT, "run_mesh.py")
    if not os.path.isfile(path):
        return {"ok": False, "error": u"找不到 %s" % path}
    try:
        execfile(path, {"__name__": "__main__"})
    except Exception:
        return {"ok": False, "error": _tail(traceback.format_exc(), 1500)}

    # 找最新报告
    rep_dir = os.path.join(ROOT, "reports")
    latest = None
    latest_t = 0
    try:
        for fn in os.listdir(rep_dir):
            if not fn.startswith(u"网格自动化报告_"):
                continue
            full = os.path.join(rep_dir, fn)
            mt = os.path.getmtime(full)
            if mt > latest_t:
                latest_t = mt
                latest = full
    except Exception:
        pass
    text = u""
    if latest:
        try:
            f = codecs.open(latest, "r", encoding="utf-8")
            try:
                text = f.read()
            finally:
                f.close()
        except Exception:
            pass
    return {"ok": True, "report_path": latest, "report": text}


def op_exec(payload):
    """执行指定脚本"""
    script = (payload or {}).get("script") or u""
    if not script:
        return {"ok": False, "error": u"缺少 script 参数"}
    path = script if os.path.isabs(script) else os.path.join(ROOT, script)
    if not os.path.isfile(path):
        return {"ok": False, "error": u"找不到 %s" % path}
    try:
        execfile(path, {"__name__": "__main__"})
        return {"ok": True, "script": path}
    except Exception:
        return {"ok": False, "error": _tail(traceback.format_exc(), 1500)}


# ============================================================================
#  类别扫描（按模型里的部件名自动归类）+ 方法选择
# ============================================================================
def _len_factor():
    """项目长度单位换算到 mm 的系数"""
    s = _txt(_safe(_mesh(), "ElementSize", u""))
    low = s.replace(u" ", u"").lower()
    if u"[mm]" in low:
        return 1.0
    if u"[m]" in low:
        return 1000.0
    return 1.0


def _mm_value(value):
    """把 '119.99 [mm]' / '0.12 [m]' 这类 Quantity 统一解析成 mm 数值"""
    s = _txt(value)
    try:
        num = float(s.split(u"[")[0].strip())
    except Exception:
        try:
            num = float(s)
        except Exception:
            return 0.0
    low = s.replace(u" ", u"").lower()
    if u"[mm]" in low:
        return num
    if u"[m]" in low:
        return num * 1000.0
    if u"[um]" in low:
        return num / 1000.0
    return num


def _vol_value(value):
    """体积统一解析成 mm^3"""
    s = _txt(value)
    try:
        num = float(s.split(u"[")[0].strip())
    except Exception:
        try:
            num = float(s)
        except Exception:
            return 0.0
    low = s.replace(u" ", u"").lower()
    if u"[mmmmmm]" in low:
        return num
    if u"[mmm]" in low:
        return num * 1.0e9
    if u"[mmmm]" in low:
        return num * 1000.0
    return num


def _category_of(name, fallback):
    """我们的命名规则：<层号>_<层名>_<类别>_<尺寸>_<序号>[_S]"""
    parts = [p for p in _txt(name).split(u"_") if p != u""]
    if len(parts) >= 3 and parts[0] in (u"1", u"2", u"3"):
        return parts[2]
    if len(parts) >= 2:
        return parts[-1] if len(parts) == 2 else parts[1]
    return fallback or u"未分类"


def _method_ctrls():
    """{类别: 控制项对象}，来自 METHOD_<类别>"""
    out = {}
    try:
        for c in (_mesh().Children or []):
            nm = _txt(_safe(c, "Name", u""))
            if nm.startswith(u"METHOD_"):
                out[nm[7:]] = c
    except Exception:
        pass
    return out


def _size_ctrls():
    out = {}
    try:
        for c in (_mesh().Children or []):
            nm = _txt(_safe(c, "Name", u""))
            if nm.startswith(u"SIZE_") and not nm.startswith(u"SIZE_FIX_"):
                out[nm[5:]] = c
    except Exception:
        pass
    return out


def _median(values):
    v = sorted([x for x in (values or []) if x is not None])
    n = len(v)
    if n == 0:
        return None
    if n % 2 == 1:
        return v[n // 2]
    return 0.5 * (v[n // 2 - 1] + v[n // 2])


_CTRL_PREFIXES = (u"SIZE_FIX_", u"SIZE_", u"METHOD_", u"SWEEP_", u"SHELL_")


def _ctrl_category(nm):
    """控制项名 -> 类别名（去掉 SIZE_/METHOD_ 等前缀）"""
    key = _txt(nm)
    for pre in _CTRL_PREFIXES:
        if key.startswith(pre):
            key = key[len(pre):]
            break
    if key.endswith(u"_实体"):
        key = key[:-3]
    return key


def _plan_groups(recs):
    """按 Mechanical 里"实际存在的控制项"分组。
    返回 (grouped{类别: [rec]}, owners{类别: [控制项]})。
    这样一个类别可以对应你自建的 Body Sizing，也可以对应程序的 SIZE_xxx。
    """
    by_id = {}
    for r in recs:
        if r.get("id") is not None:
            by_id[r["id"]] = r
    by_name = {r["name"]: r for r in recs}

    grouped, owners, seen = {}, {}, {}
    try:
        children = list(_mesh().Children or [])
    except Exception:
        children = []
    for c in children:
        nm = _txt(_safe(c, "Name", u""))
        if not nm or nm.startswith(u"SIZE_FIX_"):
            continue
        key = _ctrl_category(nm)
        if not key:
            continue
        owners.setdefault(key, []).append(c)
        ids = []
        try:
            ids = [int(x) for x in (_safe(_safe(c, "Location"), "Ids") or [])]
        except Exception:
            pass
        for gid in ids:
            r = by_id.get(gid)
            if r is None:
                continue
            if r["name"] in seen.get(key, set()):
                continue
            seen.setdefault(key, set()).add(r["name"])
            grouped.setdefault(key, []).append(r)

    # 没有任何控制项覆盖的体，单独成一行，避免"看不见"
    rest = [r for r in recs if r["name"] not in set(
        n for s in seen.values() for n in s)]
    if rest:
        grouped[u"未覆盖"] = rest
        owners[u"未覆盖"] = []
    return grouped, owners


def _ctrl_size(ctrl):
    try:
        v = _mm_value(_safe(ctrl, "ElementSize", 0))
        return round(v, 2) if v > 0 else None
    except Exception:
        return None


def op_scan_categories(payload):
    """扫描当前模型的所有体，按"形状 + 位置"归类，并给出薄板/壳体标记与已设方法"""
    factor = _len_factor()
    recs = _body_records()
    grouped, owners = _plan_groups(recs)
    methods = {}
    sizes = {}
    for cat, ctrls in owners.items():
        for c in ctrls:
            nm = _txt(_safe(c, "Name", u""))
            if nm.startswith(u"METHOD_"):
                methods.setdefault(cat, c)
            elif _ctrl_size(c) is not None:
                sizes.setdefault(cat, c)
    total = len(recs)
    debug_samples = []

    out = []
    for cat, items in grouped.items():
        info = {"name": cat, "base": _base_name_of(cat), "bodies": 0, "shells": 0,
                "thin": 0, "min_dim": None, "max_dim": None, "volume": 0.0,
                "samples": [], "dims_list": [], "fill_list": [],
                "bbox_vol": 0.0, "bbox_area": 0.0,
                "zone": items[0]["zone"] if items else u""}

        for r in items:
            info["bodies"] += 1
            if r["shell"]:
                info["shells"] += 1
            lx, ly, lz = r["lx"], r["ly"], r["lz"]
            dims = sorted([d for d in (lx, ly, lz) if d > 0])
            if len(dims) == 3:
                smallest, middle, largest = dims
                thin_ratio = smallest / largest if largest > 0 else 1.0
                # 薄板判据：最薄方向 < 最大方向的 3%，中间方向不小；壳体不重复标
                if (not r["shell"]) and thin_ratio < 0.03 and middle / largest > 0.2:
                    info["thin"] += 1
                if info["min_dim"] is None or smallest < info["min_dim"]:
                    info["min_dim"] = smallest
                if info["max_dim"] is None or largest > info["max_dim"]:
                    info["max_dim"] = largest
                info["dims_list"].append(dims)
                info["bbox_vol"] += smallest * middle * largest
                info["bbox_area"] += 2.0 * (smallest * middle + middle * largest +
                                            smallest * largest)
                if largest > 0 and (smallest * middle * largest) > 0:
                    info["fill_list"].append(r["vol"] / (smallest * middle * largest))
            info["volume"] += r["vol"]
            if len(info["samples"]) < 3:
                info["samples"].append(r["name"])
            if len(debug_samples) < 3:
                debug_samples.append({"name": r["name"], "zone": r["zone"],
                                      "base": r["base"]})

        dims_list = info.pop("dims_list", []) or []
        fill_list = info.pop("fill_list", []) or []
        small_med = _median([d[0] for d in dims_list])
        mid_med = _median([d[1] for d in dims_list])
        large_med = _median([d[2] for d in dims_list])
        fill_med = _median(fill_list)
        info["d_small"] = round(small_med, 2) if small_med else None
        info["d_mid"] = round(mid_med, 2) if mid_med else None
        info["d_large"] = round(large_med, 2) if large_med else None
        info["fill_med"] = round(fill_med, 4) if fill_med is not None else None
        # 壁厚估算：实心件取薄向尺寸；空心薄壁按截面填充率反推
        wall = None
        try:
            if fill_med is not None and mid_med:
                if fill_med > 0.6 or small_med is None:
                    wall = small_med
                else:
                    ratio = 1.0 - fill_med
                    if ratio > 0:
                        wall = 0.5 * mid_med * (1.0 - (ratio ** 0.5))
        except Exception:
            wall = small_med
        info["wall_mm"] = round(wall, 2) if wall else None
        info["bbox_area"] = round(info.get("bbox_area") or 0.0, 1)
        info["bbox_vol"] = round(info.get("bbox_vol") or 0.0, 1)

        size_ctrl = sizes.get(cat)
        method_ctrl = methods.get(cat)
        info["size_mm"] = None
        if size_ctrl is not None:
            v = _mm_value(_safe(size_ctrl, "ElementSize", 0))
            info["size_mm"] = round(v, 2) if v > 0 else None
        # 模型里没有该类的尺寸控制时，给一个建议值（前端/后端可被配置覆盖）
        if info["shells"] > 0:
            info["size_suggested"] = 30.0
        elif info["thin"] > 0:
            info["size_suggested"] = 20.0
        else:
            info["size_suggested"] = 30.0
        info["method"] = u"Automatic"
        if method_ctrl is not None:
            info["method"] = _txt(_safe(method_ctrl, "Method", u"Automatic"))
        # 只要该类别下有非方法类控制项（不管有没有设尺寸）就算"有尺寸控制项"
        _own = owners.get(cat) or []
        _size_owner = None
        for _c in _own:
            if not _txt(_safe(_c, "Name", u"")).startswith(u"METHOD_"):
                _size_owner = _c
                break
        info["has_size_ctrl"] = _size_owner is not None
        info["size_is_set"] = size_ctrl is not None
        info["control_name"] = (_txt(_safe(_size_owner, "Name", u""))
                                if _size_owner is not None else None)
        info["has_method_ctrl"] = method_ctrl is not None
        info["is_shell_cat"] = info["shells"] > 0
        info["is_thin_cat"] = info["thin"] > 0
        out.append(info)

    out.sort(key=lambda d: -d["bodies"])
    try:
        cur_elements, cur_nodes = _counts()
    except Exception:
        cur_elements, cur_nodes = 0, 0
    return {"ok": True, "total_bodies": total, "categories": out,
            "elements": cur_elements, "nodes": cur_nodes,
            "length_unit_factor": factor, "debug_samples": debug_samples,
            "debug_size_ctrls": sorted(sizes.keys()),
            "debug_method_ctrls": sorted(methods.keys()),
            "debug_children": len(list(_mesh().Children or []))}


def _set_method(ctrl, member_name):
    """设置方法控制项的 Method（2022 R1 实测：MethodType 枚举 + Property.InternalValue）"""
    # 1) 取 MethodType 枚举成员（本版本唯一的正确入口）
    value = None
    errs = []
    try:
        from Ansys.Mechanical.DataModel.Enums import MethodType
        value = getattr(MethodType, member_name, None)
    except Exception:
        errs.append(u"MethodType 导入失败")
    if value is None:
        errs.append(u"MethodType.%s 不存在" % member_name)

    # 2) 写 Property.InternalValue
    try:
        getter = _safe(ctrl, "PropertyByAPIName")
        if not callable(getter):
            return False, u"没有 PropertyByAPIName"
        prop = getter("Method")
        if prop is None:
            return False, u"Method 属性为空"
        if value is not None:
            prop.InternalValue = value
            return True, u""
        # 3) 兜底：若当前值本身是枚举，用 Enum.Parse
        current = _safe(prop, "InternalValue")
        import System
        t = current.GetType()
        if t is not None and t.IsEnum:
            prop.InternalValue = System.Enum.Parse(t, member_name, True)
            return True, u""
        return False, u"; ".join(errs) or u"无法解析枚举"
    except Exception:
        return False, _tail(traceback.format_exc(), 300)


def _sel_enum():
    """几何实体选择类型枚举（2022 R1 实测入口）"""
    try:
        from Ansys.ACT.Interfaces.Common import SelectionTypeEnum
        return SelectionTypeEnum.GeometryEntities
    except Exception:
        pass
    try:
        import Ansys.ACT.Interfaces.Common as C
        return C.SelectionTypeEnum.GeometryEntities
    except Exception:
        pass
    return 1


def _apply_methods(methods_cfg, divisions=None):
    """按 {类别: 方法名} 建立/更新/删除方法控制项（用实际控制项分组）"""
    result = {"created": [], "updated": [], "deleted": [], "errors": [],
              "cat_count": 0, "id_total": 0}
    if not methods_cfg:
        return result
    divisions = divisions or {}

    recs = _body_records()
    grouped, _owners = _plan_groups(recs)
    result["cat_count"] = len(grouped)
    result["id_total"] = sum(len(v) for v in grouped.values())
    existing = _method_ctrls()

    for cat, method in methods_cfg.items():
        m = _txt(method).strip()
        ids = [r["id"] for r in grouped.get(cat, []) if r.get("id") is not None]
        if not ids:
            result["errors"].append(u"%s: 找不到该类的体" % cat)
            continue
        was = existing.get(cat) is not None
        ok, err = _set_method_for(cat, ids, m, divisions.get(cat))
        if not ok:
            result["errors"].append(u"%s: %s" % (cat, err))
            continue
        if m in (u"", u"Automatic", u"自动"):
            if was:
                result["deleted"].append(cat)
        elif was:
            result["updated"].append(cat)
        else:
            result["created"].append(cat)
    return result


def _group_of(api):
    """参数归到哪个折叠分组"""
    name = _txt(api)
    for group, keys in _PARAM_GROUPS:
        for k in keys:
            if k.lower() in name.lower():
                return group
    return u"其它"


def _find_ctrl(payload):
    """按类别或控制项名找控制项对象"""
    cat = (payload or {}).get("category")
    cname = (payload or {}).get("control_name")
    try:
        children = list(_mesh().Children or [])
    except Exception:
        return None, u"读取 Mesh.Children 失败"
    if cname:
        for c in children:
            if _txt(_safe(c, "Name", u"")) == _txt(cname):
                return c, u""
        return None, u"找不到控制项 %s" % cname
    if cat:
        for prefix in (u"METHOD_", u"SIZE_"):
            for c in children:
                if _txt(_safe(c, "Name", u"")) == prefix + _txt(cat):
                    return c, u""
        return None, u"类别 %s 还没有尺寸/方法控制项" % cat
    return None, u"缺少 category 或 control_name"


def _param_kind(value):
    """判断参数类型，返回 (kind, 当前值文本, options)；非标量返回 (None, None, [])"""
    if value is None or value is _UNSET:
        return None, None, []
    # 枚举
    try:
        t = value.GetType()
        if t is not None and t.IsEnum:
            import System
            names = list(System.Enum.GetNames(t))
            return "enum", _txt(value), names
        tname = _txt(t.FullName) if t is not None else u""
    except Exception:
        tname = u""
    if isinstance(value, bool):
        return "bool", u"True" if value else u"False", []
    # 带单位的量（Quantity）
    if u"Quantity" in tname:
        return "quantity", _txt(value), []
    if isinstance(value, int) and not isinstance(value, bool):
        return "number", _txt(value), []
    if isinstance(value, float):
        return "number", _txt(value), []
    if isinstance(value, _STR_TYPES):
        s = _txt(value)
        if s and not s.startswith(u"<") and not s.startswith(u"List["):
            return "string", s, []
        return None, None, []
    return None, None, []


def _collect_from_props(ctrl):
    """优先用 VisibleProperties（等价于 Details 面板里显示的参数）"""
    props = _safe(ctrl, "VisibleProperties", None)
    if props is None:
        return None
    out = []
    try:
        plist = list(props)
    except Exception:
        return None
    for p in plist:
        api = _txt(_safe(p, "Name", u""))
        if not api:
            continue
        val = _safe(p, "InternalValue", _UNSET)
        kind, text, options = _param_kind(val)
        if kind is None:
            continue
        out.append({
            "api": api,
            "value": text,
            "kind": kind,
            "options": options,
            "readonly": bool(_safe(p, "ReadOnly", False)),
            "unit": _txt(_safe(p, "Unit", u"") or u""),
        })
    return out


def _collect_from_dir(ctrl):
    """兜底：反射枚举（VisibleProperties 不可用时）"""
    try:
        names = [n for n in dir(ctrl) if not n.startswith(u"_")]
    except Exception:
        return []
    out = []
    for attr in names:
        if attr in _PARAM_SKIP:
            continue
        value = _safe(ctrl, attr, _UNSET)
        if value is _UNSET or callable(value):
            continue
        kind, text, options = _param_kind(value)
        if kind is None:
            continue
        readonly = False
        unit = u""
        try:
            getter = _safe(ctrl, "PropertyByAPIName")
            if callable(getter):
                prop = getter(attr)
                if prop is not None:
                    readonly = bool(_safe(prop, "ReadOnly", False))
                    unit = _txt(_safe(prop, "Unit", u"") or u"")
        except Exception:
            pass
        out.append({"api": attr, "value": text, "kind": kind, "options": options,
                    "readonly": readonly, "unit": unit})
    return out


_ZONE_NAMES = {u"1": u"下", u"2": u"中", u"3": u"上"}
_ZONE_Y = (3748.0, 6664.0)          # 下/中 与 中/上 的分界（mm）


def _zone_of_body(name, cy):
    parts = _txt(name).split(u"_")
    if parts and parts[0] in _ZONE_NAMES:
        return _ZONE_NAMES[parts[0]]
    if cy < _ZONE_Y[0]:
        return u"下"
    if cy <= _ZONE_Y[1]:
        return u"中"
    return u"上"


def _base_of(name, fallback):
    parts = [p for p in _txt(name).split(u"_") if p]
    if len(parts) >= 3 and parts[0] in _ZONE_NAMES:
        return parts[2]
    return fallback or u"未分类"


def _is_corner_fitting(dims):
    """角件判据：三个方向都 >=80mm，且最长/最短 <=3（立方块状，排除梁和薄板）"""
    ds = sorted([d for d in dims if d > 0])
    if len(ds) != 3:
        return False
    if ds[0] < 80:
        return False
    if ds[2] > 500:
        return False
    return (ds[2] / ds[0]) <= 3.0


def _classify_fix(base, dims, is_shell):
    """分类修正规则：
       1) 波纹墙 -> 波纹板
       2) 板 里的壳体（波纹板）-> 隔板
       3) 薄垫板 里竖直的中间隔断板 -> 中间隔断板
       4) 块体 / 结构件 里立方块状的 -> 角件（上下共 8 个）
    """
    if base == u"波纹墙":
        base = u"波纹板"
    if base == u"板" and is_shell:
        base = u"隔板"
    if base == u"薄垫板" and len(dims) == 3:
        lx, ly, lz = dims
        # 竖直：Y 最长，且 X 是最薄方向
        if ly >= max(lx, lz) - 1e-6 and lx <= min(ly, lz):
            base = u"中间隔断板"
    if base in (u"块体", u"结构件") and _is_corner_fitting(dims):
        base = u"角件"
    return base


def _body_records():
    """收集所有体的分类所需信息"""
    recs = []
    for b in _bodies():
        _sup0 = bool(_safe(b, "Suppressed", False))
        name = _txt(_safe(b, "Name", u"?"))
        lx = _mm_value(_safe(b, "LengthX", 0))
        ly = _mm_value(_safe(b, "LengthY", 0))
        lz = _mm_value(_safe(b, "LengthZ", 0))
        cy = _mm_value(_safe(b, "CentroidY", 0))
        is_shell = u"urface" in _txt(_safe(b, "GeometryType", u""))
        try:
            gid = int(b.GetGeoBody().Id)
        except Exception:
            gid = None
        base = _base_of(name, u"未分类")
        base = _classify_fix(base, (lx, ly, lz), is_shell)
        recs.append({"suppressed": _sup0, "obj": b, "name": name, "id": gid, "lx": lx, "ly": ly, "lz": lz,
                     "cy": cy, "shell": is_shell, "base": base,
                     "zone": _zone_of_body(name, cy),
                     "vol": _vol_value(_safe(b, "Volume", 0))})
    return recs


_NO_ZONE_SPLIT = set([u"角件"])     # 角件上下共 8 个，合成一类


def _final_categories(recs):
    """按 base 分组；若跨多个位置且尺寸不一致，则按位置拆成"下_横梁"这样的独立类别"""
    by_base = {}
    for r in recs:
        by_base.setdefault(r["base"], []).append(r)

    result = {}          # final_name -> list[rec]
    for base, items in by_base.items():
        zones = {}
        for r in items:
            zones.setdefault(r["zone"], []).append(r)
        if len(zones) <= 1 or base in _NO_ZONE_SPLIT:
            result[base] = items
            continue
        # 各位置的"尺寸签名集合"是否完全一致
        sig_sets = []
        for z, rs in zones.items():
            sig_sets.append(frozenset(
                (round(r["lx"]), round(r["ly"]), round(r["lz"])) for r in rs))
        if len(set(sig_sets)) == 1:
            result[base] = items           # 尺寸一模一样 -> 合并
        else:
            for z, rs in zones.items():
                result[u"%s_%s" % (z, base)] = rs
    return result


def _base_name_of(cat):
    """'下_横梁' -> '横梁'；'波纹板' -> '波纹板'"""
    c = _txt(cat)
    if len(c) > 2 and c[0] in (u"下", u"中", u"上") and c[1] == u"_":
        return c[2:]
    return c


def _category_ids():
    """{最终类别: [geo id, ...]}，同时返回 {类别: 是否含壳体}"""
    recs = _body_records()
    grouped = _final_categories(recs)
    cat_ids = {}
    has_shell = {}
    for cat, items in grouped.items():
        ids = [r["id"] for r in items if r["id"] is not None]
        if ids:
            cat_ids[cat] = ids
        if any(r["shell"] for r in items):
            has_shell[cat] = True
    return cat_ids, has_shell


def _new_sizing_named(ctrl_name, ids, size_mm):
    """按指定名字新建一个尺寸控制项"""
    manager = _extapi().SelectionManager
    info = None
    try:
        info = manager.CreateSelectionInfo(_sel_enum())
    except Exception:
        info = None
    if info is None:
        return None, u"CreateSelectionInfo 失败"
    try:
        info.Ids = list(ids)
    except Exception:
        return None, u"Ids 赋值失败"
    adder = _safe(_mesh(), "AddSizing")
    if not callable(adder):
        return None, u"没有 AddSizing()"
    try:
        ctrl = adder()
        ctrl.Name = ctrl_name
        ctrl.Location = info                 # 先设作用域
        from Ansys.Core.Units import Quantity
        ctrl.ElementSize = Quantity(u"%.3f [mm]" % float(size_mm))
        return ctrl, u""
    except Exception:
        return None, _tail(traceback.format_exc(), 300)


def _new_sizing(cat, ids, size_mm):
    """为类别新建 SIZE_<类别> 控制项"""
    return _new_sizing_named(u"SIZE_" + _txt(cat), ids, size_mm)


def op_ensure_controls(payload):
    """为缺少尺寸控制的类别补建 SIZE_ 控制项"""
    sizes_cfg = (payload or {}).get("sizes") or {}
    default_size = float((payload or {}).get("default_size") or 30.0)
    cat_ids, _ = _category_ids()
    existing = _size_ctrls()
    created = []
    errors = []
    for cat, ids in cat_ids.items():
        if cat in existing or not ids:
            continue
        if _cancelled():
            errors.append(u"已被用户停止")
            break
        val = sizes_cfg.get(cat, default_size)
        ctrl, err = _new_sizing(cat, ids, val)
        if ctrl is None:
            errors.append(u"%s: %s" % (cat, err))
        else:
            created.append(u"%s=%g" % (cat, val))
    return {"ok": not errors, "created": created, "errors": errors,
            "total_categories": len(cat_ids)}


def op_probe_tree(payload):
    """列出 Model 子树结构（含分组文件夹），供读取 Geometry 分组用"""
    model = _model()
    out = {"ok": True, "items": []}

    def cat_of(obj):
        try:
            return _txt(obj.DataModelObjectCategory)
        except Exception:
            return u""

    def nm(obj):
        try:
            return _txt(obj.Name)
        except Exception:
            return u""

    try:
        children = list(model.Children)
    except Exception as e:
        return {"ok": False, "error": _txt(e)}

    # 第一层
    for ch in children:
        c = cat_of(ch)
        item = {"lvl": 1, "cat": c, "name": nm(ch)}
        sub = []
        if "roup" in c or "Folder" in c:
            try:
                for g in ch.Children:
                    sub.append({"cat": cat_of(g), "name": nm(g)})
            except Exception:
                pass
            item["children"] = sub[:200]
            item["child_count"] = len(sub)
        out["items"].append(item)

    # 若 Model.Geometry 下也有分组，补一层
    try:
        geo = model.Geometry
        gitems = []
        for g in geo.Children:
            gitems.append({"cat": cat_of(g), "name": nm(g)})
        out["geometry_children"] = gitems[:80]
    except Exception as e:
        out["geometry_error"] = _txt(e)
    return out


def op_probe_geometry_groups(payload):
    """探测：Model 上是否有 Groups（Geometry 分组）以及可用方法"""
    out = {"ok": True}
    model = _model()
    try:
        names = [n for n in dir(model) if not n.startswith("_")]
    except Exception as e:
        return {"ok": False, "error": _txt(e)}

    out["group_members"] = [n for n in names if "roup" in n]
    out["geom_members"] = [n for n in names if "eometry" in n]

    # 尝试拿到 Groups 容器
    for attr in ("Groups", "GeometryGroups", "NamedSelections"):
        try:
            obj = getattr(model, attr)
            out[attr] = {
                "type": _txt(type(obj)),
                "count": len(list(obj)) if hasattr(obj, "__iter__") else None,
                "members": [n for n in dir(obj) if not n.startswith("_")][:40],
            }
        except Exception as e:
            out[attr] = {"error": _txt(e)[:120]}
    return out


def op_geom_groups(payload):
    """读取 Geometry 下的分组文件夹（TreeGroupingFolder）与成员体名
       返回 {"groups":[{"name":..,"bodies":[..]}], "ungrouped":[..], "total": n}
    """
    model = _model()
    out = {"ok": True, "groups": [], "ungrouped": [], "total": 0}
    grouped = set()

    def cat_of(o):
        try:
            return _txt(o.DataModelObjectCategory)
        except Exception:
            return u""

    def nm(o):
        try:
            return _txt(o.Name)
        except Exception:
            return u""

    try:
        geo = model.Geometry
        children = list(geo.Children)
    except Exception as e:
        return {"ok": False, "error": _txt(e)}

    # 递归收集所有分组文件夹（文件夹可能嵌套）
    folders = []

    def _walk(nodes, depth):
        if depth > 4:
            return
        for x in nodes:
            c = cat_of(x)
            if "roupingFolder" in c:
                folders.append(x)
            try:
                kids = list(x.Children)
            except Exception:
                kids = []
            if kids and "Part" not in c and "Body" not in c:
                _walk(kids, depth + 1)

    try:
        _walk(children, 0)
    except Exception:
        folders = []
    if folders:
        children = folders

    for ch in children:
        c = cat_of(ch)
        if "TreeGroupingFolder" not in c and "GroupingFolder" not in c:
            continue
        members = []
        # 先用 GetChildren 直接取文件夹里的体（最可靠）
        try:
            from Ansys.Mechanical.DataModel.Enums import DataModelObjectCategory as _gc
            for _b in ch.GetChildren(_gc.Body, True):
                _bn = nm(_b)
                if _bn and _bn not in members:
                    members.append(_bn)
                try:
                    _EXTRA_BODIES[_bn] = _b
                except Exception:
                    pass
        except Exception:
            pass
        try:
            for sub in ch.Children:
                if members:
                    break
                sc = cat_of(sub)
                if "Body" in sc:
                    members.append(nm(sub))
                    try:
                        _EXTRA_BODIES[nm(sub)] = sub
                    except Exception:
                        pass
                elif "Part" in sc:
                    # 文件夹里可能是 Part：展开成其中的体名
                    got = []
                    try:
                        from Ansys.Mechanical.DataModel.Enums import DataModelObjectCategory as _cats
                        got = [nm(x) for x in sub.GetChildren(_cats.Body, True)]
                    except Exception:
                        got = []
                    if not got:
                        try:
                            got = [nm(x) for x in sub.Children]
                        except Exception:
                            got = []
                    for _x in sub.Children:
                        try:
                            if "Body" in cat_of(_x):
                                _EXTRA_BODIES[nm(_x)] = _x
                        except Exception:
                            pass
                    members.extend(got or [nm(sub)])
        except Exception:
            pass
        if not members:
            continue
        for m in members:
            grouped.add(m)
        out["groups"].append({"name": nm(ch), "bodies": members})

    allb = []
    for b in _bodies():
        allb.append(_txt(_safe(b, "Name", u"")))
    # 合并同名分组（嵌套会产生同名重复）
    _merged = {}
    for _g in out["groups"]:
        _k = _g["name"]
        if _k not in _merged:
            _merged[_k] = {"name": _k, "bodies": list(_g["bodies"])}
        else:
            _have = set(_merged[_k]["bodies"])
            for _b in _g["bodies"]:
                if _b not in _have:
                    _merged[_k]["bodies"].append(_b)
                    _have.add(_b)
    out["groups"] = list(_merged.values())

    out["total"] = len(allb)
    out["ungrouped"] = [n for n in allb if n not in grouped]
    return out


def op_ungroup_folders(payload):
    """取消分组（Ungroup）：把分组文件夹解散，体回到 Geometry，不删体
       payload={"names":["组名",...]} 或 {"names":["*"]} 表示全部
    """
    names = [_txt(x) for x in ((payload or {}).get("names") or [])]
    all_flag = ("*" in names) or (not names)
    want = set(n for n in names if n != "*")
    model = _model()

    def cat_of(o):
        try:
            return _txt(o.DataModelObjectCategory)
        except Exception:
            return u""

    found = []
    seen_ids = set()

    def walk(nodes, depth):
        if depth > 8:
            return
        for x in nodes:
            try:
                _xid = id(x)
            except Exception:
                _xid = 0
            if _xid in seen_ids:
                continue
            seen_ids.add(_xid)
            if "roupingFolder" in cat_of(x):
                nm = _txt(_safe(x, "Name", u""))
                if all_flag or nm in want:
                    found.append((nm, x))
            try:
                kids = list(x.Children)
            except Exception:
                kids = []
            if kids:
                walk(kids, depth + 1)

    try:
        walk([model], 0)
    except Exception as e:
        return {"ok": False, "error": _txt(e)}

    done, failed = [], []
    for nm, obj in found:
        if _cancelled():
            return {"ok": False, "cancelled": True, "done": done, "failed": failed}
        ok = False
        for how in ("Ungroup", "DeleteTreeGroupAndChildren"):
            try:
                getattr(obj, how)()
                ok = True
                break
            except Exception as e:
                err = _txt(e)
        if ok:
            done.append(nm)
        else:
            failed.append(u"%s: %s" % (nm, err[:80]))
    return {"ok": not failed, "done": done, "failed": failed,
            "count": len(done), "found": len(found)}


def op_probe_ungroup(payload):
    """探测分组文件夹上可用的"取消分组"方法"""
    model = _model()
    out = {"ok": True, "found": None, "methods": []}

    def cat_of(o):
        try:
            return _txt(o.DataModelObjectCategory)
        except Exception:
            return u""

    def find(nodes, depth):
        if depth > 6 or out["found"] is not None:
            return
        for x in nodes:
            if "roupingFolder" in cat_of(x):
                out["found"] = _txt(_safe(x, "Name", u""))
                try:
                    out["methods"] = [n for n in dir(x) if not n.startswith("_")]
                except Exception:
                    pass
                return
            try:
                kids = list(x.Children)
            except Exception:
                kids = []
            if kids:
                find(kids, depth + 1)

    try:
        find([model], 0)
    except Exception as e:
        out["error"] = _txt(e)
    return out


def op_group_geometry(payload):
    """在 Geometry 树中按组建立文件夹并把体移入
       payload={"items":[{"group":"垫板","bodies":["...","..."]}]}
    """
    items = (payload or {}).get("items") or []
    model = _model()

    # 先收集已存在的分组名（避免重复建同名组）
    _existing = set()
    _folders = {}
    _members = {}

    def _scan_folders(nodes, depth):
        if depth > 6:
            return
        for x in nodes:
            try:
                c = _txt(x.DataModelObjectCategory)
            except Exception:
                c = u""
            if "roupingFolder" in c:
                try:
                    _nmx = _txt(x.Name)
                    _existing.add(_nmx)
                    _folders.setdefault(_nmx, x)
                    _ms = []
                    for _k in x.Children:
                        _kc = _txt(_k.DataModelObjectCategory)
                        if "Body" in _kc:
                            _ms.append(_txt(_k.Name))
                        elif "Part" in _kc:
                            try:
                                for _b2 in _k.Children:
                                    if "Body" in _txt(_b2.DataModelObjectCategory):
                                        _ms.append(_txt(_b2.Name))
                            except Exception:
                                pass
                    _members.setdefault(_nmx, set()).update(_ms)
                except Exception:
                    pass
            try:
                kids = list(x.Children)
            except Exception:
                kids = []
            if kids and "Body" not in c and "Part" not in c:
                _scan_folders(kids, depth + 1)

    try:
        _scan_folders([model], 0)
    except Exception:
        pass

    by_name = {}
    for b in _bodies():
        by_name[_txt(_safe(b, "Name", u""))] = b

    made, failed, skipped = [], [], []
    moved = 0
    for it in items:
        if _cancelled():
            return {"ok": False, "cancelled": True, "groups": made,
                    "failed": failed, "moved": moved}
        gname = _txt(it.get("group") or u"分组")
        names = it.get("bodies") or []
        bodies = [by_name[n] for n in names if n in by_name]
        if not bodies:
            failed.append(u"%s: 没有可用体" % gname)
            continue

        if gname in _existing:
            _folder0 = _folders.get(gname)
            if _folder0 is None:
                skipped.append(u"%s（已存在，跳过）" % gname)
                continue
            try:
                _folder0.Ungroup()
            except Exception:
                try:
                    _folder0.DeleteTreeGroupAndChildren()
                except Exception:
                    skipped.append(u"%s（已存在且无法更新，跳过）" % gname)
                    continue
            _existing.discard(gname)
            _folders.pop(gname, None)

        folder = None
        last_err = u""
        # AddTreeGroupingFolder(对象集合)：直接把体传进去，一次成型
        try:
            folder = model.AddTreeGroupingFolder(bodies)
        except Exception as e:
            last_err = _txt(e)
            folder = None
        if folder is not None:
            try:
                folder.Name = gname
            except Exception:
                pass
            made.append(u"%s(%d 体)" % (gname, len(bodies)))
            moved += len(bodies)
            continue
        if folder is None:
            failed.append(u"%s: 建分组失败 %s" % (gname, last_err[:80]))
            continue

        # 2) 把体放进文件夹（多种方式）
        ok_n = 0
        for b in bodies:
            done = False
            for how in ("AddObject", "children_add", "parent"):
                try:
                    if how == "AddObject":
                        folder.AddObject(b)
                    elif how == "children_add":
                        folder.Children.Add(b)
                    else:
                        b.Parent = folder
                    done = True
                    break
                except Exception:
                    done = False
            if done:
                ok_n += 1
        moved += ok_n
        made.append(u"%s(%d/%d)" % (gname, ok_n, len(bodies)))

    return {"ok": not failed, "groups": made, "failed": failed,
            "moved": moved, "skipped": skipped}


def op_rename_bodies(payload):
    """批量重命名体：payload={"items":[{"body":"旧名","name":"新名"}]}
       或 {"mode":"prefix","items":[{"body":"旧名","group":"组名"}]}
    """
    items = (payload or {}).get("items") or []
    mode = _txt((payload or {}).get("mode") or u"exact")
    by_name = {}
    for b in _bodies():
        by_name[_txt(_safe(b, "Name", u""))] = b

    renamed, failed, skipped = [], [], []
    used_names = set()
    try:
        for _b0 in _bodies():
            used_names.add(_txt(_safe(_b0, "Name", u"")))
    except Exception:
        pass
    idx = {}
    for it in items:
        if _cancelled():
            return {"ok": False, "cancelled": True, "renamed": renamed,
                    "failed": failed}
        old = _txt(it.get("body") or u"")
        b = by_name.get(old)
        if b is None:
            failed.append(u"%s: 找不到" % old)
            continue
        if mode == "prefix":
            g = _txt(it.get("group") or u"未分类")
            _m = None
            try:
                import re as _re
                _m = _re.match(r"^(.+?)_(\d+)$", old)
            except Exception:
                _m = None
            if _m and _m.group(1) == g:
                skipped.append(old)
                continue
            # 序号从该组已有最大号之后继续，且保证全局不重复
            if g not in idx:
                _maxn = 0
                for _b in _bodies():
                    _bn = _txt(_safe(_b, "Name", u""))
                    _mm = None
                    try:
                        _mm = _re.match(r"^" + _re.escape(g) + r"_(\d+)$", _bn)
                    except Exception:
                        _mm = None
                    if _mm:
                        try:
                            _maxn = max(_maxn, int(_mm.group(1)))
                        except Exception:
                            pass
                for _bn in used_names:
                    _mm = None
                    try:
                        _mm = _re.match(r"^" + _re.escape(g) + r"_(\d+)$", _bn)
                    except Exception:
                        _mm = None
                    if _mm:
                        try:
                            _maxn = max(_maxn, int(_mm.group(1)))
                        except Exception:
                            pass
                idx[g] = _maxn
            idx[g] = idx.get(g, 0) + 1
            new = u"%s_%02d" % (g, idx[g])
        else:
            new = _txt(it.get("name") or u"")
        if not new or new == old:
            skipped.append(old)
            continue
        try:
            b.Name = new
            by_name.pop(old, None)
            by_name[new] = b
            renamed.append({"old": old, "new": new})
        except Exception as e:
            failed.append(u"%s: %s" % (old, _txt(e)[:80]))
    return {"ok": not failed, "renamed": renamed, "failed": failed,
            "skipped": skipped, "count": len(renamed)}


def op_list_bodies(payload):
    """列出全部体的几何信息（含最终类别名，用于核对与建模记忆）"""
    factor = _len_factor()

    # 先算"最终类别"，保证和界面上的类别一致
    recs = _body_records()
    grouped = _final_categories(recs)
    final_cat = {}
    for cat, items in grouped.items():
        for r in items:
            final_cat[r["name"]] = cat

    out = []
    for b in _bodies():
        _sup = bool(_safe(b, "Suppressed", False))
        name = _txt(_safe(b, "Name", u"?"))
        lx = _mm_value(_safe(b, "LengthX", 0))
        ly = _mm_value(_safe(b, "LengthY", 0))
        lz = _mm_value(_safe(b, "LengthZ", 0))
        cy = _mm_value(_safe(b, "CentroidY", 0))
        cx = _mm_value(_safe(b, "CentroidX", 0))
        cz = _mm_value(_safe(b, "CentroidZ", 0))
        gid = None
        try:
            gid = int(_safe(b, "GetGeoBody")().Id)
        except Exception:
            pass
        # 跳过无几何的空壳节点（例如 Part 占位节点：无 id、尺寸全 0）
        if gid is None and lx <= 0 and ly <= 0 and lz <= 0:
            continue
        out.append({"name": name, "id": gid, "suppressed": _sup,
                    "lx": round(lx, 1), "ly": round(ly, 1), "lz": round(lz, 1),
                    "cx": round(cx, 1), "cy": round(cy, 1), "cz": round(cz, 1),
                    "shell": u"urface" in _txt(_safe(b, "GeometryType", u"")),
                    "vol": round(_vol_value(_safe(b, "Volume", 0)), 1),
                    "category": final_cat.get(name) or _category_of(name, u"未分类")})
    out.sort(key=lambda d: (d["category"], d["name"]))
    return {"ok": True, "count": len(out), "bodies": out}


def op_failed_detail(payload):
    """失败体清单：以 Mechanical 的消息为依据区分黄色失败 / 粉色过期"""
    # ---- 1) 体基本信息 ----
    bodies = []
    for b in _bodies():
        try:
            if _safe(b, "Suppressed", False):
                continue
        except Exception:
            pass
        st = _txt(_safe(b, "ObjectState", u""))
        gid = None
        try:
            gid = int(b.GetGeoBody().Id)
        except Exception:
            pass
        dims = sorted([_mm_value(_safe(b, "LengthX", 0)),
                       _mm_value(_safe(b, "LengthY", 0)),
                       _mm_value(_safe(b, "LengthZ", 0))])
        bodies.append({"obj": b, "name": _txt(_safe(b, "Name", u"")),
                       "state": st, "id": gid, "dims": dims})

    # ---- 2) 从消息里取"真正失败"的体 ----
    failed_names = set()
    msgs_info = []
    try:
        msgs = list(_extapi().Application.Messages)
    except Exception:
        msgs = []
    _st = _stamp_read()
    _start_ts = float(_st.get("mesh_start_ts") or 0)
    for m in msgs:
        try:
            text = _txt(_safe(m, "DisplayString", u""))
            sev = _txt(_safe(m, "Severity", u""))
            ts = _txt(_safe(m, "TimeStamp", u""))
        except Exception:
            continue
        # 只采信本次网格之后的失败消息（避免旧消息造成误判）
        if _start_ts > 0 and ts:
            try:
                import datetime as _dt
                _t = _dt.datetime.strptime(ts.strip(), "%Y/%m/%d %H:%M:%S")
                if _t.timestamp() < _start_ts - 5:
                    continue
            except Exception:
                pass
        low = text.lower()
        is_fail = (sev.lower() == u"error") or (u"fail" in low) or (u"无法" in text)
        src_obj = None
        try:
            src_obj = _safe(m, "Source")
        except Exception:
            src_obj = None
        sname = u""
        if src_obj is not None:
            try:
                sname = _txt(_safe(src_obj, "Name", u""))
            except Exception:
                sname = u""
        if is_fail:
            msgs_info.append({"text": text[:120], "severity": sev,
                              "source": sname, "time": ts})
            if sname and (u"Body" in _txt(src_obj.GetType().Name if src_obj is not None else u"")
                          or sname in set(x["name"] for x in bodies)):
                failed_names.add(sname)

    # ---- 3) 组织输出 ----
    out = []
    pink = []
    for item in bodies:
        b = item["obj"]
        name = item["name"]
        st = item["state"]
        has_fix = False
        try:
            want = u"SIZE_FIX_%s" % name
            for c in (_mesh().Children or []):
                if _txt(_safe(c, "Name", u"")) == want:
                    has_fix = True
                    break
        except Exception:
            has_fix = False

        defeature = None
        fix_size = None
        try:
            want = u"SIZE_FIX_%s" % name
            for c in (_mesh().Children or []):
                if _txt(_safe(c, "Name", u"")) != want:
                    continue
                fix_size = round(_mm_value(_safe(c, "ElementSize", 0)), 2) or None
                for p in c.Properties:
                    if _txt(_safe(p, "Name", u"")) == "LocalDefeaturingTolerance":
                        sv = _txt(_safe(p, "StringValue", u""))
                        if sv and not sv.startswith(u"Default"):
                            try:
                                defeature = float(sv.split()[0])
                            except Exception:
                                pass
                        break
                break
        except Exception:
            pass

        dims = item["dims"]
        row = {"name": name, "id": item["id"], "state": st,
               "meshed": (st == u"Meshed"),
               "fix_size": fix_size, "defeature": defeature,
               "min_dim": round(dims[0], 1) if dims and dims[0] > 0 else None,
               "max_dim": round(dims[-1], 1) if dims else None}

        if st == u"Meshed":
            if has_fix:
                out.append(row)          # 历史记录：已成功，绿灯
            continue
        if name in failed_names:
            out.append(row)              # 黄色：真失败
        else:
            pink.append(row)             # 粉色：过期，不列入失败

    # 回退：有失败消息但没指向具体体时，非 Meshed 全部当失败（标注 uncertain）
    uncertain = False
    if not failed_names and msgs_info:
        uncertain = True
        out2 = []
        for item in bodies:
            if item["state"] == u"Meshed":
                continue
            out2.append({"name": item["name"], "id": item["id"],
                         "state": item["state"], "meshed": False,
                         "fix_size": None, "defeature": None,
                         "min_dim": round(item["dims"][0], 1) if item["dims"] and item["dims"][0] > 0 else None,
                         "max_dim": round(item["dims"][-1], 1) if item["dims"] else None})
        if out2:
            out = out2
            pink = []

    out.sort(key=lambda d: d["name"])
    try:
        els = int(_safe(_mesh(), "Elements", 0))
    except Exception:
        els = 0
    return {"ok": True, "count": len(out), "bodies": out,
            "raw_count": len(out) + len(pink),
            "pink_count": len(pink),
            "pink": [p["name"] for p in pink][:60],
            "stale": els <= 0,
            "mesh_state": _txt(_safe(_mesh(), "ObjectState", u"-")),
            "uncertain": uncertain,
            "messages": msgs_info[:8]}

def op_set_body_size(payload):
    """给单个体设置/更新 SIZE_FIX 尺寸"""
    body_name = _txt((payload or {}).get("body_name") or u"")
    try:
        size = float((payload or {}).get("size"))
    except Exception:
        return {"ok": False, "error": u"size 无效"}
    if not body_name:
        return {"ok": False, "error": u"缺少 body_name"}

    target = None
    bid = None
    for b in _bodies():
        if _txt(_safe(b, "Name", u"")) == body_name:
            target = b
            try:
                bid = int(b.GetGeoBody().Id)
            except Exception:
                bid = None
            break
    if target is None or bid is None:
        return {"ok": False, "error": u"找不到体 %s" % body_name}

    # 找已有的 SIZE_FIX
    ctrl = None
    for c in (_mesh().Children or []):
        nm = _txt(_safe(c, "Name", u""))
        if not nm.startswith(u"SIZE_FIX_"):
            continue
        try:
            ids = [int(x) for x in (_safe(_safe(c, "Location"), "Ids") or [])]
        except Exception:
            continue
        if bid in ids:
            ctrl = c
            break

    from Ansys.Core.Units import Quantity
    if ctrl is not None:
        try:
            ctrl.ElementSize = Quantity(u"%.3f [mm]" % size)
            out = {"ok": True, "control": _txt(_safe(ctrl, "Name", u"")),
                   "size_mm": size, "created": False,
                   "value": _txt(_safe(ctrl, "ElementSize", u""))}
            # 关键：从类别控制项里摘除，否则类别尺寸仍会覆盖该体
            try:
                _rm, _er = _exclude_body_from_categories(bid, body_name)
                if _rm:
                    out["excluded_from"] = _rm
                if _er:
                    out["exclude_errors"] = _er
            except Exception as _e:
                out["exclude_errors"] = [_txt(_e)[:60]]
            return out
        except Exception:
            return {"ok": False, "error": _tail(traceback.format_exc(), 300)}

    ctrl, err = _new_sizing_named(u"SIZE_FIX_" + body_name.replace(u"\\", u"_")[:24],
                                  [bid], size)
    if ctrl is None:
        return {"ok": False, "error": err}
    _out2 = {"excluded_from": []}
    try:
        _rm2, _er2 = _exclude_body_from_categories(bid, body_name)
        if _rm2:
            _out2["excluded_from"] = _rm2
        if _er2:
            _out2["exclude_errors"] = _er2
    except Exception as _e:
        _out2["exclude_errors"] = [_txt(_e)[:60]]
    _out2.update({"ok": True,
                  "control": _txt(_safe(ctrl, "Name", u"")),
                  "size_mm": size,
                  "created": True,
                  "value": _txt(_safe(ctrl, "ElementSize", u""))})
    return _out2


def op_set_bodies_size(payload):
    """批量给多个失败体设修复尺寸；可选顺带重新生成网格"""
    items = (payload or {}).get("items") or []
    regen = bool((payload or {}).get("regenerate"))
    results = []
    ok_all = True
    for it in items:
        if _cancelled():
            results.append({"body": it.get("body_name"), "ok": False,
                            "error": u"已被用户停止"})
            ok_all = False
            break
        r = op_set_body_size(it)
        if not r.get("ok"):
            ok_all = False
        results.append({"body": it.get("body_name"), "ok": bool(r.get("ok")),
                        "control": r.get("control"), "created": r.get("created"),
                        "error": r.get("error")})
        _progress(u"set_bodies_size", u"设置修复尺寸", len(results), len(items))

    out = {"ok": ok_all, "count": len(results), "results": results}
    if regen:
        _progress(u"set_bodies_size", u"重新生成网格")
        m = op_mesh({"timeout": 1800})
        out["mesh"] = {"ok": m.get("ok"), "elements": m.get("elements"),
                       "nodes": m.get("nodes"),
                       "failed": len(m.get("failed_bodies") or []),
                       "mesh_state": m.get("mesh_state")}
    return out


def op_add_to_control(payload):
    """把若干体加进已有控制项的作用域（保留原有的体）"""
    cname = _txt((payload or {}).get("control") or u"")
    names = (payload or {}).get("bodies") or []
    if not cname or not names:
        return {"ok": False, "error": u"缺少 control 或 bodies"}

    ctrl = None
    for c in (_mesh().Children or []):
        if _txt(_safe(c, "Name", u"")) == cname:
            ctrl = c
            break
    if ctrl is None:
        return {"ok": False, "error": u"找不到控制项 %s" % cname}

    ids = []
    try:
        ids = [int(x) for x in (_safe(_safe(ctrl, "Location"), "Ids") or [])]
    except Exception:
        ids = []

    id_by_name = {}
    for b in _bodies():
        if _safe(b, "Suppressed", False):
            continue
        try:
            id_by_name[_txt(_safe(b, "Name", u""))] = int(b.GetGeoBody().Id)
        except Exception:
            continue

    added, missing = [], []
    for nm in names:
        gid = id_by_name.get(nm)
        if gid is None:
            missing.append(nm)
            continue
        if gid not in ids:
            ids.append(gid)
            added.append(nm)

    if not added:
        return {"ok": True, "control": cname, "added": [], "missing": missing,
                "total_ids": len(ids), "message": u"已包含，无需改动"}

    manager = _extapi().SelectionManager
    try:
        info = manager.CreateSelectionInfo(_sel_enum())
        info.Ids = list(ids)
        ctrl.Location = info
    except Exception:
        return {"ok": False, "error": _tail(traceback.format_exc(), 300)}

    return {"ok": True, "control": cname, "added": added, "missing": missing,
            "total_ids": len(ids)}


def op_delete_controls(payload):
    """按名字删除控制项；names=[...] 或 prefix='SIZE_'"""
    names = [n for n in ((payload or {}).get("names") or []) if n]
    prefix = (payload or {}).get("prefix") or u""
    nameset = set(names)
    removed, failed = [], []
    for c in list(_mesh().Children or []):
        if _cancelled():
            return {"ok": False, "cancelled": True, "removed": removed,
                    "failed": failed, "error": u"已被用户停止"}
        nm = _txt(_safe(c, "Name", u""))
        hit = nm in nameset or (prefix and nm.startswith(prefix))
        if not hit:
            continue
        try:
            c.Delete()
            removed.append(nm)
        except Exception:
            failed.append(nm)
    return {"ok": not failed, "removed": removed, "count": len(removed),
            "failed": failed}


def op_body_quality_scan(payload):
    """扫描体的几何质量：面数/最小面面积/最短边/微小面个数
    微小面(sliver)和极短边是网格失败最常见的原因。
    """
    factor = _len_factor()
    out = []
    for b in _bodies():
        if _safe(b, "Suppressed", False):
            continue
        name = _txt(_safe(b, "Name", u"?"))
        lx = _mm_value(_safe(b, "LengthX", 0))
        ly = _mm_value(_safe(b, "LengthY", 0))
        lz = _mm_value(_safe(b, "LengthZ", 0))
        dims = sorted([d for d in (lx, ly, lz) if d > 0])
        small_dim = dims[0] if dims else 0.0
        item = {"name": name, "dim_small": round(small_dim, 2),
                "faces": 0, "edges": 0, "min_face_area": None,
                "min_edge_mm": None, "sliver_faces": 0, "short_edges": 0}
        faces = []
        try:
            faces = list(b.Faces)
        except Exception:
            faces = []
        item["faces"] = len(faces)
        areas = []
        for f in faces:
            try:
                a = float(_safe(f, "Area", 0.0))
                if a > 0:
                    areas.append(a / (factor * factor))
            except Exception:
                pass
        if areas:
            item["min_face_area"] = round(min(areas), 6)
            ref = (small_dim * small_dim) if small_dim > 0 else 0
            if ref > 0:
                item["sliver_faces"] = len([a for a in areas if a < ref * 0.01])
        edges = []
        try:
            edges = list(b.Edges)
        except Exception:
            edges = []
        item["edges"] = len(edges)
        lens = []
        for e in edges:
            try:
                L = float(_safe(e, "Length", 0.0))
                if L > 0:
                    lens.append(L / factor)
            except Exception:
                pass
        if lens:
            item["min_edge_mm"] = round(min(lens), 4)
            if small_dim > 0:
                item["short_edges"] = len([L for L in lens if L < small_dim * 0.02])
        # 便捷判据
        item["bad"] = bool(
            (item["min_edge_mm"] is not None and small_dim > 0 and
             item["min_edge_mm"] < small_dim * 0.005) or
            item["sliver_faces"] > 0 or item["short_edges"] > 0
        )
        out.append(item)
    out.sort(key=lambda d: (-d["sliver_faces"], -d["short_edges"]))
    return {"ok": True, "count": len(out), "bodies": out,
            "bad_count": len([x for x in out if x["bad"]])}


def op_diag_states(payload):
    """诊断：网格与各控制项的 ObjectState / State / 错误信息"""
    mesh = _mesh()
    out = {"mesh": {}, "children": [], "problems": []}

    def _state(obj):
        return {"name": _txt(_safe(obj, "Name", u"")),
                "category": _txt(_safe(obj, "DataModelObjectCategory", u"")),
                "ObjectState": _txt(_safe(obj, "ObjectState", u"")),
                "State": _txt(_safe(obj, "State", u"")),
                "Suppressed": bool(_safe(obj, "Suppressed", False))}

    try:
        out["mesh"] = _state(mesh)
        out["mesh"]["elements"] = _safe(mesh, "Elements", 0)
        out["mesh"]["nodes"] = _safe(mesh, "Nodes", 0)
        for attr in ("ElementSize", "MinimumSize", "DefeatureTolerance",
                     "CurvatureNormalAngle", "CaptureCurvature", "CaptureProximity"):
            try:
                out["mesh"][attr] = _txt(getattr(mesh, attr))
            except Exception:
                pass
    except Exception:
        out["mesh"]["error"] = _tail(traceback.format_exc(), 300)

    for c in list(_mesh().Children or []):
        st = _state(c)
        try:
            st["elementsize"] = _txt(_safe(c, "ElementSize", u""))
        except Exception:
            pass
        try:
            st["method"] = _txt(_safe(c, "Method", u""))
        except Exception:
            pass
        try:
            st["scope"] = len(list(_safe(_safe(c, "Location"), "Ids") or []))
        except Exception:
            st["scope"] = 0
        out["children"].append(st)
        if st["ObjectState"] not in (u"FullyDefined", u"Solved", u"UpToDate", u"NotSolved"):
            out["problems"].append(st)
        elif st["Suppressed"]:
            out["problems"].append(st)
        elif st["scope"] == 0:
            out["problems"].append(st)
    return {"ok": True, "result": out}


def op_geo_faces(payload):
    """查询几何拓扑：面数/边数/最小面面积/最短边长（判断是否有小特征、突出）"""
    want = _txt((payload or {}).get("body") or u"")
    factor = _len_factor()
    out = []
    for b in _bodies():
        if _safe(b, "Suppressed", False):
            continue
        nm = _txt(_safe(b, "Name", u""))
        if want and nm != want:
            continue
        item = {"name": nm, "faces": None, "edges": None,
                "min_face_area": None, "min_edge_mm": None,
                "small_faces": 0, "short_edges": 0, "err": None}
        try:
            geo = b.GetGeoBody()
        except Exception:
            geo = None
        faces, edges = [], []
        try:
            faces = list(geo.Faces) if geo is not None else []
        except Exception as e1:
            item["err"] = u"faces: %s" % e1
        try:
            edges = list(geo.Edges) if geo is not None else []
        except Exception as e2:
            item["err"] = (item["err"] or u"") + u" edges: %s" % e2
        item["faces"] = len(faces)
        item["edges"] = len(edges)

        areas = []
        for f in faces:
            try:
                a = float(f.Area) / (factor * factor)
                if a > 0:
                    areas.append(a)
            except Exception:
                pass
        lens = []
        for e in edges:
            try:
                L = float(e.Length) / factor
                if L > 0:
                    lens.append(L)
            except Exception:
                pass
        if areas:
            areas.sort()
            item["min_face_area"] = round(areas[0], 4)
            item["area_p10"] = round(areas[max(0, len(areas) // 10)], 2)
            med = areas[len(areas) // 2]
            item["small_faces"] = len([a for a in areas if med > 0 and a < med * 0.01])
        if lens:
            lens.sort()
            item["min_edge_mm"] = round(lens[0], 4)
            mede = lens[len(lens) // 2]
            item["short_edges"] = len([l for l in lens if mede > 0 and l < mede * 0.02])
        out.append(item)
    return {"ok": True, "count": len(out), "bodies": out}


def op_read_plan(payload):
    """读取 Mechanical 里现有的网格规划：每个控制项 + 它覆盖的体"""
    # geo id -> 体名
    id_name = {}
    bodies_all = []
    for b in _bodies():
        if _safe(b, "Suppressed", False):
            continue
        nm = _txt(_safe(b, "Name", u"?"))
        try:
            gid = int(_safe(b, "GetGeoBody")().Id)
        except Exception:
            gid = None
        if gid is not None:
            id_name[gid] = nm
        bodies_all.append(nm)

    controls = []
    covered = set()
    try:
        children = list(_mesh().Children or [])
    except Exception:
        children = []
    for c in children:
        nm = _txt(_safe(c, "Name", u""))
        cat = _txt(_safe(c, "DataModelObjectCategory", u""))
        ids = []
        try:
            ids = [int(x) for x in (_safe(_safe(c, "Location"), "Ids") or [])]
        except Exception:
            pass
        names = [id_name[i] for i in ids if i in id_name]
        covered.update(names)
        item = {"name": nm, "category": cat, "count": len(names),
                "bodies": names}
        try:
            v = _mm_value(_safe(c, "ElementSize", 0))
            item["size_mm"] = round(v, 2) if v > 0 else None
        except Exception:
            item["size_mm"] = None
        try:
            item["method"] = _txt(_safe(c, "Method", u"")) or None
        except Exception:
            item["method"] = None
        for extra in ("Behavior", "SweepNumberDivisions", "FreeFaceMeshType",
                      "ElementOrder", "Algorithm"):
            try:
                val = _safe(c, extra, None)
                if val is not None:
                    item[extra] = _txt(val)
            except Exception:
                pass
        controls.append(item)

    controls.sort(key=lambda d: -d["count"])
    uncovered = sorted([n for n in bodies_all if n not in covered])
    return {"ok": True, "controls": controls, "control_count": len(controls),
            "body_count": len(bodies_all), "uncovered_count": len(uncovered),
            "uncovered": uncovered[:80]}


def op_controls_from_plan(payload):
    """按"记忆方案"建控制项：plan = [{category, size, method, bodies:[体名]}]"""
    plan = (payload or {}).get("plan") or []
    if not plan:
        return {"ok": False, "error": u"plan 为空"}

    ids_by_name = {}
    for b in _bodies():
        if _safe(b, "Suppressed", False):
            continue
        try:
            ids_by_name[_txt(_safe(b, "Name", u""))] = int(b.GetGeoBody().Id)
        except Exception:
            continue

    created, methods, errors, skipped = [], [], [], []
    for item in plan:
        if _cancelled():
            errors.append(u"已被用户停止")
            break
        cat = _txt(item.get("category"))
        names = item.get("bodies") or []
        ids = [ids_by_name[n] for n in names if n in ids_by_name]
        if not ids:
            skipped.append(u"%s（找不到体）" % cat)
            continue
        try:
            size = float(item.get("size") or 30)
        except Exception:
            size = 30.0

        # 尺寸控制项：已有则改尺寸，没有则新建
        existing = _size_ctrls().get(cat)
        if existing is not None:
            try:
                from Ansys.Core.Units import Quantity
                existing.ElementSize = Quantity(u"%.3f [mm]" % size)
                created.append(u"%s（更新 %s）" % (cat, size))
            except Exception:
                errors.append(u"%s: 更新尺寸失败" % cat)
        else:
            ctrl, err = _new_sizing(cat, ids, size)
            if ctrl is None:
                errors.append(u"%s: %s" % (cat, err))
            else:
                # 用类别原名，不加 SIZE_ 前缀（保持用户自己的命名）
                try:
                    if _txt(_safe(ctrl, "Name", u"")) != cat:
                        ctrl.Name = cat
                except Exception:
                    pass
                created.append(u"%s（新建 %s）" % (cat, size))

        # 方法
        method = _txt(item.get("method") or u"Automatic")
        try:
            div = item.get("divisions")
            if div is None and method == u"Sweep":
                div = 2          # 薄板类默认 2 层
            okm, errm = _set_method_for(cat, ids, method, div)
            if okm:
                if method not in (u"", u"Automatic"):
                    methods.append(u"%s=%s" % (cat, method))
            else:
                errors.append(u"%s 方法: %s" % (cat, errm))
        except Exception:
            errors.append(u"%s 方法异常" % cat)
        _progress(u"controls_from_plan", u"按记忆建控制项",
                  len(created) + len(errors), len(plan))

    return {"ok": not errors, "created": created, "methods": methods,
            "errors": errors, "skipped": skipped,
            "category_count": len(plan)}


def _is_plate_like(ids):
    """判断作用域里的体是不是"薄板/薄壁结构"：按壁厚判断，不看长细比。
       实心件壁厚=最薄边；空心件由填充率反推壁厚。
    """
    try:
        walls = []
        id_set = set(int(x) for x in (ids or []))
        for b in _bodies():
            try:
                if int(b.GetGeoBody().Id) not in id_set:
                    continue
            except Exception:
                continue
            dims = sorted([_mm_value(_safe(b, "LengthX", 0)),
                           _mm_value(_safe(b, "LengthY", 0)),
                           _mm_value(_safe(b, "LengthZ", 0))])
            if dims[0] <= 0:
                continue
            vol = _vol_value(_safe(b, "Volume", 0))
            bbox = dims[0] * dims[1] * dims[2] or 1.0
            fill = max(0.0, min(1.0, vol / bbox))
            if fill >= 0.6:
                walls.append(dims[0])          # 实心：最薄边
            else:
                w = 0.5 * dims[1] * (1.0 - (max(0.0, 1.0 - fill)) ** 0.5)
                walls.append(max(0.3, min(w, dims[0])))
        if not walls:
            return False
        walls.sort()
        return walls[len(walls) // 2] <= 8.0   # 壁厚 ≤ 8mm 即视为薄板结构
    except Exception:
        return False


def _set_auto_thin(ctrl):
    """把方法控制项的 Src/Trg Selection 设为 Automatic Thin（按标签验证）"""
    try:
        prop = ctrl.PropertyByAPIName(u"SourceTargetSelection")
        for i in range(0, 8):
            try:
                prop.InternalValue = i
            except Exception:
                return False
            if _txt(prop.StringValue).strip().lower() == u"automatic thin":
                return True
        return False
    except Exception:
        return False


def _apply_sweep_divisions(ctrl, method, divisions, ids=None):
    """扫掠层数（穿过厚度方向分段数）；任何异常都不外抛"""
    try:
        if method not in (u"Sweep", u"MultiZone") or not divisions:
            return False
        if ids is not None and not _is_plate_like(ids):
            return False
        try:
            n = int(divisions)
        except Exception:
            return False
        for prop in (u"SweepNumDivisions", u"SweepNumberDivisions"):
            try:
                setattr(ctrl, prop, n)
                return True
            except Exception:
                pass
        for pname in (u"SweepNumDivisions", u"Divisions"):
            try:
                obj = ctrl.PropertyByAPIName(pname)
                if obj is not None:
                    obj.InternalValue = n
                    return True
            except Exception:
                pass
        return False
    except Exception:
        return False


def _set_method_for(cat, ids, method, divisions=None):
    """给指定类别（按给定体 id）设置方法控制项"""
    existing = _method_ctrls().get(cat)
    if method in (u"", u"Automatic"):
        if existing is not None:
            try:
                existing.Delete()
            except Exception:
                pass
        return True, u""
    manager = _extapi().SelectionManager
    info = None
    try:
        info = manager.CreateSelectionInfo(_sel_enum())
    except Exception:
        info = None
    if info is None:
        return False, u"CreateSelectionInfo 失败"
    try:
        info.Ids = list(ids)
    except Exception:
        return False, u"Ids 赋值失败"
    if existing is not None:
        try:
            existing.Location = info
        except Exception:
            pass
        ok, err = _set_method(existing, method)
        if ok:
            if method == u"Sweep":
                _set_auto_thin(existing)
            _apply_sweep_divisions(existing, method, divisions, ids)
        return ok, err
    try:
        adder = _safe(_mesh(), "AddAutomaticMethod")
        ctrl = adder()
        ctrl.Name = u"METHOD_" + cat
        ctrl.Location = info
        ok, err = _set_method(ctrl, method)
        if ok:
            if method == u"Sweep":
                _set_auto_thin(ctrl)
            _apply_sweep_divisions(ctrl, method, divisions, ids)
        return ok, err
    except Exception:
        return False, _tail(traceback.format_exc(), 200)


def op_delete_fix_controls(payload):
    """删除全部 SIZE_FIX_* 控制项（仅这些，不动类别控制项）"""
    keep = set((payload or {}).get("keep") or [])
    deleted = []
    kept = []
    try:
        children = list(_mesh().Children or [])
    except Exception:
        return {"ok": False, "error": u"读取控制项失败"}
    for c in children:
        nm = _txt(_safe(c, "Name", u""))
        if not nm.startswith(u"SIZE_FIX_"):
            continue
        if nm in keep:
            kept.append(nm)
            continue
        try:
            c.Delete()
            deleted.append(nm)
        except Exception:
            kept.append(nm)
    return {"ok": True, "deleted_count": len(deleted), "deleted": deleted[:40],
            "kept": kept[:40]}


def op_prune_controls(payload):
    """删除"类别已不存在"的控制项（分类规则变化后留下的孤儿），SIZE_FIX_ 保留"""
    recs = _body_records()
    grouped = _final_categories(recs)
    valid = set(grouped.keys())
    deleted = []
    kept = []
    try:
        children = list(_mesh().Children or [])
    except Exception:
        return {"ok": False, "error": u"读取控制项失败"}
    for c in children:
        nm = _txt(_safe(c, "Name", u""))
        if nm.startswith(u"SIZE_FIX_"):
            kept.append(nm)
            continue
        suffix = None
        for pre in (u"SIZE_", u"METHOD_", u"SWEEP_", u"SHELL_"):
            if nm.startswith(pre):
                suffix = nm[len(pre):].replace(u"_实体", u"")
                break
        if suffix is None:
            kept.append(nm)
            continue
        if suffix in valid:
            kept.append(nm)
            continue
        try:
            c.Delete()
            deleted.append(nm)
        except Exception:
            kept.append(nm)
    return {"ok": True, "deleted": deleted, "kept": len(kept),
            "valid_categories": len(valid)}


def op_clear_all(payload):
    """一键清理：删除 Mesh 下的全部控制项（尺寸/方法/扫掠/壳体等）"""
    try:
        children = list(_mesh().Children or [])
    except Exception:
        return {"ok": False, "error": _tail(traceback.format_exc(), 400)}

    removed = []
    failed = []
    for c in children:
        if _cancelled():
            return {"ok": False, "cancelled": True, "count": len(removed),
                    "removed": removed[:40], "failed": failed,
                    "error": u"已被用户停止"}
        nm = _txt(_safe(c, "Name", u""))
        try:
            c.Delete()
            removed.append(nm)
        except Exception:
            failed.append(nm)
    return {"ok": len(failed) == 0, "count": len(removed),
            "removed": removed, "failed": failed,
            "remaining": len(list(_mesh().Children or []))}


def op_get_params(payload):
    """列出某控制项的参数（优先 Details 面板可见项）"""
    ctrl, err = _find_ctrl(payload)
    if ctrl is None:
        return {"ok": False, "error": err}

    name = _txt(_safe(ctrl, "Name", u""))
    method = _txt(_safe(ctrl, "Method", u"")) or u"Automatic"
    # 允许前端指定"将要使用的方法"，从而显示该方法专属参数
    requested = _txt((payload or {}).get("method") or u"").strip()
    if requested:
        method = requested

    params = _collect_from_dir(ctrl)
    source = "dir"
    if not params:
        params = _collect_from_props(ctrl) or []
        source = "VisibleProperties"

    key = method if method in _METHOD_PARAMS else u"Automatic"
    primary_names = _METHOD_PARAMS.get(key, _DEFAULT_PARAMS)
    for p in params:
        p["primary"] = p["api"] in primary_names
        p["group"] = _group_of(p["api"])
    params.sort(key=lambda p: (0 if p["primary"] else 1, p["group"], p["api"]))

    groups = []
    for p in params:
        if p["group"] not in groups:
            groups.append(p["group"])

    return {"ok": True, "control": name, "method": method,
            "source": source, "params": params, "groups": groups}


def op_set_param(payload):
    """按 API 名写一个参数"""
    ctrl, err = _find_ctrl(payload)
    if ctrl is None:
        return {"ok": False, "error": err}

    api = _txt((payload or {}).get("api") or u"")
    raw = (payload or {}).get("value")
    if not api:
        return {"ok": False, "error": u"缺少 api 参数"}

    current = _safe(ctrl, api, _UNSET)
    if current is _UNSET:
        return {"ok": False, "error": u"控制项没有属性 %s" % api}

    # 目标值转换
    target = raw
    kind, _, options = _param_kind(current)
    try:
        if kind == "bool":
            if isinstance(raw, _STR_TYPES):
                target = _txt(raw).strip().lower() in (u"true", u"1", u"yes", u"是")
            else:
                target = bool(raw)
        elif kind == "number":
            f = float(raw)
            target = int(f) if abs(f - int(f)) < 1e-9 and isinstance(current, int) else f
        elif kind == "enum":
            import System
            t = current.GetType()
            member = _txt(raw)
            if member not in options:
                return {"ok": False, "error": u"%s 不是合法枚举（可选：%s）"
                        % (member, u", ".join(options))}
            target = System.Enum.Parse(t, member, True)
        elif kind == "quantity":
            from Ansys.Core.Units import Quantity
            text = _txt(raw)
            if u"[" not in text:
                unit = u"[mm]"
                try:
                    getter = _safe(ctrl, "PropertyByAPIName")
                    prop = getter(api) if callable(getter) else None
                    unit = _txt(_safe(prop, "Unit", u"[mm]") or u"[mm]")
                except Exception:
                    pass
                text = u"%s %s" % (_txt(raw), unit)
            target = Quantity(text)
    except Exception:
        return {"ok": False, "error": _tail(traceback.format_exc(), 400)}

    # 写入：优先走 Property.InternalValue
    written = False
    try:
        getter = _safe(ctrl, "PropertyByAPIName")
        if callable(getter):
            prop = getter(api)
            if prop is not None:
                prop.InternalValue = target
                written = True
    except Exception:
        written = False
    if not written:
        try:
            setattr(ctrl, api, target)
            written = True
        except Exception:
            return {"ok": False, "error": _tail(traceback.format_exc(), 400)}

    after = _txt(_safe(ctrl, api, u"?"))
    return {"ok": True, "control": _txt(_safe(ctrl, "Name", u"")),
            "api": api, "value": after}


# 注册到命令表
OPS = {
    "ping": op_ping,
    "mesh_messages": op_mesh_messages,
    "probe_controls_all": op_probe_controls_all,
    "probe_msg_fields": op_probe_msg_fields,
    "probe_app_messages": op_probe_app_messages,
    "probe_states": op_probe_states,
    "probe_ctrl_scope": op_probe_ctrl_scope,
    "set_local_defeature": op_set_local_defeature,
    "probe_local_defeature": op_probe_local_defeature,
    "probe_props_list": op_probe_props_list,
    "probe_sizing_reflect": op_probe_sizing_reflect,
    "probe_sizing_defeature": op_probe_sizing_defeature,
    "probe_mesh_methods": op_probe_mesh_methods,
    "probe_body_defeature": op_probe_body_defeature,
    "probe_defeature": op_probe_defeature,
    "fix_body": op_fix_body,
    "probe_fix_props": op_probe_fix_props,
    "probe_save_api": op_probe_save_api,
    "dedupe_names": op_dedupe_names,
    "fix_dup_names": op_fix_dup_names,
    "probe_all_bodies": op_probe_all_bodies,
    "save_project": op_save_project,
    "probe_messages": op_probe_messages,
    "set_units": op_set_units,
    "project_info": op_project_info,
    "status": op_status,
    "scan_categories": op_scan_categories,
    "get_params": op_get_params,
    "set_param": op_set_param,
    "clear_all": op_clear_all,
    "failed_detail": op_failed_detail,
    "set_body_size": op_set_body_size,
    "set_bodies_size": op_set_bodies_size,
    "add_to_control": op_add_to_control,
    "delete_controls": op_delete_controls,
    "quality_scan": op_body_quality_scan,
    "geo_faces": op_geo_faces,
    "diag_states": op_diag_states,
    "method_props": op_method_props,
    "set_src_thin": op_set_src_thin,
    "dir_control": op_dir_control,
    "dir_mesh": op_dir_mesh,
    "ui_props": op_ui_props,
    "src_target": op_src_target,
    "prop_dir": op_prop_dir,
    "set_string_value": op_set_string_value,
    "set_by_label": op_set_by_label,
    "pre_mesh_check": op_pre_mesh_check,
    "dedupe_controls": op_dedupe_controls,
    "validate_controls": op_validate_controls,
    "cat_params_v2": op_cat_params_v2,
    "body_counts": op_body_counts,
    "dir_body": op_dir_body,
    "body_quality": op_body_quality,
    "read_body_quality": op_read_body_quality,
    "quality_for_bodies": op_quality_for_bodies,
    "probe_perbody": op_probe_perbody,
    "probe_perbody2": op_probe_perbody2,
    "quality_metrics": op_quality_metrics,
    "quality_one": op_quality_one,
    "diagnose": op_diagnose,
    "set_methods_thin": op_set_methods_thin,
    "read_plan": op_read_plan,
    "controls_from_plan": op_controls_from_plan,
    "prune_controls": op_prune_controls,
    "ensure_controls": op_ensure_controls,
    "list_bodies": op_list_bodies,
    "rename_bodies": op_rename_bodies,
    "group_geometry": op_group_geometry,
    "probe_ungroup": op_probe_ungroup,
    "ungroup_folders": op_ungroup_folders,
    "geom_groups": op_geom_groups,
    "probe_geom_groups": op_probe_geometry_groups,
    "probe_tree": op_probe_tree,
    "apply": op_apply,
    "probe_folder": op_probe_folder,
    "dump_geom_ids": op_dump_geom_ids,
    "mesh": op_mesh,
    "fix_failed": op_fix_failed,
    "quality": op_quality,
    "exec": op_exec,
}


# ============================================================================
#  轮询
# ============================================================================
def _poll():
    # 心跳：让外部软件知道代理还活着（节流 3 秒写一次）
    try:
        now = time.time()
        if now - _hb[0] >= 3.0:
            _hb[0] = now
            _ts = time.strftime("%Y-%m-%d %H:%M:%S")
            _write_json(STATE_PATH, {"msg": u"alive", "ts": _ts})
            # 注册本实例（供服务端列出多个 Mechanical）
            try:
                _write_json(REG_PATH, {"pid": AGENT_PID, "version": AGENT_VERSION,
                                       "ts": _ts, "busy": bool(_busy)})
            except Exception:
                pass
    except Exception:
        pass

    cmd = _read_json(CMD_PATH)
    if not cmd:
        return
    try:
        os.remove(CMD_PATH)
    except Exception:
        pass

    op = (cmd.get("op") or u"").strip()
    seq = cmd.get("seq")
    fn = OPS.get(op)
    if fn is None:
        _write_json(RES_PATH, {"seq": seq, "ok": False, "error": u"未知命令: %s" % op})
        return
    # 每次新命令开始前清掉旧的取消标记
    _clear_cancel()
    _progress(op, u"开始")
    try:
        result = fn(cmd.get("payload") or {})
    except Exception:
        result = {"ok": False, "error": _tail(traceback.format_exc(), 2000)}
    _progress(op, u"已结束" if not result.get("cancelled") else u"已停止")
    result["seq"] = seq
    result["op"] = op
    result["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_json(RES_PATH, result)
    _write_json(STATE_PATH, {"last_op": op, "ts": result["ts"], "ok": result.get("ok")})


ERR_PATH = os.path.join(IPC_DIR, "bridge_error.txt")


def _log_err(stage, exc_text):
    try:
        if not os.path.isdir(IPC_DIR):
            os.makedirs(IPC_DIR)
        f = codecs.open(ERR_PATH, "w", encoding="utf-8")
        try:
            f.write(u"[%s] %s\n\n%s" % (stage, time.strftime("%Y-%m-%d %H:%M:%S"), exc_text))
        finally:
            f.close()
    except Exception:
        pass


def start():
    # 幂等：已经有计时器在跑就不重复启动
    if _keep:
        try:
            _write_json(STATE_PATH, {"msg": u"agent already running",
                                     "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        except Exception:
            pass
        print(u"[bridge] 已在运行，跳过重复启动")
        return True

    # 先落一个"正在启动"标记，便于外部判断代理是否被调用
    try:
        if not os.path.isdir(IPC_DIR):
            os.makedirs(IPC_DIR)
        _write_json(STATE_PATH, {"msg": u"agent loading",
                                 "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
    except Exception:
        pass

    # 每次启动自动把单位切成 mm/kg/N
    try:
        _ok_u, _msg_u, _names_u = _set_units_mm()
        print(u"[bridge] 单位切换: %s" % _msg_u)
        if not _ok_u:
            print(u"[bridge] 可用单位枚举: %s" % (u", ".join(_names_u[:20])))
    except Exception:
        pass

    # ---------------- 方式一：System.Timers.Timer + Dispatcher 调度到 UI 线程
    try:
        import clr
        try:
            clr.AddReference("WindowsBase")
        except Exception:
            pass
        from System.Windows.Threading import Dispatcher
        from System.Timers import Timer as SysTimer
        from System import Action

        ui_dispatcher = Dispatcher.CurrentDispatcher

        def _run_on_ui():
            if _busy:
                return
            _busy.append(1)
            try:
                _poll()
            except Exception:
                pass
            finally:
                _busy.remove(1)

        action = Action(_run_on_ui)

        def on_elapsed(sender, args):
            try:
                ui_dispatcher.BeginInvoke(action)
            except Exception:
                pass

        timer = SysTimer()
        timer.Interval = 1000.0
        timer.AutoReset = True
        timer.Elapsed += on_elapsed
        timer.Start()
        _keep.append(timer)
        _write_json(STATE_PATH, {"msg": u"agent started (Timers+Dispatcher)",
                                 "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        print(u"[bridge] 已启动（Timers + Dispatcher）：%s" % IPC_DIR)
        return True
    except Exception:
        _log_err(u"Timers+Dispatcher", traceback.format_exc())

    # ---------------- 方式二：WinForms Timer（也在 UI 线程回调）
    try:
        import clr
        try:
            clr.AddReference("System.Windows.Forms")
        except Exception:
            pass
        from System.Windows.Forms import Timer as WfTimer

        timer2 = WfTimer()
        timer2.Interval = 1000

        def on_tick2(sender, args):
            if _busy:
                return
            _busy.append(1)
            try:
                _poll()
            except Exception:
                pass
            finally:
                _busy.remove(1)

        timer2.Tick += on_tick2
        timer2.Start()
        _keep.append(timer2)
        _write_json(STATE_PATH, {"msg": u"agent started (WinForms.Timer)",
                                 "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        print(u"[bridge] 已启动（WinForms Timer）：%s" % IPC_DIR)
        return True
    except Exception:
        _log_err(u"WinFormsTimer", traceback.format_exc())

    # ---------------- 方式三：System.Timers.Timer 直接回调（兜底，线程不安全）
    try:
        from System.Timers import Timer as SysTimer2

        def on_elapsed2(sender, args):
            if _busy:
                return
            _busy.append(1)
            try:
                _poll()
            except Exception:
                pass
            finally:
                _busy.remove(1)

        timer3 = SysTimer2()
        timer3.Interval = 1000.0
        timer3.AutoReset = True
        timer3.Elapsed += on_elapsed2
        timer3.Start()
        _keep.append(timer3)
        _write_json(STATE_PATH, {"msg": u"agent started (Timers direct)",
                                 "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        print(u"[bridge] 已启动（Timers 直调，注意线程安全）：%s" % IPC_DIR)
        return True
    except Exception:
        _log_err(u"TimersDirect", traceback.format_exc())

    return False


start()
