# -*- coding: utf-8 -*-
"""改进预估：薄板按面积×2层算，实体按体积算（原来只用体积，薄板严重低估）"""
import io
import shutil
import time

S = r"D:\ai\meshkit\app\server.py"
shutil.copyfile(S, S + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(S, encoding="utf-8").read()

OLD = '''            vol = 0.0
            for n in names:
                b = bmap.get(n) or {}
                try:
                    vol += float(b.get("vol") or 0)
                except Exception:
                    pass
            if vol <= 0:
                continue
            est = vol / (size ** 3)
            est_total += est
            detail.append({"category": name, "bodies": len(names),
                           "size": size, "volume": round(vol, 1),
                           "est_elements": int(est)})'''

NEW = '''            est = 0.0
            vol = 0.0
            for n in names:
                b = bmap.get(n) or {}
                try:
                    _v = float(b.get("vol") or 0)
                except Exception:
                    _v = 0.0
                vol += _v
                _d = sorted([float(b.get("lx") or 0), float(b.get("ly") or 0),
                             float(b.get("lz") or 0)])
                if _d[0] <= 0:
                    continue
                if _d[0] <= 12:
                    # 薄板：面积 × 厚度方向 2 层
                    est += (_d[1] * _d[2]) / (size * size) * 2.0
                elif _v > 0:
                    est += _v / (size ** 3)
                else:
                    est += (_d[1] * _d[2]) / (size * size) * (_d[0] / size)
            if est <= 0:
                continue
            est_total += est
            detail.append({"category": name, "bodies": len(names),
                           "size": size, "volume": round(vol, 1),
                           "est_elements": int(est)})'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("预估已改为薄板/实体区分")
else:
    print("未找到预估段")

io.open(S, "w", encoding="utf-8", newline="\n").write(s)
compile(io.open(S, encoding="utf-8").read(), S, "exec")
print("语法 OK")
