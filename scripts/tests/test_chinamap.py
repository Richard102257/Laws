"""中国地图渲染：几何数据完整、着色分档与 SVG 输出。"""
import re
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from lawref.chinamap import build_scale, render_legend, render_map  # noqa: E402


class ChinaMapTest(unittest.TestCase):

    def test_geometry_covers_all_provinces(self):
        from lawref.chinamap import _geometry
        geo = _geometry()
        self.assertEqual(len(geo["provinces"]), 34)
        for prov in geo["provinces"]:
            self.assertTrue(prov["main"], f"{prov['name']} 主图没有边界")
            self.assertGreater(prov["box"][2], prov["box"][0])
            self.assertGreater(prov["box"][3], prov["box"][1])
        main_w = geo["main"][2] - geo["main"][0]
        main_h = geo["main"][3] - geo["main"][1]
        self.assertGreater(main_w, 700)
        self.assertLess(main_h / main_w, 1.1, "主图应接近横版")

    def test_render_map_has_every_province(self):
        counts = {"江苏": 1500, "北京": 80}
        svg = render_map(counts, links={"江苏": "/Laws/region/jiangsu/index.html"})
        names = re.findall(r'data-name="([^"]+)"', svg)
        self.assertEqual(len(names), 34)
        self.assertEqual(set(names), set(PROVINCE_NAMES))
        self.assertIn('href="/Laws/region/jiangsu/index.html"', svg)
        self.assertEqual(svg.count("cn-prov--empty"), 32)
        self.assertIn("南海诸岛", svg)
        self.assertNotIn("nan", svg.lower())

    def test_scale_and_legend(self):
        fills, legend = build_scale({"甲": 10, "乙": 20, "丙": 30})
        self.assertEqual(fills["甲"], fills["乙"])
        self.assertIn(("暂无数据", "#eef0f3"), legend)
        html = render_legend({})
        self.assertIn("暂无数据", html)


PROVINCE_NAMES = [
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江", "上海", "江苏",
    "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "广西",
    "海南", "重庆", "四川", "贵州", "云南", "西藏", "陕西", "甘肃", "青海", "宁夏",
    "新疆", "台湾", "香港", "澳门",
]


if __name__ == "__main__":
    unittest.main()
