# -*- coding: utf-8 -*-
"""加回「生成网格前预估节点数」：
   - 服务端 /api/premesh：按各类别体体积 ÷ 尺寸³ 估算单元，换算节点
   - 超预算时页面弹确认，并列出最耗的类别
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

FUNC = '''
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
            vol = 0.0
            for n in names:
                b = bmap.get(n) or {}
                try:
                    vol += float(b.get("vol") or 0)
                except Exception:
                    pass
            if vol <= 0:
                continue
            est = vol / (size ** 3)
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

'''

if "def premesh_check(" not in s:
    s = s.replace("def _mech_busy():", FUNC.strip() + "\n\n\ndef _mech_busy():", 1)
    print("已加 premesh_check")

ROUTE = '''            if path == "/api/premesh":
                return self._json(premesh_check())

'''
if "/api/premesh" not in s:
    anchor = '            if path == "/api/model_key":'
    pos = s.find(anchor, s.find("def do_GET"))
    if pos > 0:
        s = s[:pos] + ROUTE + s[pos:]
        print("已加 /api/premesh")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("server 语法 OK")
