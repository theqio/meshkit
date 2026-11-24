# -*- coding: utf-8 -*-
"""代理端加速：list_bodies / geom_groups 加短缓存（15 秒）
   - 这两个每次要遍历 290 个体/整棵 Geometry 树，各耗 2~3 秒
   - 重命名/分组/解散后立即失效，保证数据新鲜
"""
import io
import shutil
import time

A = r"D:\ai\meshkit\app\bridge_agent.py"
shutil.copyfile(A, A + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(A, encoding="utf-8-sig").read()

HELP = '''
# ---------------------------------------------------------------- 读缓存
_CACHE_BODIES = {"t": 0.0, "v": None}
_CACHE_GROUPS = {"t": 0.0, "v": None}
CACHE_TTL = 15.0


def _cache_bust():
    _CACHE_BODIES["t"] = 0.0
    _CACHE_BODIES["v"] = None
    _CACHE_GROUPS["t"] = 0.0
    _CACHE_GROUPS["v"] = None


def _cache_get(slot):
    try:
        if slot["v"] is not None and (time.time() - slot["t"]) < CACHE_TTL:
            return slot["v"]
    except Exception:
        pass
    return None


def _cache_put(slot, val):
    slot["t"] = time.time()
    slot["v"] = val

'''

if "_cache_bust" not in s:
    s = s.replace("def op_list_bodies(payload):",
                  HELP.strip() + "\n\n\ndef op_list_bodies(payload):", 1)
    print("已加读缓存工具")

# list_bodies 命中缓存
OLD = '''def op_list_bodies(payload):'''
if OLD in s:
    i = s.find(OLD)
    j = s.find("\ndef ", i + 10)
    body = s[i:j]
    if "_cache_get(_CACHE_BODIES)" not in body:
        body2 = body.replace('"""', '"""', 1)
        # 在函数体第一行插入缓存检查：找函数签名后的第一个空行后
        k = body2.find("\n", body2.find(":"))
        inject = ('\n    _cv = _cache_get(_CACHE_BODIES)\n'
                  '    if _cv is not None:\n'
                  '        return _cv\n')
        body2 = body2[:k+1] + inject + body2[k+1:]
        s = s[:i] + body2 + s[j:]
        print("list_bodies 已接缓存")

io.open(A, "w", encoding="utf-8-sig").write(s)
compile(io.open(A, encoding="utf-8-sig").read(), A, "exec")
print("语法 OK（第一段）")
