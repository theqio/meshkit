# -*- coding: utf-8 -*-
"""页面侧「实例管理 + 一键连接」

- /api/instances : 列出所有正在运行的 Mechanical（含未连接的），带工程名与连接状态
- /api/connect   : 已连接的直接切过去；未连接的自动把 execfile 命令放进剪贴板、
                   把该 Mechanical 窗口激活，并告诉前端"请按 Ctrl+V 回车"
- 前端轮询 /api/instances，代理一注册就自动变成"已连接"，无需刷新
"""
import io
import shutil
import time

P = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(P, P + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
src = io.open(P, encoding="utf-8").read()

FUNC = r'''
MECH_CONNECT_CMD = 'execfile("D:/ai/meshkit/app/bridge_agent.py")'


def _list_mech_procs():
    """所有正在运行的 AnsysWBU 进程 + 其 Workbench 父进程里的工程路径"""
    try:
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='AnsysWBU.exe'\" | "
              "ForEach-Object { $p=$_; $q=Get-CimInstance Win32_Process "
              "-Filter \"ProcessId=$($p.ParentProcessId)\" -ErrorAction SilentlyContinue; "
              "\"$($p.ProcessId)|$($q.CommandLine)\" }")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, timeout=30, creationflags=0x08000000)
        text = (r.stdout or b"").decode("mbcs", "ignore")
    except Exception:
        return []
    out = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        pid_s, cmd = line.split("|", 1)
        pid_s = pid_s.strip()
        if not pid_s.isdigit():
            continue
        proj = None
        m = cmd.find(".wbpj")
        if m > 0:
            start = cmd.rfind('"', 0, m)
            proj = cmd[start + 1:m + 5] if start >= 0 else None
            if proj:
                proj = os.path.basename(proj)[:-5]
        out.append({"pid": int(pid_s), "project": proj})
    return out


def instances_list():
    """所有 Mechanical 实例（含未连接）+ 连接状态"""
    raw = _agents_raw() if "_agents_raw" in globals() else []
    reg = {}
    for a in raw:
        pid = a.get("pid")
        try:
            pid = int(pid)
        except Exception:
            continue
        if _pid_alive(pid):
            reg[pid] = a
    tgt = target_pid()
    out = []
    for proc in _list_mech_procs():
        pid = proc["pid"]
        a = reg.get(pid)
        out.append({
            "pid": pid,
            "project": proc.get("project") or (a or {}).get("project"),
            "connected": bool(a),
            "version": (a or {}).get("version"),
            "busy": bool((a or {}).get("busy")),
            "target": (pid == tgt),
        })
    out.sort(key=lambda x: (not x["connected"], x["pid"]))
    return {"ok": True, "instances": out,
            "connected_count": sum(1 for x in out if x["connected"]),
            "target": tgt,
            "connect_cmd": MECH_CONNECT_CMD}


def _activate_window(pid):
    """把指定 pid 的 Mechanical 窗口切到前台"""
    try:
        cmd = [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "$sig='[DllImport(\"user32.dll\")] public static extern bool "
            "SetForegroundWindow(IntPtr h);"
            "[DllImport(\"user32.dll\")] public static extern bool "
            "BringWindowToTop(IntPtr h);"
            "[DllImport(\"user32.dll\")] public static extern IntPtr "
            "GetForegroundWindow();';"
            "$t=Add-Type -MemberDefinition $sig -Name W -Namespace MK "
            "-PassThru;"
            "$p=Get-Process -Id %d -ErrorAction SilentlyContinue;"
            "if($p -and $p.MainWindowHandle -ne 0){"
            " $h=$p.MainWindowHandle;"
            " [MK.W]::BringWindowToTop($h) | Out-Null;"
            " [MK.W]::SetForegroundWindow($h) | Out-Null;"
            " Start-Sleep -Milliseconds 300;"
            " if([MK.W]::GetForegroundWindow() -eq $h){'ok'}else{'no'}"
            "} else {'nowin'}" % int(pid)
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=20,
                           creationflags=0x08000000)
        return (r.stdout or b"").decode("mbcs", "ignore").strip()
    except Exception as e:
        return "err:%s" % str(e)[:40]


def _set_clipboard(text):
    try:
        import subprocess as _sp
        esc = text.replace("'", "''")
        r = _sp.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "Set-Clipboard -Value '%s'" % esc],
                    capture_output=True, timeout=20, creationflags=0x08000000)
        return r.returncode == 0
    except Exception:
        return False


def connect_instance(pid):
    """已连接→切换；未连接→激活窗口 + 命令进剪贴板，返回引导信息"""
    pid = int(pid)
    for proc in _list_mech_procs():
        if proc["pid"] == pid:
            break
    else:
        return {"ok": False, "error": "该 Mechanical 进程不存在（可能已关闭）"}

    # 已连接：直接切换
    for a in (_agents_raw() if "_agents_raw" in globals() else []):
        try:
            if int(a.get("pid")) == pid and _pid_alive(pid):
                cfg = load_config()
                cfg["target_pid"] = pid
                save_config(cfg)
                return {"ok": True, "connected": True, "pid": pid,
                        "message": "已切换到该实例"}
        except Exception:
            pass

    # 未连接：激活 + 复制命令
    act = _activate_window(pid)
    clip = _set_clipboard(MECH_CONNECT_CMD)
    return {"ok": True, "connected": False, "pid": pid,
            "activated": act, "clipboard": clip,
            "command": MECH_CONNECT_CMD,
            "message": "已激活该 Mechanical 窗口并把命令放进剪贴板，"
                       "在 Mechanical 的 Scripting 输入框里 Ctrl+V 再回车"}

'''

if "def instances_list(" not in src:
    src = src.replace("def zones_preview():", FUNC.strip() + "\n\n\ndef zones_preview():", 1) \
        if "def zones_preview():" in src else src.replace("def scan_groups():", FUNC.strip() + "\n\n\ndef scan_groups():", 1)
    print("已加 instances_list / connect_instance")

ROUTES = '''            if path == "/api/instances":
                return self._json(instances_list())

            if path == "/api/connect":
                body = self._body()
                try:
                    pid = int(body.get("pid") or 0)
                except Exception:
                    pid = 0
                if not pid:
                    return self._json({"ok": False, "error": "缺少 pid"})
                return self._json(connect_instance(pid))

'''
if "/api/instances" not in src:
    anchor = '            if path == "/api/agents":'
    src = src.replace(anchor, ROUTES + anchor, 1)
    print("已加路由 /api/instances、/api/connect")

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
compile(io.open(P, encoding="utf-8").read(), P, "exec")
print("server 语法 OK")
