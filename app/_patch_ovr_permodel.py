# -*- coding: utf-8 -*-
"""人工分组覆盖表改成「按模型分开存」
   - 新文件：app/group_overrides__<工程名>.json
   - 旧全局文件仍可读（仅作为兼容回退，且只接受能解析的键）
"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

HELPER = r'''
def _model_key():
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
                safe = "".join(c if (c.isalnum() or c in "-_") else "_"
                               for c in base)
                return safe[:60]
    except Exception:
        pass
    return "default"


def _ovr_path(key=None):
    return os.path.join(APP_DIR, "group_overrides__%s.json" % (key or _model_key()))


def _ovr_load():
    """读当前模型的覆盖表；没有则回退到旧全局文件"""
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

if "_ovr_load" not in s:
    anchor = "def merge_custom_groups(groups):"
    s = s.replace(anchor, HELPER.strip() + "\n\n\n" + anchor, 1)
    print("已加按模型存取覆盖表的函数")

# 1) merge_custom_groups 读改成 _ovr_load()
OLD1 = '''    try:
        with open(os.path.join(APP_DIR, "group_overrides.json"), encoding="utf-8") as f:
            ov = json.load(f) or {}
    except Exception:
        ov = {}'''
NEW1 = '''    try:
        ov, _ovr_src = _ovr_load()
    except Exception:
        ov = {}'''
if OLD1 in s:
    s = s.replace(OLD1, NEW1, 1)
    print("merge_custom_groups 已改为按模型读取")

# 2) 写入处
i = s.find('p_ov = os.path.join(APP_DIR, "group_overrides.json")')
if i > 0:
    seg_end = s.find("with open(p_ov", i)
    print("写入处上下文:\n", s[max(0,i-300):i+500])
else:
    print("未找到写入处")
