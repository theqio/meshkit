# -*- coding: utf-8 -*-
"""稳定性优先：
   1) 「连接」不再自动按 Ctrl+F5（多次触发会让 Mechanical 里堆叠定时器，导致闪退）
      改为：激活窗口 + 打开 Scripting 面板 + 复制命令，由你按一次 Ctrl+F5
   2) 取消服务启动时的自动预热（避免 Mechanical 刚启动就被连续命令冲击）
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

# 1) connect 不再发 Ctrl+F5
OLD = '''    # 未连接：先放命令进剪贴板，再走全自动尝试
    clip = _set_clipboard(MECH_CONNECT_CMD)
    auto = {"ok": False, "stage": "未开始"}
    try:
        auto = _mech_auto_connect(pid)
    except Exception as e:
        auto = {"ok": False, "stage": "异常", "error": str(e)[:160]}'''
NEW = '''    # 未连接：只做"激活窗口 + 打开面板 + 复制命令"，不自动敲 Ctrl+F5
    clip = _set_clipboard(MECH_CONNECT_CMD)
    auto = {"ok": False, "stage": "等待手动加载",
            "error": "为避免重复启动定时器导致 Mechanical 闪退，连接不再自动执行脚本"}
    try:
        act = _activate_window(pid)
        if act == "ok" and not _scripting_pane_open(pid):
            _open_scripting_pane(pid)
    except Exception:
        pass'''
if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("connect 已改为只准备环境，不自动运行")
else:
    print("未找到 connect 自动段")

# 2) 返回文案
OLD2 = '''    act = auto.get("stage") or "失败"
    pane = "auto"'''
NEW2 = '''    act = "ok"
    pane = "ready"'''
if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)

OLD3 = '''            "message": "自动连接未成功（停在：%s）。请到 Mechanical 的 "
                       "Scripting 面板手动点运行或按 Ctrl+F5；命令已在剪贴板。"
                       % (auto.get("stage") or "未知")}'''
NEW3 = '''            "message": "已激活该窗口并把命令放进剪贴板。请在它的 Scripting 面板里按一次 Ctrl+F5"
                       "（只需一次；重复执行会堆叠定时器导致闪退）"}'''
if OLD3 in s:
    s = s.replace(OLD3, NEW3, 1)
    print("connect 提示文案已更新")

# 3) 取消启动预热
OLD4 = '''try:
    import threading as _bt
    _bt.Thread(target=_boot_warm, daemon=True).start()
except Exception:
    pass'''
NEW4 = '''# 不在启动时自动预热：Mechanical 刚起来时连续命令容易引起闪退
# （需要时页面会自行触发首次读取）'''
if OLD4 in s:
    s = s.replace(OLD4, NEW4, 1)
    print("已取消启动预热")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("server 语法 OK")
