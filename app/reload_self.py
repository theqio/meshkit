# -*- coding: utf-8 -*-
"""在 Mechanical 内自我重载 bridge_agent.py
   通过调用栈找到代理模块的 globals（op_exec 的 f_globals），
   停掉旧定时器后把新代码执行进同一命名空间，函数全部替换为新版。
   结果写到 app/ipc/reload_result.json（不打印，避免流被关闭）
"""
import sys
import os
import json
import time
import traceback

ROOT = r"D:/ai/meshkit"
TARGET = os.path.join(ROOT, "app", "bridge_agent.py")
RESULT = os.path.join(ROOT, "app", "ipc", "reload_result.json")

res = {"ok": False, "step": "start", "ts": time.strftime("%Y-%m-%d %H:%M:%S")}


def _write():
    try:
        with open(RESULT, "w") as f:
            f.write(json.dumps(res))
    except Exception:
        pass


def _find_agent_globals():
    """找含 AGENT_VERSION 的命名空间：先扫 sys.modules，再尝试 __main__"""
    tried = []
    for name in list(sys.modules.keys()):
        m = sys.modules.get(name)
        d = getattr(m, "__dict__", None)
        if isinstance(d, dict) and "AGENT_VERSION" in d and "IPC_DIR" in d:
            return d
        tried.append(name)
    m = sys.modules.get("__main__")
    d = getattr(m, "__dict__", None)
    if isinstance(d, dict) and "AGENT_VERSION" in d:
        return d
    return None


def _main():
    g = _find_agent_globals()
    if g is None:
        res["step"] = "not_found"
        return
    res["old_version"] = g.get("AGENT_VERSION")

    # 不停旧定时器：它们会继续调用同一命名空间里的（已被替换的）函数
    res["timers_alive"] = len(g.get("_keep") or [])

    res["step"] = "execfile"
    old_name = g.get("__name__")
    g["__name__"] = "meshkit_bridge_reloaded"
    try:
        execfile(TARGET, g)
    except Exception:
        res["exec_error"] = traceback.format_exc()[-500:]
    finally:
        g["__name__"] = old_name if old_name is not None else "__main__"

    # 确保调度器还在跑：execfile 后如果定时器没起来，手动补一次
    # 如果确实没有任何定时器（比如之前被停过），补启动一次
    try:
        if not (g.get("_keep") or []):
            starter = g.get("start")
            if starter is not None:
                starter()
                res["started"] = True
        else:
            res["timers"] = len(g.get("_keep") or [])
    except Exception:
        res["start_error"] = traceback.format_exc()[-300:]

    res["new_version"] = g.get("AGENT_VERSION")
    res["ok"] = bool(res.get("new_version"))
    res["step"] = "done"


try:
    _main()
except Exception:
    res["step"] = "exception"
    res["error"] = traceback.format_exc()[-500:]

_write()
