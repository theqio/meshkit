# -*- coding: utf-8 -*-
"""把本次会话已验证的经验灌入经验库（作为初始积累）"""
import sys

sys.path.insert(0, r"D:\ai\meshkit\app")
import experience as E

MODEL = "测试版(集装箱)"
cases = [
    # ---- 尺寸降至 2.5~3x 壁厚：成功 ----
    ({"wall": 3.42, "fill": 0.06, "aspect": 52.3, "hollow": True,
      "dims": [45.5, 204, 2380]}, "mesh_failed",
     {"type": "size", "value": 10.0, "note": "降到 2.5×壁厚"}, "fixed",
     "2_中_侧封板_204x2380x46_04", "侧封板", "3.4mm 壁厚，原尺寸 25mm 划不出"),
    ({"wall": 3.76, "fill": 0.09, "aspect": 4.2, "hollow": True,
      "dims": [49.7, 200, 209]}, "mesh_failed",
     {"type": "size", "value": 10.0}, "fixed",
     "2_中_侧板固定件_209x200x50_01", "侧板固定件", ""),
    ({"wall": 7.2, "fill": 0.31, "aspect": 9.4, "hollow": True,
      "dims": [25.0, 235, 235]}, "mesh_failed",
     {"type": "size", "value": 10.0}, "fixed",
     "2_中_板_235x25x235_02", "板", ""),
    # ---- 极薄壁长梁：降尺寸无效 ----
    ({"wall": 2.2, "fill": 0.048, "aspect": 16.7, "hollow": True,
      "dims": [105.0, 185, 1752]}, "mesh_failed",
     {"type": "size", "value": 2.0}, "no_effect",
     "3_上_纵梁_1752x105x185_01", "异形纵梁", "10/5/2mm 全失败，降尺寸无效"),
    ({"wall": 2.2, "fill": 0.048, "aspect": 16.7, "hollow": True,
      "dims": [105.0, 185, 1752]}, "mesh_failed",
     {"type": "defeature", "value": 2.5, "note": "去特征 2.5mm 后成功"}, "fixed",
     "3_上_纵梁_1752x105x185_01", "异形纵梁", "有突出块+0.0016mm 退化边"),
    # ---- 扫掠：柱类不能设层数 ----
    ({"wall": 3.5, "fill": 0.05, "aspect": 13.3, "hollow": True,
      "dims": [231.0, 250, 3070]}, "mesh_failed",
     {"type": "sweep", "divisions": 2}, "no_effect",
     "2_中_立柱_231x3070x250_01", "角柱", "层数=2 等于沿 3 米只分 2 段"),
    ({"wall": 3.5, "fill": 0.05, "aspect": 13.3, "hollow": True,
      "dims": [231.0, 250, 3070]}, "mesh_failed",
     {"type": "sweep", "auto_thin": True}, "fixed",
     "2_中_立柱_231x3070x250_01", "角柱", "Sweep + Automatic Thin，不设层数"),
    # ---- 框架类扫掠+Automatic Thin 成功 ----
    ({"wall": 2.83, "fill": 0.11, "aspect": 57.5, "hollow": True,
      "dims": [40.0, 40, 2300]}, "mesh_failed",
     {"type": "sweep", "auto_thin": True}, "fixed",
     "1_下_横梁_40x40x2300_01", "底部横梁", "规整截面，扫掠成功"),
    ({"wall": 3.22, "fill": 0.15, "aspect": 8.3, "hollow": True,
      "dims": [50.0, 100, 415]}, "mesh_failed",
     {"type": "sweep", "auto_thin": True}, "fixed",
     "2_中_百叶窗框_415x50x100_01", "窗框架", ""),
    # ---- 薄板扫掠 2 层 ----
    ({"wall": 8.0, "fill": 0.92, "aspect": 3.3, "hollow": False,
      "dims": [8.0, 60, 200]}, "quality_low",
     {"type": "sweep", "divisions": 2, "note": "薄板扫掠 2 层"}, "fixed",
     "1_下_垫板_60x8x200_01", "垫板", "薄板用扫掠 2 层，单元规整"),
]

n = 0
for feat, prob, act, out, body, grp, note in cases:
    E.add_case(feat, prob, act, out, body=body, group=grp, model=MODEL, note=note)
    n += 1
print("已写入 %d 条经验" % n)
print("统计:", E.stats())
