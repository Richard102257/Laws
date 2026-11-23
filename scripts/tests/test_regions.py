"""地区与地图布局测试。"""
import unittest

from lawref.regions import (
    MAP_COLS,
    MAP_PLACEMENT,
    MAP_ROWS,
    REGION_CODE_MAP,
    REGION_PROVINCES,
    dlc_name,
    parse_regions,
    region_of,
)


class ParseRegionsTest(unittest.TestCase):

    def test_range(self):
        regions = parse_regions("7-31")
        self.assertEqual(regions, list(REGION_CODE_MAP)[6:])
        self.assertEqual((regions[0], regions[-1], len(regions)), ("吉林", "新疆", 25))
        self.assertEqual(parse_regions("7 - 9"), ["吉林", "黑龙江", "上海"])

    def test_mixed_and_dedup(self):
        self.assertEqual(parse_regions("北京 3，5-6、5 北京"), ["北京", "河北", "内蒙古", "辽宁"])
        self.assertEqual(parse_regions("31"), ["新疆"])

    def test_invalid(self):
        for spec in ("0", "32", "9-7", "1-40", "火星", "", " , "):
            with self.assertRaises(ValueError, msg=spec):
                parse_regions(spec)


class MapPlacementTest(unittest.TestCase):

    def test_every_province_has_one_cell(self):
        provinces = [name for items in REGION_PROVINCES.values() for name in items]
        self.assertEqual(len(provinces), 31)
        self.assertEqual(set(MAP_PLACEMENT), set(provinces))
        cells = list(MAP_PLACEMENT.values())
        self.assertEqual(len(set(cells)), len(cells), "地图格子不能重叠")

    def test_cells_within_grid(self):
        for column, row in MAP_PLACEMENT.values():
            self.assertTrue(1 <= column <= MAP_COLS)
            self.assertTrue(1 <= row <= MAP_ROWS)

    def test_region_of_dlc_name(self):
        self.assertEqual(region_of(dlc_name("江苏")), "江苏")
        self.assertEqual(region_of("main"), "main")


if __name__ == "__main__":
    unittest.main()
