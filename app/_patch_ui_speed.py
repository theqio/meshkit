# -*- coding: utf-8 -*-
"""前端提速：请求带超时、避免叠加、降低轮询频率
   - api() 支持 timeout（默认 30s），超时直接返回 {ok:false, timeout:true}
   - refresh/loadAgents/loadCategories 加在途保护，不叠加
   - 轮询：状态 20s、实例 10s；页面隐藏时暂停
"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\static\index.html"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

# 1) api() 增加超时与在途统计
OLD = """async function api(path, opts){
  const r = await fetch(path, Object.assign({headers:{'Content-Type':'application/json'}}, opts||{}));
  const json = await r.json();"""
NEW = """async function api(path, opts){
  const opt = Object.assign({headers:{'Content-Type':'application/json'}}, opts||{});
  // 默认 30 秒超时，避免 Mechanical 忙时页面一直转圈
  if(!opt.signal && !opt._noTimeout){
    const ctl = new AbortController();
    opt.signal = ctl.signal;
    setTimeout(() => ctl.abort(), opt._timeoutMs || 30000);
  }
  let r;
  try{
    r = await fetch(path, opt);
  }catch(e){
    if(e && e.name === 'AbortError') return {ok:false, timeout:true,
      error:'请求超时（Mechanical 可能正忙）'};
    throw e;
  }
  let json;
  try{ json = await r.json(); }
  catch(e){ return {ok:false, error:'返回不是 JSON（' + r.status + '）'}; }"""

if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("api() 已支持超时")
else:
    print("未找到 api()")

# 2) refresh 在途保护 + 状态超时 8s
OLD2 = """async function refresh(){
  try{
    const p = await api('/api/ping');"""
NEW2 = """let _refreshBusy = false;
async function refresh(){
  if(_refreshBusy) return;
  if(document.hidden) return;
  _refreshBusy = true;
  try{
    const p = await api('/api/ping', {_timeoutMs: 6000});"""
if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("refresh 已加在途保护")
else:
    print("未找到 refresh")

OLD3 = """    const st = await api('/api/status');
    LAST_STATUS = st;
    renderStatus(st);
  }catch(e){ log('状态读取失败：' + e); }
}"""
NEW3 = """    const st = await api('/api/status', {_timeoutMs: 12000});
    if(st && st.busy){
      $('bridgeText').textContent = 'Mechanical 忙：' + (st.step || '');
      return;
    }
    if(st && st.timeout){
      $('bridgeText').textContent = 'Mechanical 忙（状态读取超时）';
      return;
    }
    LAST_STATUS = st;
    renderStatus(st);
  }catch(e){ log('状态读取失败：' + e); }
  finally{ _refreshBusy = false; }
}"""
if OLD3 in src:
    src = src.replace(OLD3, NEW3, 1)
    print("status 已加忙处理")
else:
    print("未找到 status 段")

# 3) loadCategories 在途保护
OLD4 = """async function loadCategories(){"""
NEW4 = """let _catBusy = false;
async function loadCategories(){
  if(_catBusy) return;   // 不叠加"""
if OLD4 in src:
    src = src.replace(OLD4, NEW4, 1)
    print("loadCategories 已加在途保护")

# 给 loadCategories 的 try 加 finally 复位
i = src.find("let _catBusy = false;")
if i > 0:
    j = src.find("async function loadCategories(){", i)
    k = src.find("}catch(e){ log('类别读取异常：'+e); }", j)
    if k > 0:
        src = src[:k] + "}catch(e){ log('类别读取异常：'+e); }\n  finally{ _catBusy = false; }" + src[k+len("}catch(e){ log('类别读取异常：'+e); }"):]
        print("loadCategories 已加 finally")

# 4) loadAgents 在途保护 + 超时
OLD5 = """async function loadAgents(){
  const sel = $('agentSel');
  if(!sel) return;
  try{
    const r = await api('/api/instances');"""
NEW5 = """let _agentBusy = false;
async function loadAgents(){
  const sel = $('agentSel');
  if(!sel) return;
  if(_agentBusy) return;
  if(document.hidden) return;
  _agentBusy = true;
  try{
    const r = await api('/api/instances', {_timeoutMs: 10000});
    if(r && r.timeout) return;"""
if OLD5 in src:
    src = src.replace(OLD5, NEW5, 1)
    print("loadAgents 已加在途保护")
else:
    print("未找到 loadAgents")

OLD6 = """  }catch(e){ /* 忽略 */ }
}

"""
if src.count(OLD6) >= 1:
    i = src.find("_agentBusy = true;")
    k = src.find("  }catch(e){ /* 忽略 */ }", i)
    if k > 0:
        src = src[:k] + "  }catch(e){ /* 忽略 */ }\n  finally{ _agentBusy = false; }" + src[k+len("  }catch(e){ /* 忽略 */ }"):]
        print("loadAgents 已加 finally")

# 5) 轮询频率
src = src.replace("setInterval(refresh, 15000);", "setInterval(refresh, 20000);", 1)
src = src.replace("setInterval(loadAgents, 5000);", "setInterval(loadAgents, 10000);", 1)

io.open(P, "w", encoding="utf-8").write(src)
print("完成")
