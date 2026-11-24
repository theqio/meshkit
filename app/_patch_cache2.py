# -*- coding: utf-8 -*-
"""重接口加缓存（精确替换，不破坏结构）"""
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
    """缓存读取；过期则后台刷新，先返回旧值（避免页面等几十秒）"""
    now = time.time()
    hit = _CACHE.get(key)

    def _tag(data, refreshing=False):
        if isinstance(data, dict):
            data = dict(data)
            if hit:
                data["cached"] = True
                data["cache_age"] = round(now - hit[0], 1)
            if refreshing:
                data["refreshing"] = True
        return data

    if hit and now - hit[0] < ttl:
        return _tag(hit[1])

    if hit:
        if not _CACHE_BUSY.get(key):
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
        return _tag(hit[1], refreshing=True)

    if not block:
        return {"ok": False, "loading": True,
                "error": "首次读取中，请稍后再刷新"}

    val = producer()
    if isinstance(val, dict) and val.get("ok"):
        _CACHE[key] = (time.time(), val)
    return val


def cache_invalidate(prefix=""):
    for k in list(_CACHE.keys()):
        if not prefix or str(k).startswith(str(prefix)):
            _CACHE.pop(k, None)

'''

if "def cache_get(" not in s:
    s = s.replace("def _mech_busy():", HELPER.strip() + "\n\n\ndef _mech_busy():", 1)
    print("已加缓存工具")

# ---- status ----
OLD_STATUS = '''                _st = call_bridge("status")
                try:
                    _remember_failed(_st)      # status_remember
                except Exception:
                    pass
                return self._json(_st)'''
NEW_STATUS = '''                _tp_s = target_pid()
                _st = cache_get(("status", _tp_s), 30,
                                lambda: call_bridge("status", timeout=180),
                                block=bool(body_cfg_status_block()))
                try:
                    _remember_failed(_st)      # status_remember
                except Exception:
                    pass
                return self._json(_st)'''
if OLD_STATUS in s:
    s = s.replace(OLD_STATUS, NEW_STATUS, 1)
    print("status 已接入缓存")
else:
    print("未找到 status 段")

# ---- body_counts ----
OLD_BC = '''                return self._json(call_bridge("body_counts", timeout=120))'''
NEW_BC = '''                _tp_b = target_pid()
                return self._json(cache_get(("body_counts", _tp_b), 90,
                                            lambda: call_bridge("body_counts", timeout=180),
                                            block=False))'''
if OLD_BC in s:
    s = s.replace(OLD_BC, NEW_BC, 1)
    print("body_counts 已接入缓存")
else:
    print("未找到 body_counts 段")

# ---- categories：包一层缓存 ----
OLD_CAT = '''                # ⓿ 优先用几何识别结果（与扫描分组一致）
                _rec = categories_from_recognition()'''
NEW_CAT = '''                # ⓿ 优先用几何识别结果（与扫描分组一致）—— 带 60 秒缓存
                _tp_c = target_pid()
                _rec = cache_get(("categories", _tp_c), 60,
                                 categories_from_recognition, block=False)
                if _rec.get("loading"):
                    return self._json(_rec)
                if not _rec.get("cached"):
                    pass'''
if OLD_CAT in s:
    s = s.replace(OLD_CAT, NEW_CAT, 1)
    print("categories 已接入缓存")
else:
    print("未找到 categories 段")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
