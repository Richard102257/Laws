"""地方性法规的地区：名称、在国家法律法规数据库里的制定机关编号，以及命令行里的写法。

另外提供地图展示用的区域划分与相对方位（静态查询页的「地方法规」页和
Web 管理端的覆盖面板共用）。
"""
import re

# 顺序是命令行和 make shell 里的编号，从 1（北京）到 31（新疆）
REGION_CODE_MAP = {
    "北京": 170,
    "天津": 180,
    "河北": 190,
    "山西": 200,
    "内蒙古": 210,
    "辽宁": 220,
    "吉林": 230,
    "黑龙江": 240,
    "上海": 250,
    "江苏": 260,
    "浙江": 270,
    "安徽": 280,
    "福建": 290,
    "江西": 300,
    "山东": 310,
    "河南": 320,
    "湖北": 330,
    "湖南": 340,
    "广东": 350,
    "广西": 360,
    "海南": 370,
    "重庆": 380,
    "四川": 390,
    "贵州": 400,
    "云南": 410,
    "西藏": 420,
    "陕西": 430,
    "甘肃": 440,
    "青海": 450,
    "宁夏": 460,
    "新疆": 470,
}


def dlc_name(region: str) -> str:
    """DLC 目录名：「北京」→「北京地方法规」。"""
    return f"{region}地方法规"


def parse_regions(spec: str) -> list[str]:
    """「7-31」「北京 3,5-8」→ 地区名，去重并保持顺序。编号从 1 开始，顺序同 REGION_CODE_MAP。"""
    names = list(REGION_CODE_MAP)
    spec = re.sub(r"\s*[-~～—]\s*", "-", spec.strip())
    result: list[str] = []
    for part in re.split(r"[,，、\s]+", spec):
        if not part:
            continue
        if part in REGION_CODE_MAP:
            picked = [part]
        elif m := re.fullmatch(r"(\d+)-(\d+)", part):
            start, end = int(m.group(1)), int(m.group(2))
            if not 1 <= start <= end <= len(names):
                raise ValueError(f"范围要在 1-{len(names)} 之间，且从小到大：{part}")
            picked = names[start - 1:end]
        elif part.isdigit() and 1 <= int(part) <= len(names):
            picked = [names[int(part) - 1]]
        else:
            raise ValueError(f"不支持的地区：{part}")
        result += [n for n in picked if n not in result]
    if not result:
        raise ValueError("没有选择地区")
    return result


# ---------- 地图展示 ----------

REGION_ORDER = ["华北", "东北", "华东", "华中", "华南", "西南", "西北"]
REGION_PROVINCES = {
    "华北": ["北京", "天津", "河北", "山西", "内蒙古"],
    "东北": ["辽宁", "吉林", "黑龙江"],
    "华东": ["上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "台湾"],
    "华中": ["河南", "湖北", "湖南"],
    "华南": ["广东", "广西", "海南", "香港", "澳门"],
    "西南": ["重庆", "四川", "贵州", "云南", "西藏"],
    "西北": ["陕西", "甘肃", "青海", "宁夏", "新疆"],
}
PROVINCE_REGION = {province: region for region, items in REGION_PROVINCES.items()
                   for province in items}

# 地图上有方块、但仓库还没有采集数据的省级地区（港澳台）
NO_DATA_PROVINCES = ("香港", "澳门", "台湾")

# 网格大小与省份块的相对方位（列, 行），按中国地图从西北到东南排布。
# 只用方块表示大致位置，不绘制国界与争议地区边界。
MAP_COLS = 8
MAP_ROWS = 7
MAP_PLACEMENT = {
    "新疆": (1, 1), "吉林": (7, 1), "黑龙江": (8, 1),
    "甘肃": (2, 2), "宁夏": (3, 2), "内蒙古": (4, 2),
    "北京": (5, 2), "天津": (6, 2), "辽宁": (7, 2),
    "青海": (2, 3), "陕西": (3, 3), "山西": (4, 3),
    "河北": (5, 3), "山东": (6, 3),
    "西藏": (1, 4), "四川": (2, 4), "重庆": (3, 4), "湖北": (4, 4),
    "河南": (5, 4), "安徽": (6, 4), "江苏": (7, 4), "上海": (8, 4),
    "贵州": (3, 5), "湖南": (4, 5), "江西": (5, 5),
    "福建": (6, 5), "浙江": (7, 5), "台湾": (8, 6),
    "云南": (2, 6), "广西": (3, 6), "广东": (4, 6),
    "海南": (3, 7), "澳门": (4, 7), "香港": (5, 7),
}


def region_of(pack_name: str) -> str:
    """DLC 目录名（``江苏地方法规``）→ 省份名（``江苏``）。"""
    return (pack_name or "").replace("地方法规", "")


def placement_of(pack_name: str) -> tuple[int, int] | None:
    """省份在地图网格里的 (列, 行)，没有对应方块时返回 None。"""
    return MAP_PLACEMENT.get(region_of(pack_name))
