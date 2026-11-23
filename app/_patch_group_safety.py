# -*- coding: utf-8 -*-
"""② 落地安全化：解散前快照、重建后校验、失败自动回滚
   1) 记录当前分组（名字 → 成员）
   2) 建分组后核对每个组是否存在且成员数一致
   3) 有缺失 → 重试一次；仍失败 → 用快照把分组恢复回去
"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

HELPER = r'''
def _snapshot_groups():
    """当前 Geometry 分组快照 {名字: [体名]}"""
    try:
        gg = call_bridge("geom_groups", timeout=300)
        out = {}
        for g in (gg.get("groups") or []):
            ns = list(g.get("bodies") or [])
            if ns:
                out[g.get("name")] = ns
        return out
    except Exception:
        return {}


def _verify_groups(expected):
    """校验分组是否都建好；返回缺失/不一致列表"""
    try:
        gg = call_bridge("geom_groups", timeout=300)
        have = {}
        for g in (gg.get("groups") or []):
            have[g.get("name")] = set(g.get("bodies") or [])
        bad = []
        for name, members in expected.items():
            got = have.get(name)
            if got is None:
                bad.append({"group": name, "reason": "缺失",
                            "want": len(members), "got": 0})
                continue
            want = set(members)
            if got != want:
                bad.append({"group": name, "reason": "成员不一致",
                            "want": len(want), "got": len(got),
                            "missing": sorted(want - got)[:5],
                            "extra": sorted(got - want)[:5]})
        return bad
    except Exception as e:
        return [{"group": "-", "reason": "校验失败: %s" % str(e)[:80]}]


def _restore_groups(snapshot):
    """用快照把分组重建回去（尽力而为）"""
    if not snapshot:
        return {"ok": False, "error": "没有快照"}
    items = [{"group": k, "bodies": v} for k, v in snapshot.items() if v]
    try:
        call_bridge("ungroup_folders", {"names": ["*"]}, timeout=1800)
        r = call_bridge("group_geometry", {"items": items}, timeout=3600)
        return {"ok": bool(r.get("ok")), "groups": r.get("groups"),
                "failed": r.get("failed")}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}

'''

if "_snapshot_groups" not in src:
    anchor = "def scan_model("
    src = src.replace(anchor, HELPER.strip() + "\n\n\n" + anchor, 1)
    print("已加分组快照/校验/恢复工具")

# 在 scan_apply 里：解散前快照；建完后校验；失败回滚
OLD = '''                # 先解散所有旧分组，保证结构扁平、不嵌套
                try:
                    call_bridge("ungroup_folders", {"names": ["*"]},
                                timeout=1800)
                    out["cleared"] = True
                except Exception as e:
                    out["errors"].append("清空旧分组失败: %s" % str(e)[:80])'''
NEW = '''                # 解散前先记下当前分组（失败可回滚）
                _before = _snapshot_groups()
                out["snapshot_groups"] = len(_before)
                try:
                    call_bridge("ungroup_folders", {"names": ["*"]},
                                timeout=1800)
                    out["cleared"] = True
                except Exception as e:
                    out["errors"].append("清空旧分组失败: %s" % str(e)[:80])'''
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("scan_apply 已加解散前快照")
else:
    print("警告：未找到解散段")

# 建分组后校验
OLD2 = '''                    out["geom_groups"] = len(gg.get("groups") or [])
                    out["geom_skipped"] = len(gg.get("skipped") or [])
                    if gg.get("failed"):
                        out["errors"].extend(gg["failed"][:5])'''
NEW2 = '''                    out["geom_groups"] = len(gg.get("groups") or [])
                    out["geom_skipped"] = len(gg.get("skipped") or [])
                    if gg.get("failed"):
                        out["errors"].extend(gg["failed"][:5])

                    # 建完核对：缺组/成员不一致 → 重试一次 → 仍不行就回滚
                    _bad = _verify_groups({g: ns for g, ns in groups.items() if ns})
                    if _bad:
                        out["verify_failed"] = _bad[:8]
                        try:
                            _items = [{"group": g, "bodies": ns}
                                      for g, ns in groups.items() if ns]
                            call_bridge("group_geometry", {"items": _items},
                                        timeout=3600)
                            _bad2 = _verify_groups(
                                {g: ns for g, ns in groups.items() if ns})
                        except Exception:
                            _bad2 = _bad
                        if _bad2:
                            out["errors"].append(
                                "分组重建未通过校验，已尝试回滚到原分组")
                            _rb = _restore_groups(_before)
                            out["rollback"] = _rb
                        else:
                            out["verify_failed"] = []
                            out["retried"] = True'''
if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    print("scan_apply 已加建后校验与回滚")
else:
    print("警告：未找到建分组结果段")

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
compile(io.open(P, encoding="utf-8").read(), P, "exec")
print("server 语法 OK")
