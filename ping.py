# -*- coding: utf-8 -*-
# ============================================================================
#  ping.py —— 3 秒钟的"脚本执行链路"自检（什么都不碰）
#
#  在 Mechanical -> Automation -> Scripting 里执行任意一种：
#      execfile("D:/ai/meshkit/ping.py")          # 注意用正斜杠
#      或把本文件用"打开"按钮载入后点绿色 ▶
#
#  成功的标志（两个都要有）：
#      1) Mechanical 的 Console 里出现  PING OK
#      2) 磁盘上出现  D:\ai\meshkit\reports\_ping.txt
#
#  如果一条都没有 —— 说明不是脚本的问题，是"运行按钮/输入位置"没对上。
# ============================================================================
import os
import sys
import time

OUT_DIR = r"D:\ai\meshkit\reports"
OUT = os.path.join(OUT_DIR, "_ping.txt")

text = []
text.append(u"PING OK")
text.append(u"时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
text.append(u"sys.version: %s" % sys.version)
try:
    text.append(u"有 execfile: %s" % ("是" if callable(execfile) else u"否"))   # noqa: F821
except Exception:
    text.append(u"有 execfile: 否")
try:
    text.append(u"命名空间里有 ExtAPI: %s" % ("是" if "ExtAPI" in globals() else u"否"))
except Exception:
    text.append(u"命名空间里有 ExtAPI: 未知")

payload = u"\n".join(text)

try:
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)
except Exception:
    pass

written = False
try:
    import System
    from System.Text import Encoding
    System.IO.File.WriteAllText(OUT, payload, Encoding.UTF8)
    written = True
except Exception:
    try:
        fh = open(OUT, "wb")
        try:
            fh.write(payload.encode("utf-8"))
        finally:
            fh.close()
        written = True
    except Exception:
        written = False

try:
    print(payload)
    print(u"PING OK -> %s（写入成功: %s）" % (OUT, written))
except Exception:
    pass
