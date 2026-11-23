# -*- coding: utf-8 -*-
"""页面：实例下拉 + 一键连接按钮
   - 下拉列出所有 Mechanical（含未连接）
   - 点「连接」：已连接→切换；未连接→激活窗口+命令进剪贴板+页面给指引
   - 每 5 秒轮询，连上后自动变「已连接」
"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\static\index.html"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

# ---------- 1) HTML：替换原来的 select ----------
OLD_HTML = '''  <select id="agentSel" title="选择要控制的 Mechanical 实例"
          style="display:none;background:var(--panel2);border:1px solid var(--line);
                 color:var(--fg);border-radius:6px;padding:4px 8px;font-size:12.5px;
                 max-width:340px"></select>'''
NEW_HTML = '''  <select id="agentSel" title="选择要控制的 Mechanical 实例"
          style="background:var(--panel2);border:1px solid var(--line);
                 color:var(--fg);border-radius:6px;padding:4px 8px;font-size:12.5px;
                 max-width:300px"></select>
  <button class="btn" id="btnConnect" style="padding:4px 10px;font-size:12.5px"
          title="连接/切换到这个 Mechanical 实例">连接</button>'''
if OLD_HTML in src:
    src = src.replace(OLD_HTML, NEW_HTML, 1)
    print("已改 HTML：下拉 + 连接按钮")
else:
    print("警告：未找到 agentSel HTML")

# ---------- 2) JS：替换 loadAgents 与 selectAgent ----------
i = src.find("async function loadAgents(){")
j = src.find("async function selectAgent(", i)
k = src.find("\n}\n", j)
if i < 0 or j < 0 or k < 0:
    raise SystemExit("找不到 loadAgents/selectAgent")
end = k + len("\n}\n")

NEW_JS = r'''
let INSTANCES = [];

async function loadAgents(){
  const sel = $('agentSel');
  if(!sel) return;
  try{
    const r = await api('/api/instances');
    if(!r.ok) return;
    INSTANCES = r.instances || [];
    if(!INSTANCES.length){
      sel.innerHTML = '<option>没有检测到 Mechanical</option>';
      return;
    }
    sel.innerHTML = INSTANCES.map(x => {
      const st = x.connected ? '已连接' : '未连接';
      const tgt = x.target ? ' ★' : '';
      return `<option value="${x.pid}"${x.target ? ' selected' : ''}>` +
        `${x.connected ? '●' : '○'} ${x.project || ('PID ' + x.pid)} · ${x.pid} · ${st}${tgt}</option>`;
    }).join('');
    // 连接按钮文字随选择变化
    const cur = INSTANCES.find(x => String(x.pid) === sel.value) || INSTANCES[0];
    const btn = $('btnConnect');
    if(btn) btn.textContent = cur && cur.connected ? '切换' : '连接';
  }catch(e){ /* 忽略 */ }
}

async function connectSelected(){
  const sel = $('agentSel');
  if(!sel || !sel.value) return;
  const pid = parseInt(sel.value, 10);
  const inst = INSTANCES.find(x => x.pid === pid) || {};
  setBusy(true, inst.connected ? '切换实例' : '连接实例');
  try{
    const r = await api('/api/connect', {method:'POST',
      body: JSON.stringify({pid: pid})});
    if(!r.ok){ log('连接失败：' + (r.error || '')); toast('连接失败'); setBusy(false); return; }
    if(r.connected){
      log(r.message || '已切换');
      toast('已切换到 PID ' + pid);
      await loadAgents();
      await refresh();
    }else{
      log('未连接：' + (r.message || ''));
      log('命令已放入剪贴板：' + (r.command || ''));
      showConnectGuide(pid, r);
      toast('命令已复制，请在 Mechanical 里粘贴');
      // 之后自动轮询，连上会自己变状态
    }
  }catch(e){ log('连接异常：' + e); }
  setBusy(false);
}

function showConnectGuide(pid, r){
  const box = $('connectGuide');
  if(!box) return;
  box.style.display = 'block';
  box.innerHTML = `
    <div style="font-size:12.5px;line-height:1.7">
      <b style="color:#ffd48a">PID ${pid} 还没连接</b>，按下面 3 步（命令已在剪贴板）：
      <div style="margin-top:6px">1. 切到那个 Mechanical 窗口（已帮你激活）</div>
      <div>2. 点顶部 <b>Automation（自动化）</b> → <b>Scripting（脚本）</b></div>
      <div>3. 点脚本框，按 <b>Ctrl+V</b>，再按 <b>Enter</b>（或点绿色 ▶）</div>
      <div class="muted" style="margin-top:6px">命令：<code>${(r.command||'').replace(/</g,'&lt;')}</code></div>
      <div style="margin-top:8px">
        <button class="btn" onclick="copyConnectCmd()" style="padding:3px 10px">再复制一次命令</button>
        <button class="btn" onclick="loadAgents()" style="padding:3px 10px">我粘好了，重试</button>
        <button class="btn" onclick="$('connectGuide').style.display='none'" style="padding:3px 10px">关闭</button>
      </div>
    </div>`;
}

function copyConnectCmd(){
  const cmd = 'execfile("D:/ai/meshkit/app/bridge_agent.py")';
  if(navigator.clipboard){
    navigator.clipboard.writeText(cmd).then(
      () => toast('命令已复制'), () => toast('复制失败，请手动选中'));
  }else{
    toast('请手动复制：' + cmd);
  }
}
'''

src = src[:i] + NEW_JS.strip() + "\n\n" + src[end:]
print("已替换 loadAgents/selectAgent")

# ---------- 3) 绑定按钮 ----------
OLD_BIND = "$('agentSel').onchange = e => selectAgent(parseInt(e.target.value, 10));"
NEW_BIND = """$('agentSel').onchange = () => loadAgents();
  const _bc = $('btnConnect'); if(_bc) _bc.onclick = connectSelected;"""
if OLD_BIND in src:
    src = src.replace(OLD_BIND, NEW_BIND, 1)
    print("已改按钮绑定")
else:
    print("警告：未找到 onchange 绑定")

# ---------- 4) 指引框容器 ----------
OLD_TOAST = '<div class="toast" id="toast"></div>'
NEW_TOAST = '''<div id="connectGuide" style="display:none;position:fixed;right:18px;bottom:60px;
     z-index:98;max-width:520px;background:#1b2431;border:1px solid #3a4a5f;
     border-radius:8px;padding:12px 14px;box-shadow:0 12px 36px rgba(0,0,0,.55)"></div>
<div class="toast" id="toast"></div>'''
if OLD_TOAST in src and 'id="connectGuide"' not in src:
    src = src.replace(OLD_TOAST, NEW_TOAST, 1)
    print("已加指引框")

# ---------- 5) 轮询间隔 5 秒 ----------
src = src.replace("setInterval(loadAgents, 10000);", "setInterval(loadAgents, 5000);")

io.open(P, "w", encoding="utf-8").write(src)
print("完成")
