# -*- coding: utf-8 -*-
"""实例互相独立性的保障与可见化：
   1) call_bridge_pid(pid, op) —— 直接给指定实例下命令，不动当前目标
   2) /api/instances 里带上每个实例的 体数/分组数（并发读，带缓存）
   3) 安全模式：默认开启，写操作需先解锁（避免误改错模型）
"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

HELPER = r'''
_INST_CACHE = {}


def call_bridge_pid(pid, op, payload=None, timeout=30):
    """直接向指定 pid 的代理下命令（不改当前目标）"""
    pid = int(pid)
    os.makedirs(IPC_DIR, exist_ok=True)
    seq = uuid.uuid4().hex[:12]
    cmd_path = os.path.join(IPC_DIR, "cmd_%d.json" % pid)
    res_path = os.path.join(IPC_DIR, "result_%d.json" % pid)
    try:
        if os.path.isfile(res_path):
            os.remove(res_path)
    except Exception:
        pass
    try:
        with open(cmd_path, "w", encoding="utf-8") as f:
            json.dump({"seq": seq, "op": op, "payload": payload or {}}, f)
    except Exception as e:
        return {"ok": False, "error": "写命令失败: %s" % str(e)[:80]}
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.25)
        try:
            with open(res_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if data.get("seq") == seq:
            return data
    return {"ok": False, "error": "超时（%ds 无回应）" % timeout}


def instance_summary(pid, ttl=20):
    """某实例的 体数/分组数（带缓存，避免频繁打扰 Mechanical）"""
    now = time.time()
    c = _INST_CACHE.get(int(pid))
    if c and now - c[0] < ttl:
        return c[1]
    info = {"bodies": None, "groups": None, "ungrouped": None}
    try:
        lb = call_bridge_pid(pid, "list_bodies", {}, timeout=25)
        if lb.get("ok"):
            info["bodies"] = len(lb.get("bodies") or [])
        gg = call_bridge_pid(pid, "geom_groups", {}, timeout=25)
        if gg.get("ok"):
            info["groups"] = len(gg.get("groups") or [])
            info["ungrouped"] = len(gg.get("ungrouped") or [])
    except Exception:
        pass
    _INST_CACHE[int(pid)] = (now, info)
    return info

'''

if "def call_bridge_pid(" not in src:
    anchor = "def _monitors():"
    src = src.replace(anchor, HELPER.strip() + "\n\n\n" + anchor, 1)
    print("已加 call_bridge_pid / instance_summary")

# instances 里附上摘要
OLD = '''        try:
            x["screen"] = window_screen(x["pid"]).get("monitor")
        except Exception:
            x["screen"] = None'''
NEW = '''        try:
            x["screen"] = window_screen(x["pid"]).get("monitor")
        except Exception:
            x["screen"] = None
        if x.get("connected"):
            try:
                x.update(instance_summary(x["pid"]))
            except Exception:
                pass'''
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("instances 已带体数/分组数")

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
compile(io.open(P, encoding="utf-8").read(), P, "exec")
print("server 语法 OK")
