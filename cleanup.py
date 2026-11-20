# -*- coding: utf-8 -*-
"""清理无用生成文件（保留源码、配置、最新报告与最新截图）"""
import os
import re
import shutil

APP = r"D:\ai\meshkit\app"
REP = r"D:\ai\meshkit\reports"

deleted = {"files": 0, "kb": 0}
by_cat = {}


def rm(path):
    try:
        sz = os.path.getsize(path)
    except Exception:
        sz = 0
    try:
        os.remove(path)
        deleted["files"] += 1
        deleted["kb"] += sz / 1024.0
        return True
    except Exception:
        return False


def note(cat, n, kb):
    d = by_cat.setdefault(cat, [0, 0])
    d[0] += n
    d[1] += kb


# ---- A) 源码备份 ----
n = kb = 0
for root, dirs, files in os.walk(APP):
    for f in files:
        if ".bak_" in f:
            p = os.path.join(root, f)
            s = os.path.getsize(p) / 1024.0
            if rm(p):
                n += 1
                kb += s
note("源码备份 .bak_*", n, kb)

# ---- B) __pycache__ ----
n = kb = 0
for root, dirs, files in os.walk(r"D:\ai\meshkit"):
    if "__pycache__" in dirs:
        p = os.path.join(root, "__pycache__")
        s = sum(os.path.getsize(os.path.join(p, f))
                for f in os.listdir(p) if os.path.isfile(os.path.join(p, f))) / 1024.0
        try:
            shutil.rmtree(p)
            n += 1
            kb += s
        except Exception:
            pass
note("__pycache__", n, kb)

# ---- C) 一次性补丁脚本 ----
n = kb = 0
for f in os.listdir(APP):
    if f.startswith("_patch_") and f.endswith(".py"):
        p = os.path.join(APP, f)
        s = os.path.getsize(p) / 1024.0
        if rm(p):
            n += 1
            kb += s
note("一次性补丁脚本 _patch_*.py", n, kb)

# ---- D) 空日志 bystate_*.log / 探针日志 ----
n = kb = 0
for f in os.listdir(REP):
    if re.match(r"^(bystate_|coarsen\d*_|after_units_|fix\d*_|fail_state_|failed_bodies_|"
                r"finish_v2_|final_report_|final_sizes_|sizing_plan\d*_|restore_names_|"
                r"rename_|bad_bodies_|mesh_status_|fix_sizes_|tune\d*_|refine_|fix_failed_|"
                r"post_restart_|clear_sol_|audit_|generate_only_|generate_compare_|"
                r"fix_sweep\d*_|fix_shell_cat_|probe_quantity_|global_settings_|structure_|"
                r"reset_\d|reset_b_|pull_out_|elem_type_|worst_elements_|sweep_)", f) and f.endswith(".log"):
        p = os.path.join(REP, f)
        s = os.path.getsize(p) / 1024.0
        if rm(p):
            n += 1
            kb += s
note("调试日志 *.log", n, kb)

# ---- E) _last_*.txt 中间快照 ----
n = kb = 0
for f in os.listdir(REP):
    if f.startswith("_last_") or (f.startswith("_") and f.endswith(".txt")):
        p = os.path.join(REP, f)
        s = os.path.getsize(p) / 1024.0
        if rm(p):
            n += 1
            kb += s
note("_last_*.txt 快照", n, kb)

# ---- F) 调试截图 _*.png + headless_ui ----
n = kb = 0
for f in os.listdir(REP):
    if (f.startswith("_") and f.endswith(".png")) or f.startswith("headless_ui"):
        p = os.path.join(REP, f)
        s = os.path.getsize(p) / 1024.0
        if rm(p):
            n += 1
            kb += s
note("调试截图 _*.png", n, kb)

# ---- G) 旧运行报告：按时间戳保留最新 2 组 ----
pat = re.compile(r"^(run|网格摘要|网格自动化报告|网格质量指标|人工决策清单|apply_manual_fix)_"
                 r"(\d{8}_\d{6})")
groups = {}
for f in os.listdir(REP):
    m = pat.match(f)
    if m:
        groups.setdefault(m.group(2), []).append(f)
stamps = sorted(groups)
old = stamps[:-2]
n = kb = 0
for st in old:
    for f in groups[st]:
        p = os.path.join(REP, f)
        s = os.path.getsize(p) / 1024.0
        if rm(p):
            n += 1
            kb += s
note("旧运行报告(保留最新2组)", n, kb)

# ---- H) UI 截图：保留最新 6 张 ----
shots = [f for f in os.listdir(REP) if f.startswith("MeshKitAI_") and f.endswith(".png")]
shots.sort(key=lambda f: os.path.getmtime(os.path.join(REP, f)))
n = kb = 0
for f in shots[:-6]:
    p = os.path.join(REP, f)
    s = os.path.getsize(p) / 1024.0
    if rm(p):
        n += 1
        kb += s
note("旧界面截图(保留最新6张)", n, kb)

# ---- I) 快照类：各保留最新 1 个 ----
n = kb = 0
for prefix in ("重命名映射_", "全局设置快照_", "网格控制项快照_", "Sweep控制项_",
               "简化探针_", "环境探针_"):
    fs = [f for f in os.listdir(REP) if f.startswith(prefix)]
    fs.sort(key=lambda f: os.path.getmtime(os.path.join(REP, f)))
    for f in fs[:-1]:
        p = os.path.join(REP, f)
        s = os.path.getsize(p) / 1024.0
        if rm(p):
            n += 1
            kb += s
note("旧快照(各留1个)", n, kb)

print("=== 已删除 ===")
for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1][1]):
    print("  %-24s %5d 个  %8.1f KB" % (k, v[0], v[1]))
print("\n合计: %d 个文件，释放 %.1f MB" % (deleted["files"], deleted["kb"] / 1024.0))

print("\n=== 剩余 ===")
tot = 0
cnt = 0
for root, dirs, files in os.walk(r"D:\ai\meshkit"):
    for f in files:
        tot += os.path.getsize(os.path.join(root, f))
        cnt += 1
print("文件 %d 个，共 %.1f MB" % (cnt, tot / 1024 / 1024))
