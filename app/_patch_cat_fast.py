# -*- coding: utf-8 -*-
"""类别接口提速：页面用「快速模式」——只用几何分组 + 配置，不读 Mesh 控制项
   （Mesh 控制项读取 scan_categories 在有网格时极慢，是卡顿主因）
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

# 1) 函数签名加 fast 参数
OLD = "def categories_from_recognition():"
NEW = "def categories_from_recognition(fast=False):"
if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("已加 fast 参数")

# 2) fast 时跳过 scan_categories
OLD2 = '''    ctrl_info = {}
    try:
        sc = call_bridge("scan_categories", timeout=120)'''
NEW2 = '''    ctrl_info = {}
    try:
        sc = {} if fast else call_bridge("scan_categories", timeout=120)'''
if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)
    print("fast 模式跳过 scan_categories")

# 3) fast 时也跳过 mesh 控制项合并（categories 分支里的）
OLD3 = '''                    # 合并 Mesh 里的控制项类别（Geometry 分组丢失时也能读全）
                    try:
                        _sc = call_bridge("scan_categories", timeout=90)'''
NEW3 = '''                    # 合并 Mesh 里的控制项类别（Geometry 分组丢失时也能读全）
                    try:
                        _sc = {} if fast else call_bridge("scan_categories", timeout=90)'''
if OLD3 in s:
    s = s.replace(OLD3, NEW3, 1)
    print("fast 模式跳过控制项合并")

# 4) 缓存里的 producer 用 fast
OLD4 = '''                _rec = cache_get(("categories", _tp_c), 60,
                                 categories_from_recognition)'''
NEW4 = '''                _rec = cache_get(("categories", _tp_c), 60,
                                 lambda: categories_from_recognition(fast=True))'''
if OLD4 in s:
    s = s.replace(OLD4, NEW4, 1)
    print("缓存改用 fast 模式")

# 5) 预热也用 fast
OLD5 = '''            (("categories", _tp), 60, categories_from_recognition),'''
NEW5 = '''            (("categories", _tp), 60,
             lambda: categories_from_recognition(fast=True)),'''
if OLD5 in s:
    s = s.replace(OLD5, NEW5, 1)
    print("预热改用 fast 模式")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
