# -*- coding: utf-8 -*-
"""缓存版重接口（统一 block=False + 启动预热）"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

HELPER = '''
_CACHE = {}
_CACHE_BUSY = {}


def cache_get(key, ttl, producer, block=False):
    """缓存读取；过期则后台刷新，先返回旧值；没有旧值时返回 loading"""
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

    def _spawn():
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

    if hit:
        if not _CACHE_BUSY.get(key):
            _spawn()
        return _tag(hit[1], refreshing=True)

    if not block:
        if not _CACHE_BUSY.get(key):
            _spawn()
        return {"ok": False, "loading": True,
                "error": "首次读取中（Mechanical 较慢），几秒后自动刷新"}

    val = producer()
    if isinstance(val, dict) and val.get("ok"):
        _CACHE[key] = (time.time(), val)
    return val


def cache_invalidate(prefix=""):
    for k in list(_CACHE.keys()):
        if not prefix or str(k).startswith(str(prefix)):
            _CACHE.pop(k, None)


def _warm_cache():
    """后台预热：连上就先把重的数据算好"""
    try:
        _tp = target_pid()
        if not _tp:
            return
        for key, ttl, fn in (
            (("status", _tp), 30, lambda: call_bridge("status", timeout=180)),
            (("categories", _tp), 60, categories_from_recognition),
            (("body_counts", _tp), 90, lambda: call_bridge("body_counts", timeout=180)),
        ):
            try:
                if key not in _CACHE:
                    val = fn()
                    if isinstance(val, dict) and val.get("ok"):
                        _CACHE[key] = (time.time(), val)
            except Exception:
                pass
    except Exception:
        pass

'''

if "def cache_get(" not in s:
    s = s.replace("def _mech_busy():", HELPER.strip() + "\n\n\ndef _mech_busy():", 1)
    print("已加缓存工具 + 预热")

OLD_STATUS = '''                _st = call_bridge("status")
                try:
                    _remember_failed(_st)      # status_remember
                except Exception:
                    pass
                return self._json(_st)'''
NEW_STATUS = '''                _tp_s = target_pid()
                _st = cache_get(("status", _tp_s), 30,
                                lambda: call_bridge("status", timeout=180))
                try:
                    _remember_failed(_st)      # status_remember
                except Exception:
                    pass
                return self._json(_st)'''
if OLD_STATUS in s:
    s = s.replace(OLD_STATUS, NEW_STATUS, 1)
    print("status 已接入缓存")

OLD_BC = '''                return self._json(call_bridge("body_counts", timeout=120))'''
NEW_BC = '''                _tp_b = target_pid()
                return self._json(cache_get(("body_counts", _tp_b), 90,
                                            lambda: call_bridge("body_counts",
                                                                timeout=180)))'''
if OLD_BC in s:
    s = s.replace(OLD_BC, NEW_BC, 1)
    print("body_counts 已接入缓存")

OLD_CAT = '''                # ⓿ 优先用几何识别结果（与扫描分组一致）
                _rec = categories_from_recognition()'''
NEW_CAT = '''                # ⓿ 优先用几何识别结果（与扫描分组一致）—— 60 秒缓存
                _tp_c = target_pid()
                _rec = cache_get(("categories", _tp_c), 60,
                                 categories_from_recognition)
                if _rec.get("loading"):
                    return self._json(_rec)'''
if OLD_CAT in s:
    s = s.replace(OLD_CAT, NEW_CAT, 1)
    print("categories 已接入缓存")

# 启动预热线程
OLD_BOOT = '''if __name__ == "__main__":'''
NEW_BOOT = '''def _boot_warm():
    time.sleep(5)
    try:
        _warm_cache()
    except Exception:
        pass


try:
    import threading as _bt
    _bt.Thread(target=_boot_warm, daemon=True).start()
except Exception:
    pass


if __name__ == "__main__":'''
if OLD_BOOT in s and "_boot_warm" not in s:
    s = s.replace(OLD_BOOT, NEW_BOOT, 1)
    print("已加启动预热")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
