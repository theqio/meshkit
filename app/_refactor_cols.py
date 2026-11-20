# -*- coding: utf-8 -*-
"""布局重构：main 下包三个列容器（colL/colC/colR），并把
   常用配置/模型记忆/经验库/大模型设置 移到右栏。"""
import io
import re

P = r"D:\ai\meshkit\app\static\index.html"
src = io.open(P, encoding="utf-8").read()

# ---------- 1) CSS：列容器 ----------
if ".col{" not in src:
    src = src.replace(
        "  main{display:grid;grid-template-columns:470px minmax(0,1fr) 360px;gap:14px;padding:14px;align-items:start}",
        "  main{display:grid;grid-template-columns:470px minmax(0,1fr) 420px;gap:14px;padding:14px;align-items:start}\n"
        "  .col{display:flex;flex-direction:column;gap:14px;min-width:0}", 1)
    print("CSS 已加列容器")

# ---------- 2) 取出 main 内容 ----------
m = re.search(r"<main>(.*?)</main>", src, re.S)
if not m:
    raise SystemExit("找不到 main")
inner = m.group(1)

# ---------- 3) 切出所有顶层 section ----------
secs = []
pos = 0
while True:
    s = inner.find('<section class="card"', pos)
    if s < 0:
        break
    e = inner.find("</section>", s)
    if e < 0:
        break
    secs.append(inner[s:e + len("</section>")])
    pos = e + len("</section>")

if len(secs) < 3:
    raise SystemExit("顶层卡片不足 3 个：%d" % len(secs))

def pick(key):
    for x in secs:
        if key in x:
            return x
    return None

settings = pick("网格设置")
center = pick("模型状态")
ai = pick("AI 诊断")
if not (settings and center and ai):
    raise SystemExit("分类失败：settings=%s center=%s ai=%s"
                     % (bool(settings), bool(center), bool(ai)))

# ---------- 4) 把左卡片尾部（常用配置…大模型设置）切出去 ----------
mark = '<h2 style="margin-top:18px">常用配置</h2>'
i = settings.find(mark)
if i < 0:
    raise SystemExit("找不到常用配置起点")
tail = settings[i:-len("</section>")]          # 去掉结尾 </section>
settings = settings[:i].rstrip() + "\n  </section>"

# 放进 AI 卡片末尾
ai_body = ai[:-len("</section>")].rstrip()
ai = ai_body + "\n\n" + tail.rstrip() + "\n  </section>"

# ---------- 5) 重排 ----------
new_main = ("<main>\n"
            "  <div class=\"col colL\">\n" + settings + "\n  </div>\n"
            "  <div class=\"col colC\">\n" + center + "\n  </div>\n"
            "  <div class=\"col colR\">\n" + ai + "\n  </div>\n"
            "</main>")
src = src[:m.start()] + new_main + src[m.end():]

io.open(P, "w", encoding="utf-8").write(src)
print("三列容器已建立，块已移入右栏；卡片数=%d" % len(secs))
