# -*- coding: utf-8 -*-
"""页面日志显示：每个写操作改在了哪个模型上"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\static\index.html"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

OLD = """  if(json && json.blocked){
    try{
      log('⛔ 已阻止：' + (json.error || '目标实例不在前台'));
      toast('已阻止：避免操作到别的模型');
    }catch(e){}
  }
  return json;"""

NEW = """  if(json && json.blocked){
    try{
      log('⛔ 已阻止：' + (json.error || '目标实例不在前台'));
      toast('已阻止：避免操作到别的模型');
    }catch(e){}
  }
  if(json && json.ran_on && json.ran_on.project){
    try{
      log('✔ 本次改在：' + json.ran_on.project +
          '（PID ' + json.ran_on.pid + '）');
    }catch(e){}
  }
  return json;"""

if OLD in src:
    src = src.replace(OLD, NEW, 1)
    io.open(P, "w", encoding="utf-8").write(src)
    print("OK: page logs target model for each write op")
else:
    print("NOT FOUND: api() snippet")
