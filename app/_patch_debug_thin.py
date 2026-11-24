# -*- coding: utf-8 -*-
"""临时诊断接口：返回某类别每个体的薄板判定明细（在服务器进程内计算）"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

FUNC = '''
def debug_thin_detail(cat):
    """诊断：某类别内每个体的薄板判定明细"""
    sys.path.insert(0, APP_DIR)
    import geo_recognize
    try:
        import experience as _exp
        _exp_err = None
    except Exception as e:
        _exp = None
        _exp_err = str(e)[:100]
    lb = call_bridge("list_bodies", timeout=300)
    bodies = lb.get("bodies") or []
    rec = geo_recognize.recognize(bodies, kb_extra_rules())
    groups = rec.get("groups") or {}
    names = groups.get(cat) or []
    bmap = {b.get("name"): b for b in bodies}
    out = []
    plate = 0
    for n in names:
        b = bmap.get(n) or {}
        d = sorted([b.get("lx") or 0, b.get("ly") or 0, b.get("lz") or 0])
        if d[0] <= 0:
            continue
        ratio = d[1] / d[0]
        w = None
        if _exp is not None:
            try:
                w = (_exp.features(b) or {}).get("wall")
            except Exception:
                w = None
        is_plate = False
        if d[0] <= 8 and ratio >= 3:
            is_plate = True
        elif ratio >= 3 and d[0] <= 60 and w and w <= 4.5:
            is_plate = True
        if is_plate:
            plate += 1
        out.append({"name": n, "dims": [round(x, 1) for x in d],
                    "ratio": round(ratio, 2), "wall": w,
                    "vol": b.get("vol"), "plate": is_plate})
    return {"ok": True, "cat": cat, "n": len(names), "plate": plate,
            "auto_thin": bool(names and plate * 2 >= len(names)),
            "exp_ok": _exp is not None, "exp_err": _exp_err, "bodies": out}

'''

if "def debug_thin_detail(" not in s:
    s = s.replace("def categories_from_recognition():",
                  FUNC.strip() + "\n\n\ndef categories_from_recognition():", 1)
    print("已加诊断函数")

ROUTE = '''            if path == "/api/debug/thin":
                _q = urllib.parse.urlparse(self.path).query
                _cat = urllib.parse.parse_qs(_q).get("cat", [""])[0]
                return self._json(debug_thin_detail(_cat))

'''
if "/api/debug/thin" not in s:
    anchor = '            if path == "/api/model_key":'
    pos = s.find(anchor, s.find("def do_GET"))
    if pos > 0:
        s = s[:pos] + ROUTE + s[pos:]
        print("已加 /api/debug/thin")
    else:
        print("未找到 do_GET 锚点")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
