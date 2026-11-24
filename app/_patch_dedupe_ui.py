# -*- coding: utf-8 -*-
"""1) 加「清理重复控制项」按钮
   2) 长操作入口加 BUSY 守卫，防止重复点击造成重复控制项
"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\static\index.html"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

# 1) 按钮
OLD = '''    <button class="btn wide" id="btnClearAll"'''
NEW = '''    <button class="btn" id="btnDedupeCtl" style="margin-bottom:8px"
            title="删掉重复的尺寸/方法控制项，每类只保留一个">清理重复控制项</button>
    <button class="btn wide" id="btnClearAll"'''
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("已加清理重复按钮")
else:
    print("未找到 btnClearAll")

# 2) JS 函数
FUNC = '''
async function dedupeControls(){
  if(BUSY){ toast('正在忙，稍后再试'); return; }
  if(!confirm('将删除重复的尺寸/方法控制项（同名同类型只保留一个）。继续？')) return;
  setBusy(true, '清理重复控制项');
  try{
    const r = await api('/api/controls_dedupe', {_timeoutMs: 600000, method:'POST', body:'{}'});
    if(r.ok){
      const n = r.removed_count || 0;
      log(n ? `已删除 ${n} 个重复控制项：${(r.removed||[]).slice(0,8).join('、')}`
            : '没有发现重复控制项');
      toast(n ? `已清理 ${n} 个` : '无重复');
      await loadCategories();
    }else{
      log('清理失败：' + (r.error || '代理不支持该命令，请先重载代理'));
      toast('清理失败');
    }
  }catch(e){ log('清理异常：'+e); }
  setBusy(false);
}

'''
if "async function dedupeControls(" not in src:
    anchor = "async function fixFailed(){"
    src = src.replace(anchor, FUNC + anchor, 1)
    print("已加 dedupeControls()")

# 3) 绑定
OLD2 = "  $('btnFavSave').onclick = saveFavorite;"
NEW2 = ("  $('btnFavSave').onclick = saveFavorite;\n"
        "  const _ddBtn = $('btnDedupeCtl'); if(_ddBtn) _ddBtn.onclick = dedupeControls;")
if OLD2 in src and "_ddBtn" not in src:
    src = src.replace(OLD2, NEW2, 1)
    print("已绑定按钮")

# 4) BUSY 守卫
GUARDS = [
    ("async function applyAll(){\n  setBusy(true, '应用设置与尺寸（可能要 1~3 分钟）');",
     "async function applyAll(){\n  if(BUSY){ toast('正在忙，请等当前操作结束'); return; }\n  setBusy(true, '应用设置与尺寸（可能要 1~3 分钟）');"),
    ("async function generateMesh(){\n  // 先预估规模",
     "async function generateMesh(){\n  if(BUSY){ toast('正在忙，请等当前操作结束'); return; }\n  // 先预估规模"),
    ("async function fixFailed(){\n  if(!confirm(",
     "async function fixFailed(){\n  if(BUSY){ toast('正在忙，请等当前操作结束'); return; }\n  if(!confirm("),
]
for old, new in GUARDS:
    if old in src and new not in src:
        src = src.replace(old, new, 1)
        print("已加守卫:", old.split("(")[0].replace("async function ",""))

io.open(P, "w", encoding="utf-8").write(src)
print("完成")
