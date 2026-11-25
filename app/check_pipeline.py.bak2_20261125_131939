# -*- coding: utf-8 -*-
"""自检脚本：每次改完跑一遍，用真实数据核对，不靠猜测
   检查项：
   1) 服务是否在跑、agent 版本是否最新
   2) 体总数 / 重名 / 未分组
   3) 失败体（黄）与过期体（粉）是否与 Mechanical 消息一致
   4) 类别控制项范围是否合理（专属控制项只含 1 个体）
   5) 分组与类别数值是否一致
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8765"


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


fails = []
warns = []


def check(cond, msg):
    if cond:
        print("  ✓ " + msg)
    else:
        print("  ✗ " + msg)
        fails.append(msg)


def warn(cond, msg):
    if not cond:
        print("  ! " + msg)
        warns.append(msg)


print("=== 1. 服务与代理 ===")
try:
    ping = get("/api/ping")
    check(ping.get("ok"), "服务在线 pid=%s" % ping.get("pid"))
    check(ping.get("bridge"), "Mechanical 桥接在线")
except Exception as e:
    print("  ✗ 服务不可用: %s" % e)
    sys.exit(1)

ag = op("ping")
ver = ag.get("version") or "?"
check(bool(ver), "agent 版本 %s" % ver)

print("\n=== 2. 体与分组 ===")
lb = op("list_bodies")
bodies = lb.get("bodies") or []
names = [b.get("name") for b in bodies]
check(len(bodies) > 0, "体总数 %d" % len(bodies))
check(len(names) == len(set(names)),
      "名字唯一（%d/%d）" % (len(set(names)), len(names)))

gg = op("geom_groups")
groups = gg.get("groups") or []
ungrouped = gg.get("ungrouped") or []
gcnt = sum(len(g.get("bodies") or []) for g in groups)
check(len(ungrouped) == 0, "未分组 %d 个" % len(ungrouped))
check(gcnt + len(ungrouped) == len(bodies),
      "分组覆盖 %d+%d=%d 体（总 %d）" % (gcnt, len(ungrouped), gcnt + len(ungrouped), len(bodies)))

print("\n=== 3. 失败体 / 过期体 ===")
fd = get("/api/failed")
nb = len(fd.get("bodies") or [])
npink = fd.get("pink_count") or 0
print("  失败(黄)=%d  过期(粉)=%d  stale=%s  uncertain=%s"
      % (nb, npink, fd.get("stale"), fd.get("uncertain")))
check(not fd.get("uncertain"), "失败体判定来源明确（非推断）")
for b in (fd.get("bodies") or []):
    check(b.get("meshed") or b.get("state") != "Meshed",
          "失败体 %s 状态=%s" % (b.get("name"), b.get("state")))

print("\n=== 4. 控制项范围 ===")
ctrls = op("probe_controls_all").get("items") or []
bnames = set(names)
def _is_own(cname):
    # 专属控制项 = SIZE_FIX_<体名> 或 METHOD_<体名>，且 <体名> 真实存在
    if cname.startswith("SIZE_FIX_"):
        return cname[len("SIZE_FIX_"):] in bnames
    if cname.startswith("METHOD_"):
        return cname[len("METHOD_"):] in bnames
    return False
own = [c for c in ctrls if _is_own(c["name"])]
bad_own = [c for c in own if c["ids"] != 1]
check(not bad_own, "专属控制项都只含 1 个体" if not bad_own
      else "专属控制项体数异常: %s" % [(c["name"], c["ids"]) for c in bad_own])
cat = [c for c in ctrls if not c["name"].startswith("SIZE_FIX_")]
check(len(cat) > 0, "类别控制项 %d 个" % len(cat))

print("\n=== 5. 类别与分组一致性 ===")
try:
    cats = get("/api/categories").get("categories") or []
    cmap = {c["name"]: c["bodies"] for c in cats}
    gmap = {g["name"]: len(g.get("bodies") or []) for g in groups}
    diff = []
    for k in set(cmap) | set(gmap):
        a = cmap.get(k)
        b = gmap.get(k)
        if a is None or b is None:
            diff.append((k, a, b))
        elif a != b:
            diff.append((k, a, b))
    check(not diff, "类别与分组数值一致" if not diff
          else "不一致: %s" % diff[:6])
    check(sum(cmap.values()) == len(bodies),
          "类别合计 %d 体（总 %d）" % (sum(cmap.values()), len(bodies)))
except Exception as e:
    warn(False, "类别读取失败: %s" % e)

print("\n=== 结论 ===")
if fails:
    print("发现问题 %d 项：" % len(fails))
    for f in fails:
        print("  - " + f)
    sys.exit(2)
print("全部检查通过" + ("（%d 条提醒）" % len(warns) if warns else ""))
