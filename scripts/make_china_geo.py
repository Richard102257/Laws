"""从阿里 DataV 的中国省级边界生成仓库内置的简化地理数据。

用法（需要联网，一般只在新版边界发布时手动跑一次）：

    python scripts/make_china_geo.py [--src URL] [--tol 0.03]

产物：scripts/lawref/data/china_geo.json，供 lawref.chinamap 渲染地图。
只做抽稀与格式转换，不改变任何边界点，因此不会引入新的边界争议。
"""
from __future__ import annotations

import argparse
import json
import math
import urllib.request
from pathlib import Path

DEFAULT_SRC = "https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json"
OUT = Path(__file__).resolve().parent / "lawref" / "data" / "china_geo.json"

# 全称（DataV name）→ 页面上的简称
SHORT_NAMES = {
    "北京市": "北京", "天津市": "天津", "河北省": "河北", "山西省": "山西",
    "内蒙古自治区": "内蒙古", "辽宁省": "辽宁", "吉林省": "吉林", "黑龙江省": "黑龙江",
    "上海市": "上海", "江苏省": "江苏", "浙江省": "浙江", "安徽省": "安徽",
    "福建省": "福建", "江西省": "江西", "山东省": "山东", "河南省": "河南",
    "湖北省": "湖北", "湖南省": "湖南", "广东省": "广东", "广西壮族自治区": "广西",
    "海南省": "海南", "重庆市": "重庆", "四川省": "四川", "贵州省": "贵州",
    "云南省": "云南", "西藏自治区": "西藏", "陕西省": "陕西", "甘肃省": "甘肃",
    "青海省": "青海", "宁夏回族自治区": "宁夏", "新疆维吾尔自治区": "新疆",
    "台湾省": "台湾", "香港特别行政区": "香港", "澳门特别行政区": "澳门",
}


def simplify_ring(ring: list[list[float]], tol: float) -> list[list[float]]:
    """Douglas-Peucker 抽稀闭合环，返回不含重复收尾点的坐标列表。"""
    pts = [(float(x), float(y)) for x, y in ring]
    while len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    if len(pts) < 4:
        return [[round(x, 3), round(y, 3)] for x, y in pts]

    def dp(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        keep = [False] * len(points)
        keep[0] = keep[-1] = True
        stack = [(0, len(points) - 1)]
        while stack:
            start, end = stack.pop()
            if end <= start + 1:
                continue
            ax, ay = points[start]
            bx, by = points[end]
            dx, dy = bx - ax, by - ay
            length = math.hypot(dx, dy) or 1.0
            best_index, best_dist = -1, 0.0
            for index in range(start + 1, end):
                px, py = points[index]
                dist = abs(dy * (px - ax) - dx * (py - ay)) / length
                if dist > best_dist:
                    best_index, best_dist = index, dist
            if best_dist > tol:
                keep[best_index] = True
                stack.append((start, best_index))
                stack.append((best_index, end))
        return [point for point, flag in zip(points, keep, strict=True) if flag]

    simplified = dp(pts)
    return [[round(x, 3), round(y, 3)] for x, y in simplified]


def simplify_feature(geometry: dict, tol: float) -> list[list[list[float]]]:
    polygons = (geometry["coordinates"] if geometry["type"] == "MultiPolygon"
                else [geometry["coordinates"]])
    rings = []
    for polygon in polygons:
        for ring in polygon:
            simplified = simplify_ring(ring, tol)
            if len(simplified) >= 3:
                rings.append(simplified)
    return rings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default=DEFAULT_SRC)
    parser.add_argument("--tol", type=float, default=0.03, help="抽稀容差（度），默认 0.03")
    args = parser.parse_args()

    with urllib.request.urlopen(args.src, timeout=120) as response:
        raw = json.load(response)

    provinces = []
    nine_dash = []
    for feature in raw["features"]:
        props = feature["properties"]
        adcode = props.get("adcode")
        if adcode == "100000_JD":
            nine_dash.extend(simplify_feature(feature["geometry"], args.tol))
            continue
        name = SHORT_NAMES.get(props.get("name"))
        if not name:
            raise SystemExit(f"未知地区名：{props.get('name')!r}（adcode={adcode}）")
        center = props.get("centroid") or props.get("center")
        if not center:
            raise SystemExit(f"{name} 缺少标注中心")
        provinces.append({
            "code": str(adcode),
            "name": name,
            "center": [round(float(v), 4) for v in center],
            "rings": simplify_feature(feature["geometry"], args.tol),
        })

    provinces.sort(key=lambda item: item["code"])
    payload = {"provinces": provinces, "nine_dash": nine_dash}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")

    points = sum(len(ring) for item in provinces for ring in item["rings"])
    print(f"{len(provinces)} 个省级地区、{points} 个边界点、九段线 {len(nine_dash)} 段")
    print(f"写出 {OUT}（{OUT.stat().st_size / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
