# -*- coding: utf-8 -*-
"""修尺寸/数字显示不全：
   - 左栏 470 → 540px
   - 类别表允许横向滚动，最小宽度 600px
   - 类别名不换行（超长省略）
   - 经验库表同样允许横向滚动
"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\static\index.html"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

# 1) 左栏宽度
OLD = "main{display:grid;grid-template-columns:470px minmax(0,1fr) 360px;gap:14px;padding:14px;align-items:start}"
NEW = "main{display:grid;grid-template-columns:560px minmax(0,1fr) 340px;gap:14px;padding:14px;align-items:start}"
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("左栏已加宽到 560px")
else:
    print("未找到 main 布局")

# 2) 表格可滚动 + 列宽
OLD2 = "  .size-input{width:70px}"
NEW2 = """  .size-input{width:70px}
  #catTable{overflow-x:auto}
  #catTable table{min-width:600px;table-layout:auto}
  #catTable td:first-child,#catTable th:first-child{white-space:nowrap;max-width:200px;
    overflow:hidden;text-overflow:ellipsis}
  #catTable td.num,#catTable th.num{white-space:nowrap}
  #expList{overflow:auto}
  #expList table{min-width:520px}
  #expList td,#expList th{white-space:nowrap}"""
if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("已加表格滚动与列宽规则")
else:
    print("未找到 .size-input 样式")

io.open(P, "w", encoding="utf-8").write(src)
print("完成")
