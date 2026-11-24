# -*- coding: utf-8 -*-
"""批量修复失败体时：建好逐体控制项后，把该体从类别级控制项里摘掉
   （之前只有单个体修复会摘，批量修复漏了 → 尺寸被类别控制项覆盖，一直失败）
"""
import io
import shutil
import time

A = r"D:\ai\meshkit\app\bridge_agent.py"
shutil.copyfile(A, A + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
s = io.open(A, encoding="utf-8-sig").read()

OLD = '''                    ctrl, err = _new_sizing_named(new_name, [bid], 10.0)
                    if ctrl is None:
                        log.append(u"  %s: 新建失败 %s" % (_txt(b.Name)[:30], err))
                    else:
                        log.append(u"  %s: 新建 10 mm" % (_txt(b.Name)[:40]))'''

NEW = '''                    ctrl, err = _new_sizing_named(new_name, [bid], 10.0)
                    if ctrl is None:
                        log.append(u"  %s: 新建失败 %s" % (_txt(b.Name)[:30], err))
                    else:
                        log.append(u"  %s: 新建 10 mm" % (_txt(b.Name)[:40]))
                        # 关键：把它从类别级尺寸/方法控制项里摘掉，
                        # 否则类别控制项仍覆盖该体，局部尺寸不生效（一直失败）
                        try:
                            _rm, _er = _exclude_body_from_categories(
                                bid, _txt(b.Name))
                            if _rm:
                                log.append(u"  %s: 已从 %s 摘除"
                                           % (_txt(b.Name)[:30], u"、".join(_rm[:3])))
                            if _er:
                                log.append(u"  %s: 摘除告警 %s"
                                           % (_txt(b.Name)[:30], u"、".join(_er[:2])))
                        except Exception as _e:
                            log.append(u"  %s: 摘除失败 %s"
                                       % (_txt(b.Name)[:30], _txt(_e)[:60]))'''

if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("批量修复已加摘除")
else:
    print("未找到插入点")

# 已有 SIZE_FIX 的分支也摘一次（尺寸改过的体）
OLD2 = '''                    target.ElementSize = Quantity(u"%.3f [mm]" % newval)
                    log.append(u"  %s: %.1f -> %.1f mm" % (_txt(b.Name)[:40], cur, newval))'''
NEW2 = '''                    target.ElementSize = Quantity(u"%.3f [mm]" % newval)
                    log.append(u"  %s: %.1f -> %.1f mm" % (_txt(b.Name)[:40], cur, newval))
                    try:
                        _rm2, _er2 = _exclude_body_from_categories(
                            bid, _txt(b.Name))
                        if _rm2:
                            log.append(u"  %s: 已从 %s 摘除"
                                       % (_txt(b.Name)[:30], u"、".join(_rm2[:3])))
                    except Exception:
                        pass'''
if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)
    print("已给改尺寸分支加摘除")

io.open(A, "w", encoding="utf-8-sig").write(s)
print("已写入（保留 BOM）")

# 语法用 python3 检查会因 IronPython 语法差异误报，这里只做括号/缩进粗检
try:
    compile(s.replace(u"\r\n", u"\n"), A, "exec")
    print("语法 OK")
except SyntaxError as e:
    print("语法检查（预期可能因 py2 写法差异）:", e)
