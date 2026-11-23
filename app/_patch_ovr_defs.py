# -*- coding: utf-8 -*-
"""补上缺失的 _model_key/_ovr_* 定义（上一版只改了调用，没写入定义）"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

HELPER = '''def _model_key():
    """当前目标模型的短标识（工程文件名，去掉扩展名）"""
    try:
        tp = target_pid()
        if tp:
            name, path = _project_of_pid(tp)
            base = name or (os.path.basename(path or "") if path else "")
            base = (base or "").strip()
            if base.lower().endswith(".wbpj"):
                base = base[:-5]
            if base:
                return "".join(c if (c.isalnum() or c in "-_") else "_"
                               for c in base)[:60]
    except Exception:
        pass
    return "default"


def _ovr_path(key=None):
    return os.path.join(APP_DIR, "group_overrides__%s.json" % (key or _model_key()))


def _ovr_load():
    """读当前模型的覆盖表；没有则回退旧全局文件"""
    for p in (_ovr_path(), os.path.join(APP_DIR, "group_overrides.json")):
        try:
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as f:
                    return json.load(f) or {}, p
        except Exception:
            continue
    return {}, None


def _ovr_save(data):
    """写当前模型的覆盖表"""
    try:
        with open(_ovr_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False


'''

if "def _model_key(" not in s:
    anchor = "def merge_custom_groups(groups):"
    if anchor not in s:
        raise SystemExit("找不到 merge_custom_groups")
    s = s.replace(anchor, HELPER + anchor, 1)
    print("已补上 _model_key/_ovr_* 定义")
else:
    print("定义已存在")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
