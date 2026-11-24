# -*- coding: utf-8 -*-
"""生成网格前先预估，超预算时弹确认"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\static\index.html"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

OLD = """async function generateMesh(){
  if(!confirm('将清空现有网格并重新生成，可能需要 3~5 分钟。继续？')) return;
  setBusy(true, '生成网格（可能需要几分钟，请勿操作 Mechanical）');"""

NEW = """async function generateMesh(){
  // 先预估规模，防止节点数过大把 Mechanical 跑崩
  let pre = null;
  try{
    pre = await api('/api/premesh', {_timeoutMs: 30000});
  }catch(e){ pre = null; }
  if(pre && pre.ok){
    const line = `预计 ${fmt(pre.est_elements)} 单元 / ${fmt(pre.est_nodes)} 节点` +
                 `（预算 ${fmt(pre.budget_nodes)} 节点）`;
    log('网格预估：' + line);
    if(pre.over){
      const top = (pre.top || []).slice(0, 5)
        .map(t => `  ${t.category}: ${fmt(t.est_elements)} 单元（尺寸 ${t.size}）`)
        .join('\\n');
      if(!confirm(`规模超出预算 ${pre.ratio} 倍，可能因为内存不足崩溃。\\n\\n${line}\\n\\n` +
                  `最耗的类别：\\n${top}\\n\\n仍然继续？`)) {
        log('已按预估结果取消（可先调大尺寸或降低薄板层数）');
        return;
      }
    }
  }
  if(!confirm('将清空现有网格并重新生成，可能需要 3~5 分钟。继续？')) return;
  setBusy(true, '生成网格（可能需要几分钟，请勿操作 Mechanical）');"""

if OLD in src:
    src = src.replace(OLD, NEW, 1)
    io.open(P, "w", encoding="utf-8").write(src)
    print("generateMesh 已加预估确认")
else:
    print("未找到 generateMesh")
