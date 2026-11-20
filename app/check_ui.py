# -*- coding: utf-8 -*-
"""check_ui.py —— 界面改动后的强制校验（每次改 index.html 后必须运行）

检查项：
  1) JS 语法（node --check）
  2) HTML 里引用的所有函数（onclick/onchange/oninput）都必须在 JS 里有定义
  3) HTML 里 $('id') 用到的 id 都必须在页面里存在（或允许动态生成）
  4) 无头浏览器真实加载：无 console 错误、关键元素状态正确
任一失败 → 退出码 1，并打印具体缺什么
"""
import io
import os
import re
import subprocess
import sys
import tempfile

HTML = r"D:\ai\meshkit\app\static\index.html"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
NODE = r"C:\Users\qiyuqiang\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
URL = "http://127.0.0.1:8765"

fails = []


def read():
    return io.open(HTML, encoding="utf-8").read()


def get_js(src):
    m = re.search(r"<script>(.*?)</script>", src, re.S)
    return m.group(1) if m else ""


def defined_names(js):
    names = set()
    names |= set(re.findall(r"function\s+([A-Za-z_$][\w$]*)", js))
    names |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
                            r"(?:async\s*)?(?:function|\()", js))
    names |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", js))
    # 对象属性形式
    names |= set(re.findall(r"^\s*([A-Za-z_$][\w$]*)\s*[:=]\s*(?:async\s*)?function",
                            js, re.M))
    return names


def used_handlers(src):
    """onclick="fn(...)" 这类内联事件里调用的函数名"""
    used = set()
    for attr in ("onclick", "onchange", "oninput", "onkeydown", "onblur"):
        for m in re.finditer(attr + r'="([^"]+)"', src):
            body = m.group(1)
            for fn in re.findall(r"([A-Za-z_$][\w$]*)\s*\(", body):
                if fn in ("if", "return", "confirm", "alert", "encodeURIComponent",
                          "parseInt", "parseFloat", "String", "Number", "Math"):
                    continue
                used.add(fn)
    return used


def ids_in_html(src):
    return set(re.findall(r'id="([^"]+)"', src))


def ids_used(js):
    return set(re.findall(r"\$\('([^']+)'\)", js)) | \
           set(re.findall(r'\$\("([^"]+)"\)', js))


def main():
    src = read()
    js = get_js(src)
    if not js:
        fails.append("找不到 <script> 内容")
        report()
        return

    # 1) JS 语法
    tmp = os.path.join(tempfile.gettempdir(), "_check_ui.js")
    io.open(tmp, "w", encoding="utf-8").write(js)
    r = subprocess.run([NODE, "--check", tmp], capture_output=True)
    if r.returncode != 0:
        fails.append("JS 语法错误:\n" + r.stderr.decode("utf-8", "replace")[:800])

    # 2) 内联事件函数是否存在
    defined = defined_names(js)
    html_part = src.split("<script>")[0]
    used = used_handlers(html_part)
    builtin = {"confirm", "alert", "console", "JSON", "Object", "Array",
               "location", "window", "document", "event", "this"}
    missing = sorted(f for f in used if f not in defined and f not in builtin)
    if missing:
        fails.append("以下函数被 HTML 调用但未定义: " + ", ".join(missing))

    # 3) $('id') 是否都在 HTML 里
    html_ids = ids_in_html(src)
    # 动态生成的 id（模板字符串里的）先放行
    dyn = set(re.findall(r"id=\"([^\"$]+)\"", src))
    missing_ids = sorted(i for i in ids_used(js)
                         if i not in html_ids and i not in dyn
                         and "{" not in i)
    if missing_ids:
        fails.append("以下 id 被 JS 使用但 HTML 里不存在: " + ", ".join(missing_ids))

    # 4) 浏览器真实加载
    log = os.path.join(tempfile.gettempdir(), "_check_ui_console.log")
    dom = os.path.join(tempfile.gettempdir(), "_check_ui_dom.html")
    for p in (log, dom):
        if os.path.exists(p):
            os.remove(p)
    try:
        with open(dom, "w", encoding="utf-8") as f:
            subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                            "--user-data-dir=" + os.path.join(tempfile.gettempdir(), "_chk_ui"),
                            "--enable-logging=stderr", "--v=1",
                            "--virtual-time-budget=15000",
                            "--dump-dom", URL + "/?v=check"],
                           stdout=f, stderr=open(log, "w"), timeout=120)
    except Exception as e:
        fails.append("浏览器加载失败: %s" % e)

    if os.path.exists(log):
        txt = io.open(log, encoding="utf-8", errors="replace").read()
        errs = [ln for ln in txt.splitlines() if "Uncaught" in ln or "ERROR:CONSOLE" in ln]
        if errs:
            fails.append("浏览器控制台报错:\n" + "\n".join(errs[:5]))

    if os.path.exists(dom):
        d = io.open(dom, encoding="utf-8", errors="replace").read()
        m = re.search(r'id="bridgeText">([^<]*)<', d)
        state = m.group(1) if m else "?"
        if "检测中" in state:
            fails.append("页面仍显示「检测中…」→ 初始化未完成（bridgeText=%s）" % state)

    report()


def report():
    if fails:
        print("=" * 70)
        print("校验失败 %d 项：" % len(fails))
        for i, f in enumerate(fails, 1):
            print("\n[%d] %s" % (i, f))
        print("=" * 70)
        sys.exit(1)
    print("✓ 校验通过：JS 语法正常、事件函数齐全、id 完整、页面无报错")


if __name__ == "__main__":
    main()
