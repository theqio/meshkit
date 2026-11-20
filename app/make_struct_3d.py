# -*- coding: utf-8 -*-
"""生成 3D 结构编号图（自包含 HTML + Canvas，可拖拽旋转）
   - 每个体按其分组编号上色并标注序号
   - 输出 reports/结构3D.html
"""
import json
import urllib.request


def post(p, payload=None, timeout=600):
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + p, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


lb = post("/api/op", {"op": "list_bodies", "payload": {}})
bs = [b for b in (lb.get("bodies") or []) if not b.get("suppressed")]


def is_wave(n):
    return ("波纹" in n) or ("WAVE" in n.upper())


def is_bottom(b, n):
    d = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
    return (d[0] < 60 and d[1] > 400 and d[2] > 400) or ("底板" in n)


ys = sorted([b.get("cy") or 0 for b in bs])
y0, y1 = ys[0], ys[-1]
span = max(1.0, y1 - y0)
pos = sum(1 for b in bs if (b.get("cx") or 0) > 0)
inner_pos = pos >= (len(bs) - pos)

# 先算每体的组名
for b in bs:
    n = (b.get("name") or "").strip()
    _cyv = b.get("cy") or 0
    layer = "下" if _cyv < 3748 else ("上" if _cyv > 6664 else "中")
    side = "内" if ((b.get("cx") or 0) > 0) == inner_pos else "外"
    elem = "壳" if b.get("shell") else "实体"
    if layer == "上":
        role = "上层整体"
    elif is_wave(n):
        role = "波纹板"
    elif layer == "下" and is_bottom(b, n):
        role = "大平面底板"
    else:
        role = "其余结构"
    dd = sorted([round(b.get("lx") or 0), round(b.get("ly") or 0),
                 round(b.get("lz") or 0)])
    sig = "%dx%dx%d" % (dd[0], dd[1], dd[2])
    b["_key"] = "%s_%s侧_%s层_%s_%s" % (elem, side, layer, role, sig)

counts = {}
for b in bs:
    counts[b["_key"]] = counts.get(b["_key"], 0) + 1
order = sorted(counts.items(), key=lambda kv: -kv[1])
num = {}
for i, (k, c) in enumerate(order, 1):
    num[k] = i

# 归一化坐标到 [-0.5, 0.5]
xs = [b.get("cx") or 0 for b in bs]
zs = [b.get("cz") or 0 for b in bs]
cx0, cx1 = min(xs), max(xs)
cz0, cz1 = min(zs), max(zs)
dx = max(1e-6, cx1 - cx0)
dz = max(1e-6, cz1 - cz0)
dy = max(1e-6, y1 - y0)

pts = []
for b in bs:
    pts.append({
        "x": ((b.get("cx") or 0) - (cx0 + cx1) / 2) / dx,
        "y": ((b.get("cy") or 0) - (y0 + y1) / 2) / dy,
        "z": ((b.get("cz") or 0) - (cz0 + cz1) / 2) / dz,
        "n": num[b["_key"]],
        "g": b["_key"],
        "name": b.get("name"),
    })

legend = []
for _k, _c in order:
    _items = [x for x in pts if x["g"] == _k]
    if not _items:
        continue
    legend.append({"no": num[_k], "key": _k, "count": _c,
                   "cx": round(sum(p["x"] for p in _items) / len(_items), 3),
                   "cy": round(sum(p["y"] for p in _items) / len(_items), 3),
                   "cz": round(sum(p["z"] for p in _items) / len(_items), 3)})

# 容器包围盒（用于画参考框）
_bb = {"x": [round(min(p["x"] for p in pts), 3), round(max(p["x"] for p in pts), 3)],
       "y": [round(min(p["y"] for p in pts), 3), round(max(p["y"] for p in pts), 3)],
       "z": [round(min(p["z"] for p in pts), 3), round(max(p["z"] for p in pts), 3)]}
data = json.dumps({"pts": pts, "legend": legend, "bb": _bb}, ensure_ascii=False)

html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>结构 3D 编号图</title><style>
body{margin:0;background:#0b0f14;color:#e6edf3;font:13px/1.5 "Microsoft YaHei",sans-serif}
#wrap{display:flex;height:100vh}
#cv{flex:1;cursor:grab}
#side{width:420px;overflow:auto;border-left:1px solid #27313f;padding:10px 12px;
      background:#111720}
h1{font-size:15px;margin:0 0 6px}
.sub{color:#8b98a8;font-size:12px;margin-bottom:8px}
.row{display:flex;align-items:center;gap:7px;padding:3px 0;border-bottom:1px solid #1b2430}
.no{min-width:26px;text-align:center;font-weight:700;font-size:12px}
.dim{color:#8b98a8;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cnt{margin-left:auto;color:#9fd2ff;font-size:11px}
</style></head><body>
<div id="wrap"><canvas id="cv"></canvas>
<div id="side"><h1>结构 3D 编号图</h1>
<div class="sub">拖拽旋转 · 滚轮缩放 · 颜色=分组，编号同右侧列表</div>
<div id="list"></div></div></div>
<script>
const D = __DATA__;
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
let ax = -0.55, ay = 0.62, zoom = 1.6, drag = false, lx = 0, ly = 0;

function resize(){ cv.width = cv.clientWidth; cv.height = cv.clientHeight; draw(); }
window.addEventListener('resize', resize);

function proj(p){
  const x = p.x, y = p.y, z = p.z;
  const cy1 = Math.cos(ax), sy1 = Math.sin(ax);
  const cy2 = Math.cos(ay), sy2 = Math.sin(ay);
  let X = x * cy2 - z * sy2;
  let Z = x * sy2 + z * cy2;
  let Y = y * cy1 - Z * sy1;
  let Z2 = y * sy1 + Z * cy1;
  const f = 1500 * zoom / (2.2 + Z2);
  return [cv.width/2 + X * f, cv.height/2 - Y * f, Z2];
}
function hue(n){ return (n * 47) % 360; }

function line3(a, b, color, w){
  const p1 = proj(a), p2 = proj(b);
  ctx.strokeStyle = color; ctx.lineWidth = w || 1;
  ctx.beginPath(); ctx.moveTo(p1[0], p1[1]); ctx.lineTo(p2[0], p2[1]); ctx.stroke();
}

function draw(){
  ctx.clearRect(0,0,cv.width,cv.height);
  const bb = D.bb;

  // 容器参考框
  const c = '#243040';
  const X = bb.x, Y = bb.y, Z = bb.z;
  const corners = [];
  for(const x of X) for(const y of Y) for(const z of Z) corners.push({x,y,z});
  const edges = [[0,1],[0,2],[0,4],[1,3],[1,5],[2,3],[2,6],[3,7],[4,5],[4,6],[5,7],[6,7]];
  for(const [i,j] of edges) line3(corners[i], corners[j], c, 1);

  // 坐标轴
  const O = {x: X[0], y: Y[0], z: Z[0]};
  line3(O, {x: X[1], y: Y[0], z: Z[0]}, '#e05a5a', 2);   // X 红
  line3(O, {x: X[0], y: Y[1], z: Z[0]}, '#5ae08a', 2);   // Y 绿
  line3(O, {x: X[0], y: Y[0], z: Z[1]}, '#5a9ce0', 2);   // Z 蓝
  ctx.font = 'bold 13px Consolas,monospace';
  const ax1 = proj({x: X[1], y: Y[0], z: Z[0]}); ctx.fillStyle = '#e05a5a';
  ctx.fillText('X', ax1[0] + 6, ax1[1]);
  const ay1 = proj({x: X[0], y: Y[1], z: Z[0]}); ctx.fillStyle = '#5ae08a';
  ctx.fillText('Y(高度)', ay1[0] + 6, ay1[1]);
  const az1 = proj({x: X[0], y: Y[0], z: Z[1]}); ctx.fillStyle = '#5a9ce0';
  ctx.fillText('Z', az1[0] + 6, az1[1]);

  // 体（点）
  const pts = D.pts.map(p => [p, proj(p)]);
  pts.sort((a,b) => a[1][2] - b[1][2]);
  for(const [p, q] of pts){
    ctx.beginPath();
    ctx.fillStyle = `hsl(${hue(p.n)} 72% 62%)`;
    ctx.arc(q[0], q[1], 3.2 * zoom, 0, 6.2832);
    ctx.fill();
  }

  // 每个分组：在质心处标序号 + 引到第一个体
  ctx.font = 'bold 12px Consolas,monospace';
  for(const g of D.legend){
    const q = proj({x: g.cx, y: g.cy, z: g.cz});
    const first = pts.find(([p]) => p.n === g.no);
    if(first){
      ctx.strokeStyle = `hsla(${hue(g.no)} 70% 62% / .5)`;
      ctx.lineWidth = 1; ctx.setLineDash([3,3]);
      ctx.beginPath(); ctx.moveTo(first[1][0], first[1][1]); ctx.lineTo(q[0], q[1]); ctx.stroke();
      ctx.setLineDash([]);
    }
    const label = String(g.no);
    ctx.lineWidth = 3.5; ctx.strokeStyle = 'rgba(0,0,0,.85)';
    ctx.strokeText(label, q[0] + 6, q[1] - 6);
    ctx.fillStyle = `hsl(${hue(g.no)} 80% 72%)`;
    ctx.fillText(label, q[0] + 6, q[1] - 6);
  }
}

cv.addEventListener('mousedown', e => { drag = true; lx = e.clientX; ly = e.clientY;
  cv.style.cursor = 'grabbing'; });
window.addEventListener('mouseup', () => { drag = false; cv.style.cursor = 'grab'; });
window.addEventListener('mousemove', e => {
  if(!drag) return;
  ay += (e.clientX - lx) * 0.008;
  ax += (e.clientY - ly) * 0.008;
  ax = Math.max(-1.5, Math.min(1.5, ax));
  lx = e.clientX; ly = e.clientY; draw();
});
cv.addEventListener('wheel', e => { e.preventDefault();
  zoom *= (e.deltaY > 0 ? 0.92 : 1.08);
  zoom = Math.max(0.3, Math.min(4, zoom)); draw(); }, {passive:false});

const list = document.getElementById('list');
list.innerHTML = D.legend.map(x => {
  const parts = x.key.split('_');
  const dim = parts[parts.length - 1];
  return `<div class="row"><span class="no" style="color:hsl(${hue(x.no)} 70% 62%)">${x.no}</span>
    <span class="dim" title="${x.key}">${x.key.replace('_' + dim, '')}</span>
    <span class="cnt">${x.count}</span></div>`;
}).join('');

resize();
</script></body></html>"""

html = html.replace("__DATA__", data)
out = r"D:\ai\meshkit\reports\结构3D.html"
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("已生成", out, "体数", len(pts), "组数", len(legend))
