# -*- coding: utf-8 -*-
"""长操作（应用/网格/修复/质量）不再被 30 秒超时掐断：
   - 这些调用单独给 20 分钟超时
   - 执行期间轮询 /api/progress，把进度显示在忙碌提示里
"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\static\index.html"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

# 1) 进度轮询工具
HELP = '''
/* 长操作进度轮询：把 Mechanical 的 progress 显示到忙碌提示里 */
let _progTimer = null;
function startProgressWatch(label){
  stopProgressWatch();
  _progTimer = setInterval(async () => {
    try{
      const p = await api('/api/progress', {_timeoutMs: 5000});
      if(p && p.step){
        const busy = $('busyText') || document.querySelector('.busy-text');
        const txt = label + '：' + p.step +
          (p.done != null && p.total ? `（${p.done}/${p.total}）` : '');
        if(busy) busy.textContent = txt;
      }
    }catch(e){}
  }, 3000);
}
function stopProgressWatch(){
  if(_progTimer){ clearInterval(_progTimer); _progTimer = null; }
}

'''
if "function startProgressWatch(" not in src:
    anchor = "async function applyAll("
    src = src.replace(anchor, HELP + anchor, 1)
    print("已加进度轮询工具")

# 2) applyAll：长超时 + 进度
OLD = """async function applyAll(){
  setBusy(true, '应用设置与尺寸');
  try{"""
NEW = """async function applyAll(){
  setBusy(true, '应用设置与尺寸（可能要 1~3 分钟）');
  startProgressWatch('应用设置与尺寸');
  try{"""
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("applyAll 已加进度提示")

OLD2 = """    const r = await api('/api/op', {method:'POST', body: JSON.stringify({
      op:'apply', payload:{settings: readSettingsFromUI(),
                           sizes: _sizes,
                           methods: _methods,
                           divisions: _divs}
    })});"""
NEW2 = """    const r = await api('/api/op', {_timeoutMs: 1200000, method:'POST', body: JSON.stringify({
      op:'apply', payload:{settings: readSettingsFromUI(),
                           sizes: _sizes,
                           methods: _methods,
                           divisions: _divs}
    })});"""
if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("applyAll 超时已放宽到 20 分钟")

OLD3 = """  }catch(e){ log('应用异常：'+e); }
  setBusy(false); refresh();
}"""
NEW3 = """  }catch(e){ log('应用异常：'+e); }
  stopProgressWatch();
  setBusy(false); refresh();
}"""
if OLD3 in src:
    src = src.replace(OLD3, NEW3, 1)
    print("applyAll 已收尾停止轮询")

# 3) 网格/修复/质量 也放宽
for old, new, tag in [
    ("{op:'mesh', payload:{clear: clearFirst}}", "{op:'mesh', payload:{clear: clearFirst}}", "mesh"),
]:
    pass

OLD4 = """  const r = await api('/api/op', {method:'POST', body: JSON.stringify(
    {op:'mesh', payload:{clear: clearFirst}})});"""
NEW4 = """  const r = await api('/api/op', {_timeoutMs: 3600000, method:'POST', body: JSON.stringify(
    {op:'mesh', payload:{clear: clearFirst}})});"""
if OLD4 in src:
    src = src.replace(OLD4, NEW4, 1)
    print("生成网格超时已放宽到 60 分钟")

OLD5 = """  setBusy(true, '生成网格（可能需要几分钟，请勿操作 Mechanical）');
  try{"""
NEW5 = """  setBusy(true, '生成网格（可能需要几分钟，请勿操作 Mechanical）');
  startProgressWatch('生成网格');
  try{"""
if OLD5 in src:
    src = src.replace(OLD5, NEW5, 1)

OLD6 = """  setBusy(false); refresh(); loadFailed(); loadErrorLog(); analyzeMeshRun();
}"""
NEW6 = """  stopProgressWatch();
  setBusy(false); refresh(); loadFailed(); loadErrorLog(); analyzeMeshRun();
}"""
if OLD6 in src:
    src = src.replace(OLD6, NEW6, 1)
    print("网格已加进度与收尾")

io.open(P, "w", encoding="utf-8").write(src)
print("完成")
