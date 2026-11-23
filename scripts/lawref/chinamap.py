"""把仓库内置的中国省级边界渲染成可交互 SVG 地图。

查询页（site/build_site.py）与 Web 管理端共用这一个渲染器：
同一份简化边界、同一套配色与图例，避免两端各画一张地图。

采用阿尔伯斯等积圆锥投影，主图放大陆与海南/台湾，南海诸岛按国内标准地图
惯例放在右下角小图（含九段线）。数据来源见 scripts/make_china_geo.py；
边界点原样保留，只做抽稀。
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from functools import lru_cache
from html import escape
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent / "data" / "china_geo.json"

# 阿尔伯斯等积圆锥投影：标准纬线 27°/45°，中央经线 105°，原点 36°
_PHI1 = math.radians(27.0)
_PHI2 = math.radians(45.0)
_PHI0 = math.radians(36.0)
_LAMBDA0 = math.radians(105.0)
_N = (math.sin(_PHI1) + math.sin(_PHI2)) / 2
_C = math.cos(_PHI1) ** 2 + 2 * _N * math.sin(_PHI1)
_RHO0 = math.sqrt(_C - 2 * _N * math.sin(_PHI0)) / _N

WORLD_WIDTH = 920.0          # 全部要素投影后的世界坐标宽度（像素）
MAIN_CUTOFF_LAT = 17.5       # 低于该纬度的小岛归入南海诸岛小图
MARGIN = 8.0

# 数量分档的蓝色梯度；0（暂无数据）单独用灰色
PALETTE = ["#edf3fa", "#d7e7f8", "#aed3f1", "#7db3e6", "#3a83cd"]
EMPTY_FILL = "#eef0f3"
EMPTY_STROKE = "#c3cad4"


def _project(lon: float, lat: float) -> tuple[float, float]:
    lam = math.radians(lon)
    phi = math.radians(lat)
    rho = math.sqrt(max(_C - 2 * _N * math.sin(phi), 0.0)) / _N
    theta = _N * (lam - _LAMBDA0)
    return rho * math.sin(theta), _RHO0 - rho * math.cos(theta)


def _bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


@lru_cache(maxsize=1)
def _geometry() -> dict:
    """投影后的世界坐标：主图与南海诸岛小图各一套视图框。"""
    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    provinces = []
    for item in raw["provinces"]:
        pairs = [([_project(x, y) for x, y in ring], ring)
                 for ring in item["rings"]]
        main_rings = [proj for proj, source in pairs
                      if sum(point[1] for point in source) / len(source) >= MAIN_CUTOFF_LAT]
        south_rings = [proj for proj, source in pairs
                       if sum(point[1] for point in source) / len(source) < MAIN_CUTOFF_LAT]
        provinces.append({
            "code": item["code"],
            "name": item["name"],
            "center": _project(item["center"][0], item["center"][1]),
            "main": main_rings,
            "south": south_rings,
        })
    nine_dash = [[_project(x, y) for x, y in ring] for ring in raw["nine_dash"]]

    everything = ([p for prov in provinces for ring in prov["main"] + prov["south"]
                   for p in ring]
                  + [p for ring in nine_dash for p in ring])
    world = _bbox(everything)
    assert world
    scale = WORLD_WIDTH / (world[2] - world[0])

    def to_world(point: tuple[float, float]) -> tuple[float, float]:
        return ((point[0] - world[0]) * scale, (world[3] - point[1]) * scale)

    main_points = [to_world(p) for prov in provinces for ring in prov["main"] for p in ring]
    south_points = ([to_world(p) for prov in provinces for ring in prov["south"] for p in ring]
                    + [to_world(p) for ring in nine_dash for p in ring])
    main_box = _bbox(main_points)
    south_box = _bbox(south_points)

    for prov in provinces:
        prov["center"] = to_world(prov["center"])
        prov["main"] = [[to_world(p) for p in ring] for ring in prov["main"]]
        prov["south"] = [[to_world(p) for p in ring] for ring in prov["south"]]
        box = _bbox([p for ring in prov["main"] for p in ring])
        assert box
        prov["box"] = box

    return {
        "provinces": provinces,
        "nine_dash": [[to_world(p) for p in ring] for ring in nine_dash],
        "main": main_box,
        "south": south_box,
    }


def build_scale(counts: Mapping[str, int]) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """按数量把有数据的省份分成最多 5 档，返回（省份→填充色, 图例）。"""
    values = sorted({int(value) for value in counts.values() if value > 0})
    if not values:
        return {}, [("暂无数据", EMPTY_FILL)]

    cuts: list[int] = []
    if len(values) >= 5:
        for index in range(1, 5):
            cuts.append(values[min(len(values) - 1, index * len(values) // 5)])

    def bucket(count: int) -> int:
        for index, cut in enumerate(cuts):
            if count <= cut:
                return index
        return len(cuts)

    fills = {
        name: PALETTE[bucket(int(count))]
        for name, count in counts.items() if count > 0
    }

    legend: list[tuple[str, str]] = [("暂无数据", EMPTY_FILL)]
    lower = 0
    for index, color in enumerate(PALETTE):
        group = [value for value in values if bucket(value) == index]
        if not group:
            continue
        upper = cuts[index] if index < len(cuts) else None
        label = f"{lower + 1}–{upper}" if upper is not None else f"≥{lower + 1}"
        legend.append((label, color))
        if upper is not None:
            lower = upper
    return fills, legend


def _path_data(rings: list[list[tuple[float, float]]], box=None,
               pad: float = 0.0) -> str:
    parts = []
    for ring in rings:
        if len(ring) < 3:
            continue
        points = [(round(x - box[0] + pad, 1), round(y - box[1] + pad, 1))
                  for x, y in ring] if box else [(round(x, 1), round(y, 1))
                                                 for x, y in ring]
        parts.append(f"M{points[0][0]} {points[0][1]} "
                     + " ".join(f"L{x} {y}" for x, y in points[1:]) + "Z")
    return "".join(parts)


def render_map(counts: Mapping[str, int], *,
               links: Mapping[str, str] | None = None,
               actions: Mapping[str, Mapping[str, str]] | None = None,
               with_inset: bool = True) -> str:
    """渲染中国地图（主图 + 南海诸岛小图）。

    ``counts``：省份名 → 法规数；``links``：省份名 → 链接（静态查询页）；
    ``actions``：省份名 → 挂到地块上的 data 属性（管理端点击筛选）。
    """
    geo = _geometry()
    provinces: list[dict] = geo["provinces"]
    main_box = geo["main"]
    south_box = geo["south"]
    assert main_box
    width = round(main_box[2] - main_box[0] + MARGIN * 2, 1)
    height = round(main_box[3] - main_box[1] + MARGIN * 2, 1)

    fills, _ = build_scale(counts)
    links = links or {}
    actions = actions or {}

    groups = []
    for prov in provinces:
        name = prov["name"]
        count = int(counts.get(name, 0))
        fill = fills.get(name, EMPTY_FILL)
        box = prov["box"]
        big = (box[2] - box[0]) >= 46 and (box[3] - box[1]) >= 30
        attrs = [
            f'class="cn-prov{" cn-prov--empty" if not count else ""}"',
            f'data-code="{escape(prov["code"], quote=True)}"',
            f'data-name="{escape(name, quote=True)}"',
            f'data-count="{count}"',
            'data-filter-item',
        ]
        for key, value in actions.get(name, {}).items():
            attrs.append(f'{key}="{escape(str(value), quote=True)}"')
        inner = [f'<path d="{_path_data(prov["main"], main_box, MARGIN)}" fill="{fill}"/>']
        cx = round(prov["center"][0] - main_box[0] + MARGIN, 1)
        cy = round(prov["center"][1] - main_box[1] + MARGIN, 1)
        if count and big:
            inner.append(f'<text class="cn-count" x="{cx}" y="{cy + 14}">{count}</text>')
        # 太小的小块（如澳门）不标名字，避免与相邻省份的标注叠在一起
        small_side = min(box[2] - box[0], box[3] - box[1])
        if small_side >= 8:
            label_class = " cn-name--sm" if small_side < 32 else ""
            inner.append(f'<text class="cn-name{label_class}" x="{cx}" y="{cy}">'
                         f'{escape(name)}</text>')
        title = f"{name}：{count} 部" if count else f"{name}：暂无数据"
        content = f"<title>{escape(title)}</title>" + "".join(inner)
        if links.get(name):
            content = f'<a href="{escape(links[name], quote=True)}">{content}</a>'
        groups.append(f"<g {' '.join(attrs)}>{content}</g>")

    main_svg = (
        f'<svg class="cn-map" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="中国省级地区覆盖图" '
        f'xmlns="http://www.w3.org/2000/svg">{"".join(groups)}</svg>'
    )

    inset = ""
    if with_inset and south_box:
        south_width = round(south_box[2] - south_box[0] + 8, 1)
        south_height = round(south_box[3] - south_box[1] + 8, 1)
        south_paths = []
        for prov in provinces:
            if not prov["south"]:
                continue
            fill = fills.get(prov["name"], EMPTY_FILL)
            south_paths.append(
                f'<path d="{_path_data(prov["south"], south_box, 4)}" fill="{fill}"/>'
            )
        jd = "".join(f'<path d="{_path_data([ring], south_box, 4)}"/>'
                     for ring in geo["nine_dash"])
        inset = (
            '<aside class="cn-inset" aria-hidden="true">'
            '<span class="cn-inset-title">南海诸岛</span>'
            f'<svg class="cn-map-south" viewBox="0 0 {south_width} {south_height}" '
            f'xmlns="http://www.w3.org/2000/svg">{"".join(south_paths)}'
            f'<g class="cn-jd">{jd}</g></svg></aside>'
        )

    return f'<div class="cn-map-wrap">{main_svg}{inset}</div>'


def render_legend(counts: Mapping[str, int]) -> str:
    _, legend = build_scale(counts)
    items = "".join(
        f'<li><span class="sw" style="background:{color}"></span>{escape(label)}</li>'
        for label, color in legend
    )
    return f'<ul class="cn-legend">{items}</ul>'
