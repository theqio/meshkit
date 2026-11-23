# -*- coding: utf-8 -*-
"""把「常用配置」升级为可勾选的知识包：
   1) 每条配置额外保存该模型的分组几何特征（部位 → 尺寸组合）
   2) 勾选多条后，新模型识别时把这些尺寸组合并入规则表
   3) 勾选的配置里的尺寸/方法自动套到同名类别（不用手点应用）
"""
import io
import json
import os
import shutil
import time

P = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

HELPER = r'''
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

'''

if "def kb_extra_rules(" not in src:
    anchor = "def zones_preview():" if "def zones_preview():" in src else "def scan_groups():"
    src = src.replace(anchor, HELPER.strip() + "\n\n\n" + anchor, 1)
    print("已加知识包函数")

ROUTES = '''            if path == "/api/kb":
                return self._json(kb_list())

            if path == "/api/kb/capture":
                body = self._body()
                if not bridge_alive():
                    return self._json({"ok": False, "error": "桥接代理未运行"})
                return self._json(kb_capture(body.get("name") or ""))

            if path == "/api/kb/toggle":
                body = self._body()
                return self._json(kb_toggle(body.get("name") or "",
                                            bool(body.get("on"))))

            if path == "/api/kb/delete":
                body = self._body()
                return self._json(kb_delete(body.get("name") or ""))

'''
if "/api/kb" not in src:
    anchor = '            if path == "/api/favorites":'
    src = src.replace(anchor, ROUTES + anchor, 1)
    print("已加 /api/kb 路由")

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
compile(io.open(P, encoding="utf-8").read(), P, "exec")
print("server 语法 OK")
