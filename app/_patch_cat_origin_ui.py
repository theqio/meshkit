# -*- coding: utf-8 -*-
"""在 loadCategories 里核对数据来源实例"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\static\index.html"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

OLD = """    LAST_CATS = r.categories || [];
    LAST_SUG = r.suggestion || null;
    renderCategories(LAST_CATS, r.methods_available);"""

NEW = """    // 核对：这份列表来自哪个实例
    try{
      if(r.pid && r.pid !== INSTANCES_TARGET_PID()){
        log('⚠ 类别列表来自 ' + (r.project || r.pid) +
            '，与当前目标 ' + INSTANCES_TARGET_LABEL() + ' 不一致，正在重新对齐');
        loadAgents();
      }else if(r.project){
        log('类别列表来源：' + r.project + '（' + (r.categories||[]).length + ' 类）');
      }
    }catch(e){}
    LAST_CATS = r.categories || [];
    LAST_SUG = r.suggestion || null;
    renderCategories(LAST_CATS, r.methods_available);"""

if OLD in src:
    src = src.replace(OLD, NEW, 1)
    io.open(P, "w", encoding="utf-8").write(src)
    print("已插入来源核对")
else:
    print("未找到插入点")
