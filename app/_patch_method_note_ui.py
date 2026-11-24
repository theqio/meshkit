# -*- coding: utf-8 -*-
"""类别表：方法单元格显示"扫掠（手动）"这类说明"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\static\index.html"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

OLD = """      <td><select class="method-sel" data-cat="${c.name}" ${disAttr}
          onchange="onMethodChange('${c.name}', this.value)"
         style="background:var(--panel2);border:1px solid var(--line);color:var(--fg);
                border-radius:6px;padding:4px 6px;font-size:12.5px">${sel}</select></td>"""

NEW = """      <td><select class="method-sel" data-cat="${c.name}" ${disAttr}
          onchange="onMethodChange('${c.name}', this.value)"
         title="${(c.method_note||'').replace(/"/g,'')}"
         style="background:var(--panel2);border:1px solid var(--line);color:var(--fg);
                border-radius:6px;padding:4px 6px;font-size:12.5px">${sel}</select>${c.method_note
          ? '<span class="badge" style="margin-left:4px;font-size:10.5px" title="' +
            c.method_note.replace(/"/g,'') + '">手动</span>' : ''}</td>"""

if OLD in src:
    src = src.replace(OLD, NEW, 1)
    io.open(P, "w", encoding="utf-8").write(src)
    print("方法列已支持说明标记")
else:
    print("未找到方法列")
