# -*- coding: utf-8 -*-
"""把旧的全局覆盖表迁移成「按模型」文件：
   - 对每个在线实例：取体列表 → 只保留能解析的键 → 写成 group_overrides__<模型>.json
   - 全部迁移成功后把旧文件改名为 .bak，避免继续被读取
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

FUNC = '''
def migrate_overrides_per_model():
    """旧全局覆盖表 → 按模型拆分（只保留能解析的体 Id/体名）"""
    legacy = os.path.join(APP_DIR, "group_overrides.json")
    if not os.path.isfile(legacy):
        return {"ok": True, "note": "没有旧文件"}
    try:
        with open(legacy, encoding="utf-8") as f:
            old = json.load(f) or {}
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}
    if not old:
        return {"ok": True, "note": "旧文件为空"}

    report = []
    for a in _agents_raw():
        pid = int(a.get("pid"))
        try:
            name, path = _project_of_pid(pid)
        except Exception:
            name, path = (a.get("project"), a.get("project"))
        base = (name or "").strip()
        if base.lower().endswith(".wbpj"):
            base = base[:-5]
        key = "".join(c if (c.isalnum() or c in "-_") else "_" for c in base)[:60]
        if not key:
            continue
        try:
            lb = call_bridge_pid(pid, "list_bodies", {}, timeout=40)
        except Exception:
            continue
        if not lb.get("ok"):
            report.append({"model": key, "error": "读不到体"})
            continue
        id2name = {}
        names = set()
        for b in (lb.get("bodies") or []):
            if b.get("id") is not None:
                id2name[str(b["id"])] = b.get("name")
            if b.get("name"):
                names.add(b["name"])
        keep = {}
        dropped = 0
        for k, v in old.items():
            if str(k) in id2name:
                keep[str(k)] = v
            elif str(k) in names:
                keep[str(k)] = v
            else:
                dropped += 1
        outp = os.path.join(APP_DIR, "group_overrides__%s.json" % key)
        try:
            with open(outp, "w", encoding="utf-8") as f:
                json.dump(keep, f, ensure_ascii=False, indent=1)
        except Exception as e:
            report.append({"model": key, "error": str(e)[:60]})
            continue
        report.append({"model": key, "kept": len(keep), "dropped": dropped})
    try:
        os.rename(legacy, legacy + ".bak")
    except Exception:
        pass
    return {"ok": True, "report": report, "legacy": "已改名为 .bak"}

'''

if "def migrate_overrides_per_model(" not in s:
    s = s.replace("def _model_key():", FUNC.strip() + "\n\n\ndef _model_key():", 1)
    print("已加迁移函数")

ROUTE = '''            if path == "/api/ovr/migrate":
                return self._json(migrate_overrides_per_model())

'''
if "/api/ovr/migrate" not in s:
    anchor = '            if path == "/api/model_key":'
    pos = s.find(anchor, s.find("def do_GET"))
    if pos > 0:
        s = s[:pos] + ROUTE + s[pos:]
        print("已加 /api/ovr/migrate")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
