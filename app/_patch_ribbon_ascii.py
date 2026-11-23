# -*- coding: utf-8 -*-
"""修两个问题：
   1) 工具栏脚本改为纯 ASCII（IronPython 2.7 无编码声明时遇中文会 SyntaxError，
      表现就是"点按钮没反应"），并写运行标记便于排查
   2) /api/agents 过滤掉已退出的 Mechanical 进程（否则残留条目污染列表）
"""
import io
import os
import shutil
import time

# ---------- 1) 重写工具栏脚本 ----------
base = os.path.join(os.environ["APPDATA"],
                    r"Ansys\v221\Applets\DSApplet\UserRibbons\scripts")
MARK = r"D:\ai\meshkit\app\ipc\ribbon_ran.txt"

SCRIPTS = {
"MeshKit_Check.py": '''# -*- coding: utf-8 -*-
# MeshKit: load / reload the bridge agent inside Mechanical
# (ASCII only: IronPython 2.7 fails on non-ASCII source without a coding line)
import os
import time

AGENT = "D:/ai/meshkit/app/bridge_agent.py"
MARK = "D:/ai/meshkit/app/ipc/ribbon_ran.txt"

try:
    with open(MARK, "w") as f:
        f.write("check clicked %s\\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
except Exception:
    pass

print("[MeshKit] loading bridge agent ...")
if os.path.isfile(AGENT):
    try:
        execfile(AGENT)
        print("[MeshKit] bridge agent loaded")
    except Exception as exc:
        print("[MeshKit] LOAD FAILED: %s" % exc)
else:
    print("[MeshKit] agent not found: %s" % AGENT)
''',
"MeshKit_Run.py": '''# -*- coding: utf-8 -*-
# MeshKit: open the control panel in a browser
import os
import webbrowser

URL = "http://127.0.0.1:8765"
try:
    webbrowser.open(URL)
except Exception:
    try:
        os.startfile(URL)
    except Exception:
        pass
print("[MeshKit] opened %s" % URL)
''',
"MeshKit_Report.py": '''# -*- coding: utf-8 -*-
# MeshKit: open the quality report page
import os
import webbrowser

URL = "http://127.0.0.1:8765/report"
try:
    webbrowser.open(URL)
except Exception:
    try:
        os.startfile(URL)
    except Exception:
        pass
print("[MeshKit] opened %s" % URL)
''',
}

for name, body in SCRIPTS.items():
    p = os.path.join(base, name)
    with io.open(p, "w", encoding="ascii", newline="\r\n") as f:
        f.write(body)
    print("已写入 %-22s %d 字节" % (name, os.path.getsize(p)))

# 校验纯 ASCII
for name in SCRIPTS:
    raw = io.open(os.path.join(base, name), "rb").read()
    non_ascii = [b for b in raw if b > 127]
    print("  %-22s 非ASCII字节=%d %s" % (name, len(non_ascii),
                                        "OK" if not non_ascii else "← 有问题"))

# ---------- 2) 过滤失效 agent ----------
S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(S, encoding="utf-8").read()

OLD = '''def agents_list():
    """在线实例 + 工程名（沿父进程找到 Workbench 的 wbpj）"""
    out = []
    for a in _agents_raw():'''
NEW = '''def _pid_alive(pid):
    """该 pid 是否还活着"""
    try:
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
               "$p=Get-Process -Id %d -ErrorAction SilentlyContinue;"
               "if($p){'1'}else{'0'}" % int(pid)]
        r = subprocess.run(cmd, capture_output=True, timeout=10,
                           creationflags=0x08000000)
        return (r.stdout or b"").strip() == b"1"
    except Exception:
        return True


def agents_list():
    """在线实例 + 工程名（沿父进程找到 Workbench 的 wbpj）"""
    out = []
    for a in _agents_raw():
        if not _pid_alive(a.get("pid")):
            continue'''
if OLD in src:
    src = src.replace(OLD, NEW, 1)
    io.open(S, "w", encoding="utf-8", newline="\n").write(src)
    print("\nserver: agents_list 已过滤失效进程")
else:
    print("\n警告：未找到 agents_list")

compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("server 语法 OK")
