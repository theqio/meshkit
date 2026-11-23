# -*- coding: utf-8 -*-
"""把脚本扩展打包成 .wbex（供 Workbench「Install Extension」使用）
   结构（ACT 二进制扩展约定）：
        <Name>.xml            扩展清单
        scripts/*.py          入口脚本
        images/*.png          图标
        wbex.xml              包元信息
"""
import io
import os
import zipfile

SRC = os.path.join(os.environ["APPDATA"],
                   r"Ansys\v221\ACT\extensions\MeshKit")
OUT = r"D:\ai\meshkit\dist\MeshKit.wbex"
os.makedirs(os.path.dirname(OUT), exist_ok=True)

WBEX_XML = """<?xml version="1.0" encoding="utf-8"?>
<wbex version="1">
  <name>MeshKit</name>
  <version>1.0.0</version>
  <author>qiyuqiang</author>
  <description>MeshKit: load bridge agent and open the control panel</description>
  <ansysversion>221</ansysversion>
  <platform>winx64</platform>
  <extension>MeshKit.xml</extension>
</wbex>
"""

if os.path.isfile(OUT):
    os.remove(OUT)

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("MeshKit.xml",
               io.open(os.path.join(SRC, "MeshKit.xml"), encoding="utf-8").read())
    z.writestr("wbex.xml", WBEX_XML)
    for rel in ("scripts/meshkit_addin.py",):
        z.writestr(rel, io.open(os.path.join(SRC, rel), encoding="utf-8").read())
    for img in os.listdir(os.path.join(SRC, "images")):
        p = os.path.join(SRC, "images", img)
        if os.path.isfile(p):
            z.write(p, "images/" + img)

print("已生成:", OUT, os.path.getsize(OUT), "字节")
with zipfile.ZipFile(OUT) as z:
    print("\n包内容:")
    for n in z.namelist():
        print("   %-32s %6d 字节" % (n, z.getinfo(n).file_size))
