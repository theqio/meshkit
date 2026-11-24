# -*- coding: utf-8 -*-
"""加"看门狗"：页面卡在忙碌状态时自动恢复
   - 记录最后一次进度变化时间
   - 若进度显示已结束/无变化超过 25 秒 → 自动解除忙碌 + 刷新数据
   - 提供 ESC 键与「解除卡住」按钮手动恢复
"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\static\index.html"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

OLD = """function setBusy(on, label){
  BUSY = on;
  ['btnMesh','btnFix','btnQuality','btnApply','btnFailList','btnScanCat'].forEach(id => $(id).disabled = on);
  $('btnStop').hidden = !on;
  if(on){
    if(label) log(`${label} …`);
    startProgressPoll();
  }else{
    stopProgressPoll();
    $('progText').textContent = '';
  }
}"""

NEW = """/* 忙碌看门狗：防止 Mechanical 已完成但页面一直卡住 */
let _busyStartedAt = 0, _lastProgChange = 0, _lastProgSig = '', _watchdogTimer = null;

function _stopWatchdog(){
  if(_watchdogTimer){ clearInterval(_watchdogTimer); _watchdogTimer = null; }
}

function _startWatchdog(){
  _stopWatchdog();
  _busyStartedAt = Date.now();
  _lastProgChange = Date.now();
  _lastProgSig = '';
  _watchdogTimer = setInterval(async () => {
    if(!BUSY){ _stopWatchdog(); return; }
    let p = null;
    try{ p = await api('/api/progress', {_timeoutMs: 5000}); }catch(e){ p = null; }
    const sig = p ? `${p.op||''}|${p.step||''}|${p.done||''}` : '';
    const now = Date.now();
    if(sig && sig !== _lastProgSig){ _lastProgSig = sig; _lastProgChange = now; }
    const idle = now - _lastProgChange;
    const step = (p && p.step) || '';
    const finished = !step || step === '已结束' || step === '结束' || step === '完成';
    // 进度已结束 且 25 秒没变化 → 认为后端已完成，只是请求没回来
    if(finished && idle > 25000){
      log('⚠ 检测到 Mechanical 已结束，但页面仍在等待；已自动解除忙碌状态');
      forceUnbusy();
    }else if(idle > 600000){
      log('⚠ 10 分钟没有任何进度变化，已自动解除忙碌（请手动确认 Mechanical 状态）');
      forceUnbusy();
    }
  }, 4000);
}

function forceUnbusy(){
  _stopWatchdog();
  try{ setBusy(false); }catch(e){}
  try{ refresh(); loadCategories(); }catch(e){}
}

function setBusy(on, label){
  BUSY = on;
  ['btnMesh','btnFix','btnQuality','btnApply','btnFailList','btnScanCat'].forEach(id => {
    const el = $(id); if(el) el.disabled = on;
  });
  const st = $('btnStop'); if(st) st.hidden = !on;
  if(on){
    if(label) log(`${label} …`);
    startProgressPoll();
    _startWatchdog();
  }else{
    stopProgressPoll();
    _stopWatchdog();
    const pt = $('progText'); if(pt) pt.textContent = '';
  }
}

/* ESC 手动解除忙碌 */
document.addEventListener('keydown', (e) => {
  if(e.key === 'Escape' && BUSY){
    log('已按 ESC 手动解除忙碌状态');
    forceUnbusy();
  }
});"""

if OLD in src:
    src = src.replace(OLD, NEW, 1)
    io.open(P, "w", encoding="utf-8").write(src)
    print("已加忙碌看门狗 + ESC 解除")
else:
    print("未找到 setBusy")

# 顶部加一个"解除卡住"按钮（忙碌时才显示）
OLD2 = """  <button class="btn danger" id="btnStop" hidden>■ 停止</button>"""
NEW2 = """  <button class="btn danger" id="btnStop" hidden>■ 停止</button>
  <button class="btn" id="btnUnstick" hidden title="页面卡住时点这里解除"
          onclick="forceUnbusy()">解除卡住</button>"""
if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("已加解除按钮")

# 按钮显示/隐藏跟随忙碌
OLD3 = """  const st = $('btnStop'); if(st) st.hidden = !on;"""
NEW3 = """  const st = $('btnStop'); if(st) st.hidden = !on;
  const us = $('btnUnstick'); if(us) us.hidden = !on;"""
if OLD3 in src:
    src = src.replace(OLD3, NEW3, 1)
    print("解除按钮随忙碌显示")

io.open(P, "w", encoding="utf-8").write(src)
print("完成")
