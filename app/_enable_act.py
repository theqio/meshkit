# -*- coding: utf-8 -*-
"""启用 ACT 扩展：MeshKit 工具栏（比 User Buttons 可靠）
   - 复制 _MeshKit_unused → extensions\\MeshKit
   - 三个按钮：加载桥接 / 打开控制台 / 打开报告
"""
import io
import os
import shutil

EXT = os.path.join(os.environ["APPDATA"], r"Ansys\v221\ACT\extensions")
SRC = os.path.join(EXT, "_MeshKit_unused")
DST = os.path.join(EXT, "MeshKit")

if not os.path.isdir(SRC):
    raise SystemExit("找不到源扩展目录: %s" % SRC)

if os.path.isdir(DST):
    bak = DST + "_bak"
    if os.path.isdir(bak):
        shutil.rmtree(bak)
    shutil.move(DST, bak)
    print("旧 MeshKit 扩展已备份为 MeshKit_bak")

shutil.copytree(SRC, DST)
print("已复制扩展到:", DST)

# ---------- 重写清单 ----------
MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<extension version="1" name="MeshKit">
  <author>qiyuqiang</author>
  <description>MeshKit: load bridge agent and open the control panel</description>
  <guid>8f4b6c2e-31a7-4d19-9b6f-2c5e7a9d1b34</guid>

  <script src="scripts/meshkit_addin.py" encoding="utf-8" />

  <interface>
    <toolbar name="MeshKit" caption="MeshKit">
      <button name="btnLoad" caption="加载桥接" command="load_bridge"
              icon="images/check.png" />
      <button name="btnPanel" caption="打开控制台" command="open_panel"
              icon="images/run_all.png" />
      <button name="btnReport" caption="打开报告" command="open_report"
              icon="images/report.png" />
    </toolbar>
  </interface>
</extension>
"""
with io.open(os.path.join(DST, "MeshKit.xml"), "w", encoding="utf-8") as f:
    f.write(MANIFEST)
print("已重写 MeshKit.xml")

# ---------- 重写 addin ----------
ADDIN = '''# -*- coding: utf-8 -*-
# MeshKit ACT addin: load bridge agent / open panel / open report
import os
import traceback

AGENT = "D:/ai/meshkit/app/bridge_agent.py"
BASE = "http://127.0.0.1:8765"


def _log(text):
    try:
        print("[MeshKit] %s" % text)
    except Exception:
        pass


def _open(url):
    try:
        import webbrowser
        webbrowser.open(url)
        return True
    except Exception:
        pass
    try:
        os.startfile(url)
        return True
    except Exception:
        return False


def load_bridge():
    """在 Mechanical 内加载桥接代理"""
    _log("loading %s" % AGENT)
    try:
        if not os.path.isfile(AGENT):
            _log("agent not found")
            return
        execfile(AGENT, {"__name__": "__main__"})
        _log("bridge agent loaded")
        _open(BASE)
    except Exception:
        _log("load failed: %s" % traceback.format_exc())


def open_panel():
    _log("open panel")
    _open(BASE)


def open_report():
    _log("open report")
    _open(BASE + "/report")


def onstartup():
    _log("MeshKit addin loaded")


def onshutdown():
    pass
'''
with io.open(os.path.join(DST, "scripts", "meshkit_addin.py"), "w",
             encoding="utf-8", newline="\n") as f:
    f.write(ADDIN)
print("已重写 addin 脚本")

print("\n=== 结果 ===")
for root, dirs, files in os.walk(DST):
    for f in files:
        p = os.path.join(root, f)
        print("  %-42s %6d" % (p.replace(DST, "MeshKit"), os.path.getsize(p)))
