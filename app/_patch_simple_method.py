# -*- coding: utf-8 -*-
"""方法规则简化：标了薄板 = 扫掠；没标 = 自动
   - 去掉 sweep_ok 对方法的强制
   - 去掉"手动"标记与说明
"""
import io
import shutil
import time

# --- 服务端 ---
S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

OLD = '''        method = ci.get("method") or md.get(g) or (
            "Sweep" if (g in thin or g in sw or _auto) else "Automatic")
        # 薄板类别一律用扫掠（标签与方法保持一致）
        if (g in thin) or _auto:
            method = "Sweep"'''
NEW = '''        # 规则只有两条：标了薄板 = 扫掠；没标 = 自动
        _is_thin_now = (g in thin) or _auto
        if _is_thin_now:
            method = "Sweep"
        else:
            method = md.get(g) or "Automatic"
            if method == "Sweep" and g not in sw and g not in thin:
                method = "Automatic"'''
if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("方法规则已简化")
else:
    print("未找到 method 段")

OLD2 = '''            "method_note": ("扫掠（手动标记扫掠可用）"
                            if (method == "Sweep" and not ((g in thin) or _auto))
                            else ""),
'''
if OLD2 in s:
    s = s.replace(OLD2, "", 1)
    print("已去掉 method_note")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("server 语法 OK")

# --- 页面：去掉"手动"徽标 ---
P = r"D:\ai\meshkit\app\static\index.html"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

OLD3 = '''         title="${(c.method_note||'').replace(/"/g,'')}"
         style="background:var(--panel2);border:1px solid var(--line);color:var(--fg);
                border-radius:6px;padding:4px 6px;font-size:12.5px">${sel}</select>${c.method_note
          ? '<span class="badge" style="margin-left:4px;font-size:10.5px" title="' +
            c.method_note.replace(/"/g,'') + '">手动</span>' : ''}</td>'''
NEW3 = '''         style="background:var(--panel2);border:1px solid var(--line);color:var(--fg);
                border-radius:6px;padding:4px 6px;font-size:12.5px">${sel}</select></td>'''
if OLD3 in src:
    src = src.replace(OLD3, NEW3, 1)
    io.open(P, "w", encoding="utf-8").write(src)
    print("页面已去掉手动徽标")
else:
    print("未找到页面方法列")
