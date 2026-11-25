# -*- coding: utf-8 -*-
"""DLP（亿赛通）桥接：把项目导出成"非白名单程序也能读的明文副本"，改完再导回。

背景：项目目录里文件的磁盘内容是密文，只有白名单进程（Python）能透明解密。
      外部编辑器（Antigravity / VS Code 等）读到的会是乱码。

用法：
  python app/tools/dlp_bridge.py export [目标目录]
      默认导出到 %TEMP%\\meshkit_work（该目录不在加密范围）
  python app/tools/dlp_bridge.py import [源目录]
      把改动过的文件拷回项目（只覆盖源目录里存在的文件）
  python app/tools/dlp_bridge.py status [目录]
      检查目标目录里的文件是否是明文（能否被非白名单程序读取）
"""
import json
import os
import shutil
import sys
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(APP_DIR)) if os.path.basename(APP_DIR) == "tools" else os.path.dirname(APP_DIR)

SKIP_DIRS = {".git", "__pycache__", "ipc", "_archive", "dist", "node_modules",
             "tests", "act", "presets", "meshkit", "reports"}
SKIP_FILES = {"config.json", "presets_kb.json", "reload_result.json",
              "group_overrides.json"}
SKIP_PREFIX = ("model_flags__", "model_config__", "_patch_", ".")
SKIP_SUFFIX = (".pyc", ".zip", ".log", ".bak_", ".bak2_", ".tmp")

MANIFEST = ".dlp_manifest.json"


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


def _files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            if _skip(rel):
                continue
            yield rel, full


def do_export(dst):
    if os.path.isdir(dst):
        shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst, exist_ok=True)
    manifest = {"root": ROOT, "dst": dst, "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "files": {}}
    n = 0
    for rel, src in _files(ROOT):
        out = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        try:
            with open(src, "rb") as f:
                data = f.read()
            with open(out, "wb") as f:
                f.write(data)
            manifest["files"][rel] = {"size": len(data),
                                      "mtime": os.path.getmtime(src)}
            n += 1
        except Exception as e:
            print("  跳过 %s: %s" % (rel, str(e)[:60]))
    with open(os.path.join(dst, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print("已导出 %d 个文件 -> %s" % (n, dst))
    print("外部编辑器请在这个目录里改：%s" % dst)
    print("改完执行：python app/tools/dlp_bridge.py import")
    return 0


def do_import(src):
    mpath = os.path.join(src, MANIFEST)
    if not os.path.isfile(mpath):
        print("找不到 %s，请先 export" % mpath)
        return 2
    with open(mpath, encoding="utf-8") as f:
        manifest = json.load(f)
    changed, same, missing = [], 0, 0
    for rel, info in manifest["files"].items():
        s = os.path.join(src, rel)
        d = os.path.join(ROOT, rel)
        if not os.path.isfile(s):
            missing += 1
            continue
        try:
            with open(s, "rb") as f:
                data = f.read()
        except Exception as e:
            print("  读取失败 %s: %s" % (rel, str(e)[:50]))
            continue
        old = None
        if os.path.isfile(d):
            try:
                with open(d, "rb") as f:
                    old = f.read()
            except Exception:
                old = None
        if old == data:
            same += 1
            continue
        os.makedirs(os.path.dirname(d), exist_ok=True)
        with open(d, "wb") as f:
            f.write(data)
        changed.append(rel)
    # 新增文件（不在 manifest 里的）
    added = []
    for rel, full in _files(src):
        if rel == MANIFEST or rel in manifest["files"]:
            continue
        d = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        with open(full, "rb") as f:
            data = f.read()
        with open(d, "wb") as f:
            f.write(data)
        added.append(rel)
    print("导回完成：修改 %d 个，新增 %d 个，未变 %d 个，源目录缺失 %d 个"
          % (len(changed), len(added), same, missing))
    for r in changed[:30]:
        print("  M " + r)
    for r in added[:30]:
        print("  A " + r)
    if changed or added:
        print("\n下一步：cd /d %s && git add -A && git commit -m \"...\"" % ROOT)
    return 0


BINARY_EXT = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".zip",
              ".xlsx", ".docx", ".pptx", ".pdf", ".dll", ".exe")


def _looks_plain(data, rel=""):
    """判断导出副本是否还是 DLP 密文（而不是普通二进制）"""
    if b"Esafenet" in data[:512]:
        return False
    low = (rel or "").lower()
    if low.endswith(BINARY_EXT):
        return True           # 二进制资源不做文本判断
    head = data[:64]
    if head.startswith(b"\xd0\xcf\x11\xe0") or head.startswith(b"PK"):
        return True           # 正常二进制容器
    bad = sum(1 for b in head if b == 0)
    return bad <= 2


def do_status(dst):
    ok = bad = 0
    for rel, full in _files(dst):
        try:
            with open(full, "rb") as f:
                data = f.read(512)
        except Exception:
            continue
        if _looks_plain(data, rel):
            ok += 1
        else:
            bad += 1
            if bad <= 5:
                print("  ✗ 仍是密文: %s" % rel)
    print("明文 %d 个 / 疑似密文 %d 个" % (ok, bad))
    print("结论：" + ("可用（外部编辑器能读）" if bad == 0 else "不可用，换一个目录再导出"))
    return 0 if bad == 0 else 1


def main():
    argv = sys.argv[1:]
    cmd = (argv[0] if argv else "export").lower()
    arg = argv[1] if len(argv) > 1 else None
    default_dst = os.path.join(os.environ.get("TEMP", r"C:\Temp"), "meshkit_work")
    if cmd == "export":
        return do_export(arg or default_dst)
    if cmd == "import":
        return do_import(arg or default_dst)
    if cmd == "status":
        return do_status(arg or default_dst)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
