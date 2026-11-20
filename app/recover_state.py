# -*- coding: utf-8 -*-
"""
recover_state.py —— 恢复干净状态（Python 3 客户端）

用途：当"自动修复"误判、批量建了 SIZE_FIX_ 控制项之后，一键恢复：

  1) 删除全部 SIZE_FIX_* 控制项
  2) 删除类别已不存在的孤儿控制项
  3) 按基础类别恢复手调尺寸（23 个类别）
  4) 重新生成网格
  5) 打印结果与状态灯

用法：
    python recover_state.py
（需先启动 server.py 与 Mechanical 内的 bridge_agent.py）
"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8765"

# 基础类别 -> 手调尺寸（mm）
BASE_SIZES = {
    "垫板": 40, "横梁": 35, "纵梁": 35, "板": 25, "立柱": 25, "侧封板": 25,
    "波纹板": 30, "隔板": 30, "顶部支撑梁": 30, "薄垫板": 20, "薄板": 20,
    "百叶窗框": 35, "电缆口框": 35, "侧板固定件": 30, "结构件": 30, "块体": 40,
    "中间隔断板": 20,
}


def get(path, timeout=180):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path, payload, timeout=3000):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    print("== 1. 检查连接 ==")
    p = get("/api/ping")
    print("   bridge:", p.get("bridge"))
    if not p.get("bridge"):
        print("   桥接未运行：请在 Mechanical 里点 MeshKit_Check")
        return

    print("\n== 2. 删除 SIZE_FIX_* ==")
    r = post("/api/op", {"op": "delete_fix_controls", "payload": {}})
    print("   删除:", r.get("deleted_count"), "个")

    print("\n== 3. 清理孤儿控制项 ==")
    r = post("/api/op", {"op": "prune_controls", "payload": {}})
    print("   删除:", r.get("deleted"))
    print("   保留:", r.get("kept"), "个")

    print("\n== 4. 按基础类别恢复尺寸 ==")
    d = get("/api/categories")
    sizes = {}
    for c in d.get("categories") or []:
        base = c.get("base") or c["name"]
        sizes[c["name"]] = BASE_SIZES.get(base, 30)
    r = post("/api/op", {"op": "apply", "payload": {"sizes": sizes,
                                                    "methods": {k: "Automatic"
                                                                for k in sizes}}})
    print("   套用:", len(r.get("sizes") or []), "个；新建:", len(r.get("created") or []))
    if r.get("errors"):
        print("   错误:", r["errors"][:3])

    print("\n== 5. 重新生成网格 ==")
    t0 = time.time()
    r = post("/api/op", {"op": "mesh", "payload": {"timeout": 1800}})
    print("   用时 %.1f 分钟" % ((time.time() - t0) / 60.0))
    print("   单元: %s  节点: %s" % (r.get("elements"), r.get("nodes")))
    print("   失败体:", len(r.get("failed_bodies") or []), " 状态:", r.get("mesh_state"))

    print("\n== 6. 状态灯 ==")
    d = get("/api/categories")
    print("   ", d.get("summary_lights"))
    for c in (d.get("categories") or [])[:30]:
        print("    %-12s %-8s %-6s %s" % (c["name"], c.get("size_mm"),
                                          c.get("light"), c.get("light_reason")))


if __name__ == "__main__":
    main()
