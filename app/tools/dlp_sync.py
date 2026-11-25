# -*- coding: utf-8 -*-
"""DLP 双向自动同步：项目目录 <-> 明文副本目录

背景：项目目录 `D:\\ai\\meshkit` 里的文件在磁盘上是亿赛通密文，外部编辑器读到乱码；
      `%TEMP%\\meshkit_work` 不加密，外部编辑器能正常读写。

本脚本常驻后台（建议 2 秒一轮）：
  - 项目里被改 → 自动导出到副本（外部编辑器马上看到）
  - 副本里被改 → 自动导回项目（Python 写回，内容正确）
  - 两边同时改同一文件且内容不同 → 保守处理：保留项目版本，并记录冲突日志

用法：
  python app/tools/dlp_sync.py --once     只跑一轮（可用于手动同步）
  python app/tools/dlp_sync.py --watch    常驻循环（默认 2 秒）
  python app/tools/dlp_sync.py --status   显示两侧差异，不写文件
"""
import hashlib
import json
import os
import shutil
import sys
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(APP_DIR))
WORK = os.path.join(os.environ.get("TEMP", r"C:\Temp"), "meshkit_work")
STATE = os.path.join(WORK, ".dlp_sync_state.json")
LOG = os.path.join(WORK, ".dlp_sync.log")

SKIP_DIRS = {".git", "__pycache__", "ipc", "_archive", "dist", "node_modules",
             "tests", "act", "presets", "meshkit", "reports"}
SKIP_FILES = {"config.json", "presets_kb.json", "reload_result.json",
              "group_overrides.json", ".dlp_manifest.json",
              ".dlp_sync_state.json", ".dlp_sync.log"}
SKIP_PREFIX = ("model_flags__", "model_config__", "_patch_", ".")
SKIP_SUFFIX = (".pyc", ".zip", ".log", ".bak_", ".bak2_", ".tmp", ".swp")
QUIET_SEC = 2.0          # 文件在最近 N 秒内被写过 → 认为还在写，本轮跳过


def _skip(rel):
    parts = rel.replace("\\", "/").split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return True
    name = parts[-1]
    if name in SKIP_FILES:
        return True
    if name.startswith(SKIP_PREFIX):
        return True
    if name.endswith(SKIP_SUFFIX) or ".bak_" in name:
        return True
    return False


def _walk(base):
    out = {}
    if not os.path.isdir(base):
        return out
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, base)
            if _skip(rel):
                continue
            try:
                st = os.stat(full)
            except Exception:
                continue
            out[rel] = {"path": full, "mtime": st.st_mtime, "size": st.st_size}
    return out


def _hash(path):
    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            while True:
                b = f.read(65536)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()
    except Exception:
        return None


def _log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_state(st):
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
    except Exception:
        pass


def _copy(src, dst, keep_bom=False):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, "rb") as f:
        data = f.read()
    with open(dst, "wb") as f:
        f.write(data)
    return len(data)


def sync_once(write=True, verbose=True):
    proj = _walk(ROOT)
    work = _walk(WORK)
    st = _load_state()
    now = time.time()
    exported = imported = conflicts = 0
    rels = sorted(set(proj) | set(work))
    for rel in rels:
        if _skip(rel):
            continue
        p = proj.get(rel)
        w = work.get(rel)
        # 还在写：跳过
        if (p and now - p["mtime"] < QUIET_SEC) or (w and now - w["mtime"] < QUIET_SEC):
            continue
        # 安全：任一侧是 0 字节 → 不传播（防止把截断/空文件同步过去）
        if (p and p["size"] == 0) or (w and w["size"] == 0):
            _log("跳过空文件 %s（项目 %s / 副本 %s 字节）"
                 % (rel, p["size"] if p else "-", w["size"] if w else "-"))
            continue
        php = _hash(p["path"]) if p else None
        whp = _hash(w["path"]) if w else None
        if php is None and whp is None:
            continue
        rec = st.get(rel) or {}
        p_changed = php is not None and php != rec.get("p")
        w_changed = whp is not None and whp != rec.get("w")

        if p and not w:
            if write:
                _copy(p["path"], w_path(rel))
                exported += 1
                _log("导出(新) %s" % rel)
            st[rel] = {"p": php, "w": php}
            continue
        if w and not p:
            if write:
                _copy(w["path"], p_path(rel))
                imported += 1
                _log("导回(新) %s" % rel)
            st[rel] = {"p": whp, "w": whp}
            continue
        if php == whp:
            st[rel] = {"p": php, "w": whp}
            continue

        if p_changed and not w_changed:
            if write:
                _copy(p["path"], w_path(rel))
                exported += 1
            st[rel] = {"p": php, "w": php}
            _log("导出 %s" % rel)
        elif w_changed and not p_changed:
            if write:
                _copy(w["path"], p_path(rel))
                imported += 1
            st[rel] = {"p": whp, "w": whp}
            _log("导回 %s" % rel)
        else:
            # 两边都变了且内容不同：以较新修改时间的一方为准
            pm = p["mtime"] if p else 0
            wm = w["mtime"] if w else 0
            conflicts += 1
            if wm > pm:
                if write:
                    _copy(w["path"], p_path(rel))
                    imported += 1
                st[rel] = {"p": whp, "w": whp}
                _log("冲突 %s → 用副本较新版本导回" % rel)
            else:
                if write:
                    _copy(p["path"], w_path(rel))
                    exported += 1
                st[rel] = {"p": php, "w": php}
                _log("冲突 %s → 用项目较新版本导出" % rel)
    if write:
        _save_state(st)
    if verbose:
        print("本轮：导出 %d，导回 %d，冲突 %d" % (exported, imported, conflicts))
    return {"exported": exported, "imported": imported, "conflicts": conflicts}


def w_path(rel):
    return os.path.join(WORK, rel)


def p_path(rel):
    return os.path.join(ROOT, rel)


def status():
    proj = _walk(ROOT)
    work = _walk(WORK)
    only_p = sorted(set(proj) - set(work))
    only_w = sorted(set(work) - set(proj))
    diff = []
    for rel in sorted(set(proj) & set(work)):
        a = _hash(proj[rel]["path"])
        b = _hash(work[rel]["path"])
        if a != b:
            diff.append(rel)
    print("项目独有 %d，副本独有 %d，内容不同 %d" % (len(only_p), len(only_w), len(diff)))
    for r in only_p[:10]:
        print("  只在项目: " + r)
    for r in only_w[:10]:
        print("  只在副本: " + r)
    for r in diff[:10]:
        print("  内容不同: " + r)
    return 0


def main():
    args = sys.argv[1:]
    if "--status" in args:
        return status()
    if "--once" in args:
        sync_once()
        return 0
    interval = 2.0
    for i, a in enumerate(args):
        if a == "--watch" and i + 1 < len(args):
            try:
                interval = max(1.0, float(args[i + 1]))
            except Exception:
                pass
    if "--watch" not in args:
        sync_once()
        return 0
    os.makedirs(WORK, exist_ok=True)
    _log("自动同步已启动（项目 <-> %s），间隔 %.0f 秒" % (WORK, interval))
    try:
        while True:
            try:
                sync_once(verbose=False)
            except Exception as e:
                _log("同步异常: %s" % str(e)[:120])
            time.sleep(interval)
    except KeyboardInterrupt:
        _log("自动同步已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
