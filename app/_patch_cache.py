# -*- coding: utf-8 -*-
"""给重接口加缓存：页面立刻拿到上次结果，后台再刷新
   - status 30s / body_counts 60s / categories 60s
   - 第一次仍会慢一次，之后都是毫秒级返回
   - 返回里带 cached=true / age 秒，页面可显示"数据 20 秒前"
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

HELPER = '''
_CACHE = {}
_CACHE_BUSY = {}


def cache_get(key, ttl, producer, block=True):
    """缓存读取；过期则后台刷新（block=True 时首次同步生成）"""
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        data = dict(hit[1]) if isinstance(hit[1], dict) else hit[1]
        if isinstance(data, dict):
            data["cached"] = True
            data["cache_age"] = round(now - hit[0], 1)
        return data
    if hit and not _CACHE_BUSY.get(key):
        _CACHE_BUSY[key] = True

        def _bg():
            try:
                val = producer()
                if isinstance(val, dict) and val.get("ok"):
                    _CACHE[key] = (time.time(), val)
            except Exception:
                pass
            finally:
                _CACHE_BUSY[key] = False

        try:
            import threading
            threading.Thread(target=_bg, daemon=True).start()
        except Exception:
            _CACHE_BUSY[key] = False
        data = dict(hit[1]) if isinstance(hit[1], dict) else hit[1]
        if isinstance(data, dict):
            data["cached"] = True
            data["cache_age"] = round(now - hit[0], 1)
            data["refreshing"] = True
        return data
    if not block and not hit:
        return {"ok": False, "loading": True,
                "error": "首次读取中，请稍候刷新"}
    val = producer()
    if isinstance(val, dict) and val.get("ok"):
        _CACHE[key] = (time.time(), val)
    return val


def cache_invalidate(prefix=""):
    for k in list(_CACHE.keys()):
        if not prefix or str(k).startswith(prefix):
            _CACHE.pop(k, None)

'''

if "def cache_get(" not in s:
    s = s.replace("def _mech_busy():", HELPER.strip() + "\n\n\ndef _mech_busy():", 1)
    print("已加缓存工具")

# status
OLD = '''                return self._json(call_bridge("status", timeout=120))'''
if OLD in s:
    pass  # 位置未知，下面按分支处理

# 用路径分支替换：status
i = s.find('if path == "/api/status":')
if i > 0:
    j = s.find('return ', i)
    # 找到该分支的 return 行
    k = s.find("\n", s.find('return self._json(call_bridge("status"', i))
    line_start = s.rfind("\n", i, s.find('return self._json(call_bridge("status"', i))
    old_line = s[line_start:s.find("\n", s.find('return self._json(call_bridge("status"', i))+1]
    new_line = ('\n                _tp = target_pid()\n'
                '                return self._json(cache_get(\n'
                '                    ("status", _tp), 30,\n'
                '                    lambda: call_bridge("status", timeout=180)))')
    s = s[:line_start] + new_line + s[s.find("\n", s.find('return self._json(call_bridge("status"', i))+1):]
    print("status 已接入缓存")

# body_counts
i2 = s.find('return self._json(call_bridge("body_counts"')
if i2 > 0:
    ls = s.rfind("\n", 0, i2)
    le = s.find("\n", i2)
    new2 = ('\n                _tp2 = target_pid()\n'
            '                return self._json(cache_get(\n'
            '                    ("body_counts", _tp2), 60,\n'
            '                    lambda: call_bridge("body_counts", timeout=180)))')
    s = s[:ls] + new2 + s[le:]
    print("body_counts 已接入缓存")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
