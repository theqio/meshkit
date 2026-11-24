# -*- coding: utf-8 -*-
"""修"全红"：控制项清单单独缓存（5 分钟 + 后台刷新）
   - 快速模式下有缓存就用（正常判灯）
   - 没缓存时标"读取中"，不再误判成"没有控制项"
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

HELPER = '''
_CTRL_CACHE = {}
_CTRL_BUSY = {}


def ctrl_names_cached(pid=None, ttl=300):
    """Mesh 控制项名字缓存；过期后台刷新。返回 (set 或 None, age)"""
    pid = pid or target_pid()
    now = time.time()
    hit = _CTRL_CACHE.get(pid)
    if hit and now - hit[0] < ttl:
        return set(hit[1]), round(now - hit[0], 1)

    def _spawn():
        _CTRL_BUSY[pid] = True

        def _bg():
            try:
                sc = call_bridge("scan_categories", timeout=240)
                if sc.get("ok"):
                    _CTRL_CACHE[pid] = (time.time(),
                                        [c.get("name") for c in
                                         (sc.get("categories") or [])
                                         if c.get("name")])
            except Exception:
                pass
            finally:
                _CTRL_BUSY[pid] = False

        try:
            import threading
            threading.Thread(target=_bg, daemon=True).start()
        except Exception:
            _CTRL_BUSY[pid] = False

    if hit:
        if not _CTRL_BUSY.get(pid):
            _spawn()
        return set(hit[1]), round(now - hit[0], 1)

    if not _CTRL_BUSY.get(pid):
        _spawn()
    return None, None

'''

if "def ctrl_names_cached(" not in s:
    s = s.replace("def _mech_busy():", HELPER.strip() + "\n\n\ndef _mech_busy():", 1)
    print("已加控制项缓存")

# 替换 ctrl_info 构建：fast 时用缓存
OLD = '''    ctrl_info = {}
    try:
        sc = {} if fast else call_bridge("scan_categories", timeout=120)
        for c in (sc.get("categories") or []):
            n = c.get("name")
            if n and n != "未覆盖":
                ctrl_info[n] = {"size": c.get("size_mm"),
                                "method": c.get("method")}
    except Exception:
        pass
    ctrl_names = set(ctrl_info.keys())'''

NEW = '''    ctrl_info = {}
    ctrl_known = True
    try:
        if fast:
            _cn, _cage = ctrl_names_cached()
            if _cn is None:
                ctrl_known = False          # 还在后台读，先不判灯
                _cn = set()
            for _n in _cn:
                if _n and _n != "未覆盖":
                    ctrl_info.setdefault(_n, {})
        else:
            sc = call_bridge("scan_categories", timeout=120)
            for c in (sc.get("categories") or []):
                n = c.get("name")
                if n and n != "未覆盖":
                    ctrl_info[n] = {"size": c.get("size_mm"),
                                    "method": c.get("method")}
    except Exception:
        ctrl_known = False
    ctrl_names = set(ctrl_info.keys())'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("ctrl_info 已改为缓存优先")
else:
    print("未找到 ctrl_info 段")

# 判灯：控制项未知时给"读取中"，不判红
OLD2 = '''        if sup and sup >= len(names) and names:
            light, reason = "muted", "全部已抑制（不参与网格）"
        elif g not in ctrl_names:
            light, reason = "red", "Mesh 里没有该类别控制项（点「应用设置与尺寸」生成）"'''
NEW2 = '''        if sup and sup >= len(names) and names:
            light, reason = "muted", "全部已抑制（不参与网格）"
        elif not ctrl_known:
            light, reason = "muted", "控制项清单读取中（后台刷新，稍后自动更新）"
        elif g not in ctrl_names:
            light, reason = "red", "Mesh 里没有该类别控制项（点「应用设置与尺寸」生成）"'''
if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)
    print("判灯已加未知态")
else:
    print("未找到判灯段")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
