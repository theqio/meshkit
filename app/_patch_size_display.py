# -*- coding: utf-8 -*-
"""尺寸数字显示不全的修复：
   - 输入框加宽 52→78px，右对齐、等宽数字
   - 尺寸/推荐值统一保留最多 1 位小数
   - 推荐列给最小宽度，避免被挤压
"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\static\index.html"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

# 1) 数值格式化
OLD = """    const sizeVal = (c.size_mm === null || c.size_mm === undefined) ? '' : c.size_mm;
    const recVal = (c.size_recommended === undefined || c.size_recommended === null) ? '' : c.size_recommended;"""
NEW = """    const fmtSize = (v) => {
      if(v === null || v === undefined || v === '') return '';
      const n = Number(v);
      if(!isFinite(n)) return String(v);
      // 最多 1 位小数，去掉多余的 .0
      return (Math.round(n * 10) / 10).toString();
    };
    const sizeVal = fmtSize(c.size_mm);
    const recVal = fmtSize(c.size_recommended);"""
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("已加数值格式化")
else:
    print("未找到 sizeVal")

# 2) 输入框加宽 + 右对齐
OLD2 = '''      <td class="num"><input type="number" class="size-input" data-cat="${c.name}" ${disAttr}
         step="0.5" min="2" max="60" value="${c.size_is_default ? '' : sizeVal}"
         placeholder="${c.size_is_default ? '未设' : recVal}"
         title="${c.size_is_default ? '该控制项还没设尺寸，会跟随全局；填数字点应用即写入' : (c.size_basis||'').replace(/"/g,'')}"
         style="width:52px"></td>'''
NEW2 = '''      <td class="num"><input type="number" class="size-input" data-cat="${c.name}" ${disAttr}
         step="0.5" min="1" max="200" value="${c.size_is_default ? '' : sizeVal}"
         placeholder="${c.size_is_default ? '未设' : recVal}"
         title="${c.size_is_default ? '该控制项还没设尺寸，会跟随全局；填数字点应用即写入' : (c.size_basis||'').replace(/"/g,'')}"
         style="width:78px;text-align:right;font-variant-numeric:tabular-nums"></td>'''
if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("输入框已加宽到 78px")
else:
    print("未找到输入框模板")

# 3) 推荐列最小宽度
OLD3 = '''      <td class="num" style="color:${recVal?'#9fd2ff':'var(--dim)'};font-size:12.5px"
          title="${(c.size_basis||'').replace(/"/g,'')}">${recVal || '—'}</td>'''
NEW3 = '''      <td class="num" style="color:${recVal?'#9fd2ff':'var(--dim)'};font-size:12.5px;
          min-width:64px;white-space:nowrap"
          title="${(c.size_basis||'').replace(/"/g,'')}">${recVal || '—'}</td>'''
if OLD3 in src:
    src = src.replace(OLD3, NEW3, 1)
    print("推荐列已加最小宽度")
else:
    print("未找到推荐列")

io.open(P, "w", encoding="utf-8").write(src)
print("完成")
