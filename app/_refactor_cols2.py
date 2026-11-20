# -*- coding: utf-8 -*-
"""布局重构 v2：main 子元素实际是 [设置卡片][AI卡片][中列容器]"""
import io
import re

P = r"D:\ai\meshkit\app\static\index.html"
src = io.open(P, encoding="utf-8").read()

m = re.search(r"<main>(.*?)</main>", src, re.S)
inner = m.group(1)

# 取前两个 class="card" 顶层 section
cards = []
pos = 0
while len(cards) < 2:
    s = inner.find('<section class="card"', pos)
    if s < 0:
        break
    e = inner.find("</section>", s)
    cards.append((s, e + len("</section>")))
    pos = e + len("</section>")
if len(cards) < 2:
    raise SystemExit("卡片不足: %d" % len(cards))

settings = inner[cards[0][0]:cards[0][1]]
ai = inner[cards[1][0]:cards[1][1]]
# 中列 = 其余内容（去掉两张卡片）
rest = inner[:cards[0][0]] + inner[cards[0][1]:cards[1][0]] + inner[cards[1][1]:]
center = rest.strip()
if not center:
    raise SystemExit("中列为空")

# 生成新的三列
def wrap(cls, html):
    return '  <div class="col %s">\n%s\n  </div>' % (cls, html.rstrip())

new_main = ("<main>\n"
            + wrap("colL", settings) + "\n"
            + wrap("colC", center) + "\n"
            + wrap("colR", ai) + "\n"
            + "</main>")

# 把左卡片尾部（常用配置…大模型设置）移到 AI 卡片里
mark = '<h2 style="margin-top:18px">常用配置</h2>'
i = new_main.find(mark)
if i > 0:
    # 左卡片尾 = mark 到左卡片 </section>
    left_close = new_main.find("</section>", i)
    tail = new_main[i:left_close]
    new_main = new_main[:i] + new_main[left_close:]
    # 插到 AI 卡片末尾（最后一个 </section> 之前）
    last_close = new_main.rfind("</section>")
    new_main = new_main[:last_close] + tail.rstrip() + "\n  " + new_main[last_close:]
    print("块已移入右栏")
else:
    print("未找到常用配置块")

src = src[:m.start()] + new_main + src[m.end():]
io.open(P, "w", encoding="utf-8").write(src)
print("重构完成")
