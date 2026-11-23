# -*- coding: utf-8 -*-
"""识别改进：
   1) 尺寸表匹配改为「最近邻 + 容差」，支持模型尺寸略有差异（切割/不同批次）
   2) 新增储罐判定：三向都大、比例接近立方体、空心
"""
import io
import re
import shutil
import time

P = r"D:\ai\meshkit\app\geo_recognize.py"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

# ---------- 1) 最近邻匹配工具 ----------
HELPER = '''
# ---- 尺寸表最近邻匹配（带容差）----
_SIG_CACHE = {}


def _parse_sig(sig):
    try:
        parts = sig.split("x")
        if len(parts) != 3:
            return None
        return (float(parts[0]), float(parts[1]), float(parts[2]))
    except Exception:
        return None


def _match_size(dims, table, axis_tol=0.20, total_tol=0.15):
    """dims 已排序；table: [(sig_dims, label)]
       返回 (label, cost) 或 (None, None)
    """
    best = (None, None)
    for sig_dims, lab in table:
        if sig_dims is None:
            continue
        cost = 0.0
        ok = True
        for x, y in zip(dims, sig_dims):
            if y <= 0:
                ok = False
                break
            c = abs(x - y) / y
            if c > axis_tol:
                ok = False
                break
            if c > cost:
                cost = c
        if not ok or cost > total_tol:
            continue
        if best[1] is None or cost < best[1]:
            best = (lab, cost)
    return best
'''
if "_match_size(" not in src:
    src = src.replace("def _box(b):", HELPER.strip() + "\n\n\ndef _box(b):", 1)
    print("已加最近邻匹配")

# ---------- 2) 建立带尺寸的规则表 ----------
OLD = '''    rules = _load_json(RULES_PATH, {}).get("rules", {})
    sig2lab = {}
    for lab, info in rules.items():
        for s in (info.get("sizes") or []):
            sig2lab.setdefault(s.get("sig"), lab)'''
NEW = '''    rules = _load_json(RULES_PATH, {}).get("rules", {})
    sig_table = []
    for lab, info in rules.items():
        for s in (info.get("sizes") or []):
            sd = _parse_sig(s.get("sig") or "")
            if sd:
                sig_table.append((sd, lab))'''
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("已建立尺寸表")
else:
    print("警告：未找到尺寸表构建")

# ---------- 3) 储罐规则（放在尺寸表之前）----------
OLD2 = '''    # ---- 尺寸表 ----
    for n in names:
        if n in pred:
            continue
        lab = sig2lab.get(_sig(dmap[n]))
        if lab:
            pred[n] = lab'''
NEW2 = '''    # ---- 储罐：三向都大 + 接近立方体 + 空心 ----
    for n in names:
        if n in pred:
            continue
        b = dmap[n]
        d = _dims(b)
        if d[0] < 1500:
            continue
        ratio = d[2] / max(1.0, d[0])
        if ratio > 2.6:
            continue
        bbox = max(1.0, d[0] * d[1] * d[2])
        fill = (b.get("vol") or 0) / bbox
        if 0.05 <= fill <= 0.55:
            pred[n] = "储罐"

    # ---- 尺寸表（最近邻 + 容差）----
    for n in names:
        if n in pred:
            continue
        d = _dims(dmap[n])
        if d[0] <= 0:
            continue
        lab, cost = _match_size(d, sig_table)
        if lab:
            pred[n] = lab'''
if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("已加储罐规则 + 容差匹配")
else:
    print("警告：未找到尺寸表应用段")

io.open(P, "w", encoding="utf-8").write(src)
compile(io.open(P, encoding="utf-8").read(), P, "exec")
print("语法 OK")
