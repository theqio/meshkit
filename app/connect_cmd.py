# -*- coding: utf-8 -*-
"""生成"加载桥接"用的命令（自动带上本机安装目录），并复制到剪贴板。
   安装目录变了也不用改这个文件——它自己知道自己在哪。
"""
import io
import os
import sys
import subprocess

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(APP_DIR)
AGENT = os.path.join(APP_DIR, "bridge_agent.py").replace("\\", "/")

CMD = ('import os;os.environ["MESHKIT_ROOT"]=r"%s";execfile(r"%s")'
       % (ROOT, AGENT))


def _copy(text):
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Set-Clipboard -Value @'\n%s\n'@" % text],
                       capture_output=True, timeout=15)
        return True
    except Exception:
        return False


def main():
    ok = _copy(CMD)
    print("=" * 66)
    print(" MeshKit 桥接命令")
    print("=" * 66)
    print("安装目录: %s" % ROOT)
    print("")
    if ok:
        print("命令已复制到剪贴板。")
    else:
        print("自动复制失败，请手动复制下面这一行：")
    print("")
    print(CMD)
    print("")
    print("在 Mechanical 里：Automation -> Scripting -> 粘贴 -> Ctrl+F5")
    print("看到 [bridge] 已启动 即成功（只需执行一次）")
    print("=" * 66)


if __name__ == "__main__":
    main()
