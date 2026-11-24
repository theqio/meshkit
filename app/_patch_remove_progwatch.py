# -*- coding: utf-8 -*-
"""去掉多余的进度轮询（页面已有 startProgressPoll/#progText），只保留长超时"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\static\index.html"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

# 1) 删掉自定义轮询函数块
i = src.find("/* 长操作进度轮询：")
j = src.find("async function applyAll(")
if i >= 0 and j > i:
    src = src[:i] + src[j:]
    print("已删除自定义进度轮询块")

# 2) 删掉调用
for line in ("  startProgressWatch('应用设置与尺寸');\n",
             "  stopProgressWatch();\n",
             "  startProgressWatch('生成网格');\n"):
    if line in src:
        src = src.replace(line, "", 1)

io.open(P, "w", encoding="utf-8").write(src)
print("完成")
