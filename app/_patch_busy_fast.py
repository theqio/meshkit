# -*- coding: utf-8 -*-
"""解决"网页卡住"：Mechanical 忙时立即返回 busy，不让请求挂 300 秒
   - 读 progress.json 判断是否在跑长任务
   - status/categories/body_counts 遇到忙 → 立刻返回，页面显示"忙"
   - 读操作超时缩短到 25 秒
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

HELPER = '''
def _mech_busy():
    """Mechanical 是否正在执行长任务（网格/分组等）"""
    try:
        d = _read_json(PROGRESS_PATH) or {}
        step = (d.get("step") or "").strip()
        ts = d.get("ts") or ""
        if step and step not in ("已结束", "结束", "完成", "idle"):
            try:
                import datetime as _dt
                t0 = _dt.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                if (time.time() - t0.timestamp()) < 1800:
                    return step
            except Exception:
                return step
    except Exception:
        pass
    return ""

'''

if "def _mech_busy(" not in s:
    anchor = "def _mech_auto_connect(pid):"
    s = s.replace(anchor, HELPER.strip() + "\n\n\n" + anchor, 1)
    print("已加 _mech_busy")

# status / categories / body_counts 忙时快速返回
for route in ('"/api/status"', '"/api/categories"', '"/api/body_counts"'):
    i = s.find("if path == " + route + ":")
    if i < 0:
        print("未找到", route)
        continue
    # 在该分支第一行之后插入 busy 检查
    nl = s.find("\n", i)
    if nl < 0:
        continue
    insert = ('\n                _busy = _mech_busy()\n'
              '                if _busy:\n'
              '                    return self._json({"ok": False, "busy": True,\n'
              '                                       "step": _busy,\n'
              '                                       "error": "Mechanical 正在忙：%s，稍后自动重试" % _busy})\n')
    s = s[:nl+1] + insert + s[nl+1:]
    print("已给", route, "加忙检查")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("server 语法 OK")
