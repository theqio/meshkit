# -*- coding: utf-8 -*-
"""生成结构编号图：把当前分组按序号列出，供用户标注
   输出 reports/结构编号.html + PNG
"""
import json
import urllib.request

def get(p, timeout=600):
    with urllib.request.urlopen("http://127.0.0.1:8765" + p, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

zones = get("/api/zones")
groups = zones.get("groups") or []

rows = []
for i, g in enumerate(groups, 1):
    name = g.get("group") or ""
    parts = name.split("_")
    dims = parts[-1] if parts else ""
    rows.append({
        "no": i,
        "group": name,
        "count": g.get("count"),
        "dims": dims,
        "samples": "、".join((g.get("samples") or [])[:2]),
    })

html = ["""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>结构编号表</title><style>
body{background:#0f1319;color:#e6edf3;font:13px/1.6 "Microsoft YaHei",sans-serif;padding:20px}
h1{font-size:18px;margin:0 0 4px}
.sub{color:#8b98a8;font-size:12px;margin-bottom:14px}
table{border-collapse:collapse;width:100%}
th,td{border:1px solid #27313f;padding:5px 8px;text-align:left;font-size:12.5px}
th{background:#1c2430;color:#8b98a8;position:sticky;top:0}
td.n{color:#7ee2a8;font-weight:700;width:44px;text-align:center}
td.c{text-align:right;width:60px;color:#9fd2ff}
tr:nth-child(even){background:#151b24}
</style></head><body>
<h1>结构编号表</h1>
<div class="sub">共 __N__ 组 / __T__ 体　·　按体数降序　·　请告诉我序号对应哪个部位</div>
<table><tr><th>序号</th><th>分组（单元_内外_层_角色_尺寸）</th><th>体数</th>
<th>尺寸(mm)</th><th>示例体名</th></tr>"""]

for r in rows:
    html.append("<tr><td class='n'>%d</td><td>%s</td><td class='c'>%d</td>"
                "<td>%s</td><td style='color:#8b98a8'>%s</td></tr>"
                % (r["no"], r["group"], r["count"], r["dims"], r["samples"]))
html.append("</table></body></html>")
html[-1] = html[-1]

html[0] = html[0].replace("__N__", str(len(rows))).replace("__T__", str(zones.get("total") or 0))
out = r"D:\ai\meshkit\reports\结构编号.html"
with open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(html))
print("已生成", out, len(rows), "组")

# 同时输出纯文本清单，方便直接引用
txt = "\n".join("%2d. %-46s %3d 体  %s" % (r["no"], r["group"], r["count"], r["dims"])
                for r in rows)
with open(r"D:\ai\meshkit\reports\结构编号.txt", "w", encoding="utf-8") as f:
    f.write(txt)
print(txt[:1200])
