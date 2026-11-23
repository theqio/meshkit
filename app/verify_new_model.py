# -*- coding: utf-8 -*-
"""新模型体检：一次把会导致"分组/尺寸/网格"出错的隐患都查出来
用法：
    python app/verify_new_model.py
建议在「① 扫描模型」之后跑一次；有问题按提示处理，没问题再往下走。
"""
import json
import urllib.request
from collections import Counter

BASE = "http://127.0.0.1:8765"
issues, warns, oks = [], [], []


def get(p, timeout=180):
    with urllib.request.urlopen(BASE + p, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post(p, payload=None, timeout=600):
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + p, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def op(name, payload=None):
    return post("/api/op", {"op": name, "payload": payload or {}})


def ok(msg):
    oks.append(msg)
    print("  ✓ " + msg)


def bad(msg):
    issues.append(msg)
    print("  ✗ " + msg)


def warn(msg):
    warns.append(msg)
    print("  ! " + msg)


print("=== 1. 连接 ===")
try:
    ping = get("/api/ping")
    if ping.get("bridge"):
        ok("服务与 Mechanical 桥接都在线")
    else:
        bad("桥接未连接：去 Mechanical 点 MeshBox_Check（MeshKit_Check）")
except Exception as e:
    bad("服务不可用：%s" % e)
    raise SystemExit(1)

ag = op("ping")
ok("代理版本 %s" % ag.get("version"))

print("\n=== 2. 体与单位 ===")
lb = op("list_bodies")
bodies = lb.get("bodies") or []
if not bodies:
    bad("读不到体：确认 Mechanical 里已打开 Model")
    raise SystemExit(1)
ok("体数量 %d" % len(bodies))

dims = []
for b in bodies:
    d = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
    dims.append(d[2])
mx = max(dims) if dims else 0
if mx < 1:
    bad("最大尺寸 %.4f —— 单位不是 mm（应为 mm），点一次 MeshKit_Check 让代理自动切单位" % mx)
elif mx > 1e6:
    bad("最大尺寸 %.0f —— 单位可能是微米或异常" % mx)
else:
    ok("单位看起来是 mm（最大尺寸 %.1f）" % mx)

print("\n=== 3. 重名体（会导致分组吞体）===")
names = [b.get("name") for b in bodies]
dup = [(n, c) for n, c in Counter(names).items() if c > 1]
if dup:
    bad("有 %d 组重名体，共 %d 个体（识别会按名字合并，导致数量变少）"
        % (len(dup), sum(c for _, c in dup)))
    for n, c in dup[:8]:
        print("       %s ×%d" % (n, c))
    print("     → 修复：POST /api/op  {\"op\":\"dedupe_names\",\"payload\":{}}")
else:
    ok("体名唯一（%d 个）" % len(set(names)))

print("\n=== 4. 抑制体 ===")
sup = [b for b in bodies if b.get("suppressed")]
if sup:
    warn("有 %d 个体被抑制（不参与网格，但仍在分组里）" % len(sup))
    for b in sup[:5]:
        print("       %s" % b.get("name"))
else:
    ok("没有被抑制的体")

print("\n=== 5. 已有分组 / 控制项（新模型应为 0）===")
gg = op("geom_groups")
gcnt = len(gg.get("groups") or [])
un = len(gg.get("ungrouped") or [])
if gcnt:
    warn("已有 %d 个 Geometry 分组（%d 体未分组）——若是旧模型残留，先点「③ 取消全部分组」" % (gcnt, un))
else:
    ok("没有旧分组")
ctrls = op("probe_controls_all").get("items") or []
if ctrls:
    warn("已有 %d 个 Mesh 控制项（新模型应为 0）——旧残留可点「一键清理全部配置」" % len(ctrls))
else:
    ok("没有旧控制项")

print("\n=== 6. 识别覆盖 ===")
sc = get("/api/auto_scan")
groups = sc.get("groups") or {}
total = sum(len(v) for v in groups.values())
if total == len(bodies):
    ok("识别覆盖 %d 体，无遗漏（%d 组）" % (total, len(groups)))
else:
    bad("识别只覆盖 %d / %d 体，差 %d 个" % (total, len(bodies), len(bodies) - total))

empty = [k for k, v in groups.items() if not v]
if empty:
    warn("有 %d 个空分组：%s" % (len(empty), "、".join(empty[:6])))

print("\n=== 7. 部位分布（前 12）===")
for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:12]:
    print("   %-16s %3d 体" % (k, len(v)))
rare = [k for k, v in groups.items() if len(v) <= 2]
if rare:
    print("   少见分组：%s" % "、".join(rare[:8]))

print("\n=== 结论 ===")
if issues:
    print("发现 %d 个必须先解决的问题：" % len(issues))
    for m in issues:
        print("  - " + m)
elif warns:
    print("可以继续，但有 %d 条提醒（见上）" % len(warns))
else:
    print("全部正常，可以开始 ② 落地分组。")
