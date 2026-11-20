# -*- coding: utf-8 -*-
"""用早期「你手工分组」的正确结果，重建权威规则表（part_rules.json）
   数据来源：2026-11-20 由 derive_rules.py 从你的 Geometry 分组反推的结果
   这些是当时打印出来的真实尺寸组合，不受后续乱改影响
"""
import json

RULES = {
    "垫板": ["8x60x200"],
    "底横梁": ["40x40x2300"],
    "百叶窗口": ["50x100x415"],
    "侧封板": ["46x204x2380"],
    "小三角支撑": ["25x75x75"],
    "侧薄板": ["3x60x210"],
    "中隔断薄板": ["3x2300x2381"],
    "侧固定件": ["50x200x209"],
    "角件": ["118x162x178", "118x178x218"],
    "三角板": ["25x213x213", "25x235x235"],
    "电缆口框": ["50x100x195", "50x100x295"],
    "横梁": ["100x200x2114", "100x150x2194"],
    "纵梁": ["100x150x11868", "100x200x11836"],
    "角支撑": ["209x209x2459", "209x209x3139"],
    "波纹板": ["46x2858x11726", "48x2025x2858"],
    "底板": ["4x2300x2892", "4x2300x2986", "4x2300x2990"],
    "储罐": ["2232x2678x2840", "2232x2778x2934"],
    "内侧波纹板隔板": ["44x690x2300", "44x810x949", "44x810x950"],
    "内隔断框架": ["50x100x2300", "50x50x860", "50x50x999", "120x2300x2388"],
    "上梁": ["73x120x2300", "73x120x1800", "105x185x1752",
             "73x380x1800", "80x140x2300", "73x120x1226"],
    "上支撑梁": ["50x50x100", "10x100x200", "55x80x150",
                 "58x100x100", "3x48x98", "50x100x2594"],
    "其他": ["40x80x2300", "120x140x2300"],
}

rules = {}
for name, sigs in RULES.items():
    rules[name] = {
        "sizes": [{"sig": s, "count": 0, "samples": []} for s in sigs],
        "total": 0,
        "canonical": True,
    }

out = r"D:\ai\meshkit\app\part_rules.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump({"source": "canonical_manual_grouping",
               "note": "由用户手工分组的正确结果建立，勿被自动重建覆盖",
               "rules": rules}, f, ensure_ascii=False, indent=1)

n = sum(len(v["sizes"]) for v in rules.values())
print("权威规则表已重建：%d 个部位，%d 个尺寸档" % (len(rules), n))
for k, v in rules.items():
    print("   %-14s %s" % (k, " + ".join(x["sig"] for x in v["sizes"])))
