# -*- coding: utf-8 -*-
"""从 Antigravity 会话日志里重放 edit_file / write_file 操作。

用法：
  python replay_antigravity.py check   只比对（不写文件），报告能否全部命中
  python replay_antigravity.py apply   实际写入（先备份到 _archive/agy_backup/）
"""
import glob
import io
import json
import os
import shutil
import sys
import time

BRAIN = r"C:\Users\qiyuqiang\.gemini\antigravity\brain"
PROJECT = r"D:\ai\meshkit"
SRC_MARK = "meshkit_work"
BACKUP = os.path.join(PROJECT, "_archive", "agy_backup")


def load_calls():
    paths = glob.glob(os.path.join(BRAIN, "*", ".system_generated", "logs",
                                   "transcript_full.jsonl"))
    if not paths:
        raise SystemExit("找不到 Antigravity 会话日志")
    paths.sort(key=os.path.getmtime)
    calls = []
    seen = set()
    for p in paths:
        with io.open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                ts = d.get("created_at") or ""
                for tc in (d.get("tool_calls") or []):
                    name = (tc.get("name") or "").lower()
                    a = tc.get("args") or {}
                    path = a.get("Path") or ""
                    if not path or SRC_MARK not in path.replace("\\", "/"):
                        continue
                    if name not in ("edit_file", "write_file"):
                        continue
                    key = (name, path, a.get("Target") or "", a.get("Replacement") or "",
                           a.get("Content") or "")
                    if key in seen:
                        continue
                    seen.add(key)
                    calls.append({
                        "ts": ts, "tool": name, "path": path,
                        "target": a.get("Target"),
                        "replacement": a.get("Replacement"),
                        "content": a.get("Content"),
                        "overwrite": a.get("Overwrite"),
                    })
    return calls


def to_project(path):
    p = path.replace("\\", "/")
    i = p.find(SRC_MARK + "/")
    rel = p[i + len(SRC_MARK) + 1:]
    return os.path.join(PROJECT, rel.replace("/", os.sep)), rel


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "check").lower()
    calls = load_calls()
    print("从日志取到 %d 条文件修改操作" % len(calls))
    files = {}
    # 载入到内存，按顺序模拟
    order = [c for c in calls if c["tool"] == "write_file"] + \
            [c for c in calls if c["tool"] == "edit_file"]
    order.sort(key=lambda c: c["ts"])
    for c in order:
        dst, rel = to_project(c["path"])
        if rel not in files:
            if c["tool"] == "write_file":
                files[rel] = ""
            elif not os.path.isfile(dst):
                print("  ✗ 目标文件不存在: %s" % rel)
                continue
            else:
                files[rel] = io.open(dst, encoding="utf-8", errors="replace").read()
        cur = files[rel]
        if c["tool"] == "write_file":
            files[rel] = c["content"] or ""
            print("  W %s（整文件写入 %d 字符）" % (rel, len(files[rel])))
            continue
        tgt = c["target"] or ""
        if tgt and tgt in cur:
            files[rel] = cur.replace(tgt, c["replacement"] or "", 1)
            print("  E %s（%s）" % (rel, c["ts"][11:19]))
        else:
            head = (tgt or "")[:60].replace("\n", " ")
            print("  ✗ %s 命中失败 @%s  目标片段: %s" % (rel, c["ts"][11:19], head))
    print("\n涉及文件：")
    for rel, text in sorted(files.items()):
        dst = os.path.join(PROJECT, rel)
        old = io.open(dst, encoding="utf-8", errors="replace").read() if os.path.isfile(dst) else ""
        flag = "有变化" if text != old else "无变化"
        print("  %-34s %7d 字符  %s" % (rel, len(text), flag))
    if mode == "apply":
        os.makedirs(BACKUP, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        changed = 0
        for rel, text in files.items():
            dst = os.path.join(PROJECT, rel)
            old = io.open(dst, encoding="utf-8", errors="replace").read() if os.path.isfile(dst) else ""
            if text == old:
                continue
            bdir = os.path.join(BACKUP, stamp)
            bpath = os.path.join(bdir, rel)
            os.makedirs(os.path.dirname(bpath), exist_ok=True)
            if os.path.isfile(dst):
                shutil.copy2(dst, bpath)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with io.open(dst, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            changed += 1
        print("\n已写入 %d 个文件（备份在 %s）" % (changed, os.path.join(BACKUP, stamp)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
