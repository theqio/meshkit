# -*- coding: utf-8 -*-
"""test_core_unit.py —— 核心纯函数自动化单元测试（仅依赖 Python 标准库）

覆盖范围：
  1) geo_recognize._match_size（精确/容差/越界/无效条目）
  2) geo_recognize.recognize（朝向板、上梁/横梁/纵梁不串类、侧固定件、储罐、知识包扩展、兜底）
  3) geo_recognize.remap_overrides（互换/链式重命名不互相覆盖）
  4) server.merge_custom_groups（按体 ID、按体名兼容、忽略过期键、保留空自定义组）
  5) server.is_thin_plate（实心薄板、45mm 波纹板壁厚/面积组合判据、空心方梁/实心块不误判、阈值覆盖）
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(ROOT, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import geo_recognize
import server


class TestMatchSize(unittest.TestCase):
    def test_exact_and_nearest_match(self):
        table = [
            ((10.0, 100.0, 200.0), "垫板"),
            ((50.0, 100.0, 200.0), "立柱"),
        ]
        lab, cost = geo_recognize._match_size([10, 100, 200], table)
        self.assertEqual(lab, "垫板")
        self.assertAlmostEqual(cost, 0.0)

        lab2, cost2 = geo_recognize._match_size([11, 105, 210], table)
        self.assertEqual(lab2, "垫板")
        self.assertLessEqual(cost2, 0.15)

    def test_out_of_tolerance_returns_none(self):
        table = [((10.0, 100.0, 200.0), "垫板")]
        lab, cost = geo_recognize._match_size([15, 100, 200], table)
        self.assertIsNone(lab)
        self.assertIsNone(cost)

    def test_zero_or_none_sig_skipped(self):
        table = [(None, "无效"), ((0.0, 100.0, 200.0), "零边"), ((20.0, 100.0, 200.0), "有效")]
        lab, _ = geo_recognize._match_size([20, 100, 200], table)
        self.assertEqual(lab, "有效")


class TestRecognize(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_ov = geo_recognize.OVERRIDE_PATH
        self._orig_cu = geo_recognize.CUSTOM_PATH
        geo_recognize.OVERRIDE_PATH = os.path.join(self._tmp.name, "ov.json")
        geo_recognize.CUSTOM_PATH = os.path.join(self._tmp.name, "cu.json")

    def tearDown(self):
        geo_recognize.OVERRIDE_PATH = self._orig_ov
        geo_recognize.CUSTOM_PATH = self._orig_cu
        self._tmp.cleanup()

    def test_orientation_plates(self):
        bodies = [
            {"name": "p1", "lx": 5, "ly": 1000, "lz": 1200, "cx": 0, "cy": 0, "cz": 0, "vol": 6e6},
            {"name": "p2", "lx": 1000, "ly": 6, "lz": 1200, "cx": 5000, "cy": 0, "cz": 0, "vol": 7.2e6},
        ]
        res = geo_recognize.recognize(bodies)
        self.assertEqual(res["groups"].get("中隔断薄板"), ["p1"])
        self.assertEqual(res["groups"].get("底板"), ["p2"])

    def test_rule2_upper_beam_does_not_steal_longitudinal_beam(self):
        bodies = [
            {"name": "long_1", "lx": 100, "ly": 150, "lz": 11868, "cx": 0, "cy": 1000, "cz": 0},
            {"name": "beam_touch", "lx": 100, "ly": 150, "lz": 2194, "cx": 100, "cy": 1000, "cz": 0},
            {"name": "beam_free", "lx": 100, "ly": 150, "lz": 2194, "cx": 5000, "cy": 1000, "cz": 0},
        ]
        res = geo_recognize.recognize(bodies)
        self.assertIn("beam_touch", res["groups"].get("上梁", []))
        self.assertNotIn("long_1", res["groups"].get("上梁", []))
        self.assertIn("beam_free", res["groups"].get("横梁", []))

    def test_rule4_side_fixture_and_tank_and_extra_rules(self):
        bodies = [
            {"name": "wave_wall", "lx": 45, "ly": 2000, "lz": 3000, "cx": 0, "cy": 0, "cz": 0, "shell": True},
            {"name": "fix_touch", "lx": 50, "ly": 200, "lz": 209, "cx": 48, "cy": 0, "cz": 0},
            {"name": "tank_1", "lx": 2000, "ly": 2000, "lz": 3000, "cx": 8000, "cy": 0, "cz": 0, "vol": 2.4e9},
            {"name": "custom_part", "lx": 77, "ly": 177, "lz": 377, "cx": 20000, "cy": 0, "cz": 0},
            {"name": "mystery", "lx": 13, "ly": 29, "lz": 41, "cx": 30000, "cy": 0, "cz": 0},
        ]
        res = geo_recognize.recognize(bodies, extra={"特制件": ["77x177x377"]})
        self.assertIn("fix_touch", res["groups"].get("侧固定件", []))
        self.assertIn("tank_1", res["groups"].get("储罐", []))
        self.assertIn("custom_part", res["groups"].get("特制件", []))
        self.assertIn("mystery", res["groups"].get("其他", []))

    def test_remap_overrides_swap_safe(self):
        with open(geo_recognize.OVERRIDE_PATH, "w", encoding="utf-8") as f:
            json.dump({"A": "组1", "B": "组2", "C": "组3"}, f, ensure_ascii=False)
        out = geo_recognize.remap_overrides({"A": "B", "B": "A"})
        self.assertEqual(out["moved"], 2)
        with open(geo_recognize.OVERRIDE_PATH, "r", encoding="utf-8") as f:
            after = json.load(f)
        self.assertEqual(after, {"B": "组1", "A": "组2", "C": "组3"})


class TestMergeCustomGroups(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_app_dir = server.APP_DIR
        self._orig_bodies_once = server._bodies_once
        server.APP_DIR = self._tmp.name

    def tearDown(self):
        server.APP_DIR = self._orig_app_dir
        server._bodies_once = self._orig_bodies_once
        self._tmp.cleanup()

    def test_merge_by_id_name_and_empty_custom(self):
        with open(os.path.join(self._tmp.name, "group_overrides.json"), "w", encoding="utf-8") as f:
            json.dump({"101": "新组A", "body_2": "新组B", "9999": "不存在"}, f, ensure_ascii=False)
        with open(os.path.join(self._tmp.name, "custom_groups.json"), "w", encoding="utf-8") as f:
            json.dump(["空自定义组"], f, ensure_ascii=False)
        server._bodies_once = lambda: [
            {"id": 101, "name": "body_1"},
            {"id": 102, "name": "body_2"},
            {"id": 103, "name": "body_3"},
        ]
        merged = server.merge_custom_groups({"原组": ["body_1", "body_2"], "保留组": ["body_3"]})
        self.assertNotIn("原组", merged)
        self.assertEqual(merged.get("新组A"), ["body_1"])
        self.assertEqual(merged.get("新组B"), ["body_2"])
        self.assertEqual(merged.get("保留组"), ["body_3"])
        self.assertEqual(merged.get("空自定义组"), [])


class TestIsThinPlate(unittest.TestCase):
    def test_flat_thin_plate(self):
        b = {"lx": 3, "ly": 500, "lz": 1000, "vol": 3 * 500 * 1000}
        self.assertTrue(server.is_thin_plate(b))

    def test_corrugated_plate_45mm_detected(self):
        # 45mm 波纹板：外形厚 45mm，实际板厚 ~2.0mm（展开系数 1.3），投影面积 1000x2400 mm^2
        b = {"lx": 45, "ly": 1000, "lz": 2400, "vol": 1.3 * 2.0 * 1000 * 2400}
        self.assertTrue(server.is_thin_plate(b, wall=14.7))

    def test_small_corrugated_panel_detected(self):
        b = {"lx": 45, "ly": 300, "lz": 600, "vol": 1.2 * 3.0 * 300 * 600}
        self.assertTrue(server.is_thin_plate(b, wall=12.0))

    def test_hollow_beam_and_solid_block_not_plate(self):
        # 100x150x2200 空心梁（壁厚 6mm）
        beam = {"lx": 100, "ly": 150, "lz": 2200, "vol": 2 * (100 + 150) * 6 * 2200}
        self.assertFalse(server.is_thin_plate(beam, wall=6.0))
        # 50x200x2000 厚壁方管（壁厚 8mm，t_eff=20mm）
        thick_tube = {"lx": 50, "ly": 200, "lz": 2000, "vol": 2 * (50 + 200) * 8 * 2000}
        self.assertFalse(server.is_thin_plate(thick_tube, wall=8.0))
        # 40x200x200 实心垫板
        pad = {"lx": 40, "ly": 200, "lz": 200, "vol": 40 * 200 * 200}
        self.assertFalse(server.is_thin_plate(pad, wall=40.0))

    def test_custom_threshold_override(self):
        b = {"lx": 45, "ly": 1000, "lz": 2400, "vol": 8.0 * 1000 * 2400}
        self.assertFalse(server.is_thin_plate(b))
        self.assertTrue(server.is_thin_plate(b, plate_cfg={"eff_wall_max_mm": 9.0}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
