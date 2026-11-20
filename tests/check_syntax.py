# -*- coding: utf-8 -*-
"""语法自检：用目标解释器（IronPython 2.7）把每个模块编译一遍。

用法:  ipy64 check_syntax.py
"""
from __future__ import print_function

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def collect():
    out = []
    for folder, _dirs, files in os.walk(ROOT):
        if os.sep + "." in folder:
            continue
        if os.path.basename(folder) in ("reports", "__pycache__"):
            continue
        for name in files:
            if name.endswith(".py"):
                out.append(os.path.join(folder, name))
    return sorted(out)


def main():
    failures = 0
    files = collect()
    for path in files:
        try:
            fh = open(path, "rb")
            try:
                source = fh.read()
            finally:
                fh.close()
            if source[:3] == b"\xef\xbb\xbf":
                source = source[3:]
            compile(source, path, "exec")
            print("OK   %s" % path)
        except Exception:
            failures += 1
            print("FAIL %s" % path)
            print(traceback.format_exc())
    print("")
    print("%d files, %d failures" % (len(files), failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
