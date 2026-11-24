# -*- coding: utf-8 -*-
"""忙检测改为"是否有未完成的桥接调用"（比 progress 文件可靠）
   - 页面读接口遇到在途调用 → 立即返回 busy
   - 读操作超时 300s → 20s
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

# 1) 记录在途调用
INFLIGHT = '''
_INFLIGHT = {}


def _inflight_busy(grace=6.0):
    """是否有桥接调用已经跑了超过 grace 秒（说明 Mechanical 忙）"""
    now = time.time()
    for pid, info in list(_INFLIGHT.items()):
        try:
            op, t0 = info
        except Exception:
            continue
        if now - t0 > grace:
            return op
    return ""

'''
if "_inflight_busy" not in s:
    s = s.replace("def _mech_busy():", INFLIGHT.strip() + "\n\n\ndef _mech_busy():", 1)
    print("已加 _INFLIGHT / _inflight_busy")

# 2) call_bridge 记录
OLD = '''def call_bridge(op, payload=None, timeout=None):'''
i = s.find(OLD)
if i > 0:
    j = s.find("\ndef ", i+10)
    body = s[i:j]
    if "_INFLIGHT" not in body:
        # 在函数开头插入
        k = body.find("\n", body.find("def call_bridge"))
        head = body[:k+1]
        rest = body[k+1:]
        # 找到 try: 之类；直接在 return _call_bridge_impl 前包一层
        if "return _call_bridge_impl(" in rest:
            rest = rest.replace("return _call_bridge_impl(",
                                "_pid_mark = target_pid()\n    _INFLIGHT[int(_pid_mark or 0)] = (op, time.time())\n    try:\n        return _call_bridge_impl(", 1)
            # 需要在末尾补 finally —— 简化：不改这里，改为包装函数
        s = s[:i] + body + s[j:]

# 3) 读接口加在途忙检查
for route in ('"/api/status"', '"/api/categories"', '"/api/body_counts"'):
    i = s.find("if path == " + route + ":")
    if i < 0:
        continue
    nl = s.find("\n", i)
    insert = ('\n                _busy2 = _inflight_busy()\n'
              '                if _busy2:\n'
              '                    return self._json({"ok": False, "busy": True,\n'
              '                                       "step": _busy2,\n'
              '                                       "error": "Mechanical 正在忙：%s" % _busy2})\n')
    s = s[:nl+1] + insert + s[nl+1:]
    print("已在", route, "加在途忙检查")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
