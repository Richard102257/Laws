#!/usr/bin/env python3
"""生成可发布到 GitHub Pages 的静态查询页。

只用标准库。读取仓库里的本体 ``db.sqlite3`` 和 ``DLC/*/db.sqlite3``，把法规
正文（Markdown）渲染成 HTML，生成首页、分类页、每部法规的详情页、法考汇编页
和客户端搜索索引。

用法：
    python3 site/build_site.py                 # 输出到 site/public
    python3 site/build_site.py --out public    # 输出到仓库根的 public/
    python3 site/build_site.py --base /Laws    # 站内链接加上项目页前缀

GitHub Pages 项目页的地址是 https://<用户>.github.io/<仓库>/，链接要带仓库名
前缀；本地用任意静态服务器在根目录预览时不要加 ``--base``。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from markdownlite import render, split_document  # noqa: E402
from lawref.regions import (  # noqa: E402
    MAP_PLACEMENT,
    PROVINCE_REGION,
    REGION_ORDER,
    REGION_PROVINCES,
    region_of,
)

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE.parent
ASSETS = HERE / "assets"

# 现行法规的失效日期默认值，见 scripts/lawref/db/models.py
FOREVER = "2099-12-31"
UNKNOWN_EXPIRY = "1970-01-01"
TODAY = datetime.now(timezone(timedelta(hours=8))).date()

STATUS_LABELS = {
    "current": "现行有效",
    "pending": "尚未施行",
    "revised": "已修订",
    "abolished": "已废止",
    "expired": "已失效",
}

REL_TYPES = ("supersedes", "abolishes", "amends", "implements")
REL_OUT = {
    "supersedes": "本法规替代了",
    "abolishes": "本法规废止了",
    "amends": "本法规修改了",
    "implements": "本法规是下列规定的实施文件",
}
REL_IN = {
    "supersedes": "下列法规替代了本法规",
    "abolishes": "下列法规废止了本法规",
    "amends": "下列法规修改了本法规",
    "implements": "下列规定由本法规实施或细化",
}

GROUP_ORDER = [
    "法律法规",
    "部门规章",
    "司法案例",
    "行政法规",
    "司法解释",
    "法律解释",
    "有关法律问题和重大问题的决定",
    "指导案例",
    "地方法规",
    "其他",
]

# 地方法规页的省份区域划分与地图方位，见 scripts/lawref/regions.py

# 法考汇编名称归一化，与 scripts/lawref/exam.py 的规则一致
_COUNTRY_PREFIX = "中华人民共和国"
_PARENTHETICAL = re.compile(r"[（(][^）)]*[）)]")
_WHITESPACE = re.compile(r"[\s　]")


def normalize_law_name(raw: str) -> str:
    value = raw.replace(_COUNTRY_PREFIX, "")
    value = _PARENTHETICAL.sub("", value)
    return _WHITESPACE.sub("", value)


def parse_date(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def iso(value: date | None) -> str:
    return value.isoformat() if value else ""


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def href(base: str, rel: str) -> str:
    """站内链接：base 为 GitHub 项目页前缀（如 /Laws），本地预览留空。

    只转义 HTML 属性里必须转义的字符，不做 URL 百分号编码——中文和括号在
    路径里浏览器会自己处理，直接输出更短也更易读。
    """
    raw = f"/{rel}" if not base else f"{base.rstrip('/')}/{rel}"
    return raw.replace("&", "&amp;").replace('"', "&quot;")


def asset(base: str, filename: str) -> str:
    """静态资源路径：与 href 相同，单独命名便于一眼看出是 CSS/JS/索引。"""
    return href(base, f"assets/{filename}")


def short_id(text: str, length: int = 12) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


# ---------- 数据模型 ----------


@dataclass
class Pack:
    name: str
    label: str
    root: Path
    db_path: Path
    prefix: str  # 输出目录里区分 DLC 的前缀
    url: str = ""  # 该省/地区的分类汇总页
    province: str = ""  # 去掉「地方法规」后缀的省份名，本体为空
    count: int = 0


@dataclass
class Category:
    pack: str
    folder: str
    name: str
    group: str | None
    order: int | None
    is_sub: bool
    url: str = ""
    count: int = 0


@dataclass
class Law:
    id: str
    name: str
    subtitle: str | None
    filename: str | None
    level: str
    publish: str | None
    valid_from: str | None
    valid_to: str | None
    order: int | None
    ver: int
    flags: int
    pack: str
    pack_label: str
    folder: str
    category_name: str
    group: str | None
    md: Path | None = None
    url: str = ""
    cat_url: str = ""
    status: str = "current"
    shown: bool = False
    rank: int = 0
    prev: str | None = None
    next: str | None = None
    versions: list[str] = field(default_factory=list)

    @property
    def known_valid_to(self) -> date | None:
        value = parse_date(self.valid_to)
        if value and value.year < int(FOREVER[:4]) and value.isoformat() != UNKNOWN_EXPIRY:
            return value
        return None

    def version_key(self) -> tuple[int, int]:
        published = parse_date(self.publish) or parse_date(self.valid_from)
        if published:
            return (published.toordinal(), 1)
        expired = parse_date(self.valid_to)
        if expired and expired <= TODAY:
            return (expired.toordinal(), 0)
        return (-1, 0)

    def rank_key(self) -> tuple:
        published = parse_date(self.publish) or date.min
        return (self.order is None, self.order or 0, self.name, -published.toordinal())


# ---------- 读取数据 ----------


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def load_data(root: Path) -> tuple[list[Pack], list[Law], list[Category], list[dict]]:
    packs: list[Pack] = []
    main_db = root / "db.sqlite3"
    if main_db.is_file():
        packs.append(Pack("main", "本体", root, main_db, ""))
    dlc_dir = root / "DLC"
    if dlc_dir.is_dir():
        for folder in sorted(dlc_dir.iterdir()):
            db = folder / "db.sqlite3"
            if folder.is_dir() and db.is_file():
                packs.append(Pack(folder.name, folder.name, folder, db,
                                  f"dlc/{folder.name}",
                                  province=region_of(folder.name)))

    laws: list[Law] = []
    categories: list[Category] = []
    relationships: list[dict] = []

    for pack in packs:
        conn = sqlite3.connect(f"file:{pack.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            tables = _tables(conn)
            for row in conn.execute(
                    'SELECT id, folder, name, "group", "order", isSubFolder'
                    " FROM category ORDER BY \"order\""):
                categories.append(Category(
                    pack=pack.name, folder=row["folder"], name=row["name"],
                    group=row["group"], order=row["order"],
                    is_sub=bool(row["isSubFolder"]),
                ))
            for row in conn.execute(
                    'SELECT l.id, l.name, l.subtitle, l.filename, l.level, l.publish,'
                    ' l.valid_from, l.valid_to, l."order", l.ver, l.flags,'
                    ' c.folder, c.name AS category_name, c."group", c."order" AS cat_order'
                    " FROM law l JOIN category c ON c.id = l.category_id"):
                md = resolve_md(pack.root, row["folder"], row["filename"], row["name"],
                                row["publish"], row["subtitle"])
                laws.append(Law(
                    id=row["id"], name=row["name"], subtitle=row["subtitle"],
                    filename=row["filename"], level=row["level"], publish=row["publish"],
                    valid_from=row["valid_from"], valid_to=row["valid_to"],
                    order=row["order"], ver=row["ver"] or 0, flags=row["flags"] or 0,
                    pack=pack.name, pack_label=pack.label, folder=row["folder"],
                    category_name=row["category_name"], group=row["group"], md=md,
                ))
            if "relationship" in tables:
                relationships.extend(dict(row) for row in conn.execute(
                    "SELECT source_id, target_id, type, date FROM relationship"))
        finally:
            conn.close()
    return packs, laws, categories, relationships


def resolve_md(root: Path, folder: str, filename: str | None, name: str,
               publish: str | None, subtitle: str | None) -> Path | None:
    """和 App 的 TLaw.getNames 一致的正文文件查找顺序。"""
    names: list[str] = []
    if filename:
        names.append(filename)
    elif publish:
        names.append(f"{name}({str(publish)[:10]})")
    names.extend([name, subtitle])
    folder_path = root / folder if folder else root
    for stem in dict.fromkeys(n for n in names if n):
        candidate = folder_path / f"{stem}.md"
        if candidate.is_file():
            return candidate
    return None


# ---------- 计算 ----------


def assign_urls(laws: list[Law], categories: list[Category]) -> None:
    used: set[str] = set()
    by_folder = {(category.pack, category.folder): category for category in categories}
    for category in categories:
        category.url = f"cat/{short_id(f'{category.pack}|{category.folder}')}.html"

    for law in laws:
        category = by_folder.get((law.pack, law.folder))
        law.cat_url = category.url if category else "catalog/index.html"
        directory = short_id(f"{law.pack}|{law.cat_url}")
        law.url = f"law/{directory}/{law.id}.html"
        if law.url in used:
            law.url = f"law/{directory}/{law.id}-2.html"
        used.add(law.url)


def compute_states(laws: list[Law], relationships: list[dict]) -> None:
    id_map = {law.id: law for law in laws}
    supersedes = {r["source_id"]: r["target_id"] for r in relationships
                  if r["type"] == "supersedes"}
    abolishes = {r["target_id"]: r["source_id"] for r in relationships
                 if r["type"] == "abolishes"}
    superseded_ids = set(supersedes.values())

    def root(law_id: str) -> str:
        seen = {law_id}
        while law_id in supersedes and supersedes[law_id] not in seen:
            law_id = supersedes[law_id]
            seen.add(law_id)
        return law_id

    groups: dict[str, list[Law]] = {}
    for law in laws:
        key = f"{law.pack}#{(root(law.id) if law.id in supersedes or law.id in superseded_ids else law.folder + ':' + law.name)}"
        groups.setdefault(key, []).append(law)

    for key, versions in groups.items():
        versions.sort(key=lambda item: item.version_key(), reverse=True)
        listed = [item for item in versions if _listed(item)]
        # 只有一部且已过失效日的，不列出；同一谱系里还有可列版本时正常列出。
        shown = listed[0] if listed else (None if len(versions) == 1 else versions[0])
        for law in versions:
            law.versions = [item.id for item in versions]
        for law in versions:
            law.shown = bool(shown) and law.id == shown.id
            superseded = law.id in superseded_ids
            abolisher = abolishes.get(law.id)
            law.status = law_status(law, superseded, abolisher is not None)

    for law in laws:
        if law.md is None:
            law.shown = False

    # 分类里的排序与上一部 / 下一部（只算列出的版本）
    by_category: dict[tuple[str, str], list[Law]] = {}
    for law in laws:
        by_category.setdefault((law.pack, law.folder), []).append(law)
    for items in by_category.values():
        items.sort(key=lambda item: item.rank_key())
        for rank, law in enumerate(items):
            law.rank = rank
        listed = [law for law in items if law.shown]
        for index, law in enumerate(listed):
            law.prev = listed[index - 1].url if index > 0 else None
            law.next = listed[index + 1].url if index + 1 < len(listed) else None


def _pending(law: Law) -> bool:
    value = parse_date(law.valid_from)
    return value is not None and value > TODAY


def _listed(law: Law) -> bool:
    """是否列在分类里：现行、未施行都列出；真正过了失效日的版本不列出。

    失效日记成 1970-01-01 表示「已失效但不知道具体日期」（见 models.UNKNOWN_EXPIRY），
    这种要列出，否则这类法规会整部消失。
    """
    if _pending(law):
        return True
    valid_to = law.known_valid_to
    return valid_to is None or valid_to > TODAY


def law_status(law: Law, superseded: bool, has_abolisher: bool) -> str:
    valid_to = parse_date(law.valid_to)
    if valid_to is not None and valid_to <= TODAY:
        if superseded:
            return "revised"
        if has_abolisher:
            return "abolished"
        return "expired"
    if _pending(law):
        return "pending"
    return "current"


def build_relations(laws: list[Law], relationships: list[dict]) -> dict[str, dict]:
    id_map = {law.id: law for law in laws}
    outgoing: dict[str, list[dict]] = {}
    incoming: dict[str, list[dict]] = {}
    for row in relationships:
        outgoing.setdefault(row["source_id"], []).append(row)
        incoming.setdefault(row["target_id"], []).append(row)

    result: dict[str, dict] = {}
    for law in laws:
        out_groups: dict[str, list[tuple[Law, str]]] = {}
        for row in outgoing.get(law.id, []):
            target = id_map.get(row["target_id"])
            if target:
                out_groups.setdefault(row["type"], []).append((target, row.get("date") or ""))
        in_counts: dict[str, int] = {}
        in_items: dict[str, list[tuple[Law, str]]] = {}
        for row in incoming.get(law.id, []):
            source = id_map.get(row["source_id"])
            in_counts[row["type"]] = in_counts.get(row["type"], 0) + 1
            if source:
                in_items.setdefault(row["type"], []).append((source, row.get("date") or ""))
        result[law.id] = {"out": out_groups, "in_counts": in_counts, "in_items": in_items}
    return result


def catalog_groups(categories: list[Category]) -> list[tuple[str, list[Category]]]:
    buckets: dict[str, list[Category]] = {}
    for category in categories:
        group = category.group or category.folder.split("/")[0]
        buckets.setdefault(group, []).append(category)
    ordered = sorted(
        buckets.items(),
        key=lambda item: (GROUP_ORDER.index(item[0]) if item[0] in GROUP_ORDER
                          else len(GROUP_ORDER), item[0]),
    )
    return ordered


def build_stats(laws: list[Law], categories: list[Category]) -> dict:
    listed = [law for law in laws if law.shown]
    by_level: dict[str, int] = {}
    for law in listed:
        level = "地方性法规" if law.pack != "main" else law.level
        by_level[level] = by_level.get(level, 0) + 1
    return {
        "total": len(listed),
        "by_level": dict(sorted(by_level.items(), key=lambda item: -item[1])),
        "categories": sum(1 for c in categories if c.count),
        "updated": TODAY.isoformat(),
        "versions": len(laws),
    }


def build_feed(laws: list[Law]) -> tuple[list[Law], list[Law]]:
    by_lineage: dict[str, list[Law]] = {}
    for law in laws:
        by_lineage.setdefault(tuple(law.versions), []).append(law)

    recent: list[Law] = []
    upcoming: list[Law] = []
    seen: set[str] = set()
    for versions in by_lineage.values():
        ordered = sorted(versions, key=lambda item: item.version_key(), reverse=True)
        latest = ordered[0]
        if latest.id in seen or not latest.shown:
            continue
        seen.add(latest.id)
        published = parse_date(latest.publish)
        if _pending(latest) and latest.valid_from:
            upcoming.append(latest)
        elif (published and len(ordered) > 1 and published >= TODAY - timedelta(days=180)
              and latest.status != "expired"):
            recent.append(latest)
    recent.sort(key=lambda item: (parse_date(item.publish) or date.min,), reverse=True)
    upcoming.sort(key=lambda item: (parse_date(item.valid_from) or date.max,))
    return recent, upcoming


# ---------- 页面模板 ----------

def icon(name: str) -> str:
    """CSS 数据 URI 图标（样式里定义），避免每个页面重复内联 SVG。"""
    return f'<span class="icon" style="--icon: var(--i-{name})" aria-hidden="true"></span>'


ICON_SEARCH = icon("search")
ICON_SUN = icon("sun")
ICON_MOON = icon("moon")
ICON_STAR = icon("star")
ICON_LINK = icon("link")
ICON_PRINT = icon("print")
ICON_UP = icon("up")

THEME_SCRIPT = """(function () {
  try {
    var t = localStorage.getItem('laws_theme') || 'system';
    var d = t === 'dark' || (t === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.dataset.theme = d ? 'dark' : 'light';
  } catch (e) {
    document.documentElement.dataset.theme = 'light';
  }
})();"""


def shell(base: str, title: str, body: str, *, description: str = "",
          body_class: str = "") -> str:
    home = href(base, "index.html")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="icon" href="{asset(base, 'favicon.svg')}">
<script>
{THEME_SCRIPT}
</script>
<link rel="stylesheet" href="{asset(base, 'style.css')}">
</head>
<body data-base="{esc(base)}" class="{esc(body_class)}">
<header class="topbar">
  <div class="topbar-inner">
    <a class="brand" href="{home}"><span class="brand-mark">⚖</span>法律快查<small>Laws</small></a>
    <nav class="topnav">
      <a href="{href(base, 'catalog/index.html')}">分类浏览</a>
      <a href="{href(base, 'region/index.html')}">地方法规</a>
      <a href="{href(base, 'exam/index.html')}">法考汇编</a>
    </nav>
    <div class="topbar-spacer"></div>
    <form class="search search--top" role="search" action="{home}">
      {ICON_SEARCH}
      <input id="search-top" name="q" type="search" placeholder="搜索法规…" autocomplete="off">
    </form>
    <button class="icon-btn" type="button" data-theme-toggle aria-label="切换主题">{ICON_SUN}{ICON_MOON}</button>
  </div>
</header>
{body}
<footer>
  <div class="container">
    <span>法律快查 · 数据来自公开法律法规数据库</span>
    <span>更新：{TODAY.isoformat()}</span>
    <a href="https://github.com/Richard102257/Laws">源码仓库</a>
  </div>
</footer>
<button class="back-top" type="button" data-back-top aria-label="返回顶部">{ICON_UP}</button>
<script src="{asset(base, 'app.js')}"></script>
</body>
</html>
"""


def law_row(base: str, law: Law) -> str:
    status = law.status
    return (
        f'<a class="law-row" href="{href(base, law.url)}" data-status="{status}" data-filter-item>'
        f'<span class="dot-status {status}"></span>'
        f'<span class="title">{esc(law.name)}</span>'
        f'<span class="meta">{esc(law.publish or "")}</span>'
        f'<span class="badge badge--{status}">{STATUS_LABELS[status]}</span>'
        "</a>"
    )


def paginated_rows(base: str, laws: list[Law]) -> str:
    return "\n".join(law_row(base, law) for law in laws)


# ---------- 各页面 ----------

def render_home(base: str, packs: list[Pack], laws: list[Law], categories: list[Category],
                stats: dict, recent: list[Law], upcoming: list[Law]) -> str:
    groups = catalog_groups(categories)
    group_totals = {
        name: sum(c.count for c in items) for name, items in groups
    }
    cards = []
    wanted = ["法律法规", "部门规章", "司法案例", "行政法规", "司法解释", "指导案例", "地方法规", "其他"]
    descriptions = {
        "法律法规": "宪法、民法典、刑法等基本法律",
        "部门规章": "国务院各部委规章",
        "司法案例": "典型案例与司法案例",
        "行政法规": "国务院制定的行政法规",
        "司法解释": "最高法、最高检司法解释",
        "指导案例": "最高法、最高检及司法行政指导案例",
        "地方法规": "31 个省级行政区地方性法规",
        "其他": "法律解释、决定与相关资料",
    }
    local_count = len([pack for pack in packs if pack.province])
    for name in wanted:
        if name not in group_totals or group_totals[name] == 0:
            continue
        if name == "地方法规":
            cards.append(
                f'<a class="card card--map" href="{href(base, "region/index.html")}">'
                f'<span class="card-title"><span class="dot"></span>{esc(name)}</span>'
                f'<span class="card-count">{group_totals[name]} 部 · {local_count} 省级地区</span>'
                f'<span class="card-desc">{esc(descriptions.get(name, ""))}</span></a>'
            )
            continue
        anchor = "group-" + str(GROUP_ORDER.index(name))
        cards.append(
            f'<a class="card" href="{href(base, "catalog/index.html")}#{anchor}">'
            f'<span class="card-title"><span class="dot"></span>{esc(name)}</span>'
            f'<span class="card-count">{group_totals[name]} 部</span>'
            f'<span class="card-desc">{esc(descriptions.get(name, ""))}</span></a>'
        )
    cards.append(
        f'<a class="card" href="{href(base, "exam/index.html")}">'
        f'<span class="card-title"><span class="dot"></span>法考汇编</span>'
        f'<span class="card-count">8 组 · 267 条</span>'
        f'<span class="card-desc">按法考科目整理的法规清单</span></a>'
    )

    def feed_rows(items: list[Law], label: str) -> str:
        if not items:
            return f'<p class="empty">{label}暂无内容</p>'
        return "\n".join(
            f'<a class="row" href="{href(base, law.url)}">'
            f'<span class="row-main"><span class="row-title">{esc(law.name)}</span>'
            f'<span class="row-sub">{esc(law.level)}{(" · " + law.publish) if law.publish else ""}</span></span>'
            f'<span class="row-date">{esc(label)}</span></a>'
            for law in items[:12]
        )

    body = f"""
<main>
  <section class="hero">
    <h1>中国法律快查手册</h1>
    <p>收录法律、行政法规、司法解释、部门规章、地方法规与指导案例，按分类浏览，关键词秒搜。</p>
    <form class="search search--hero" role="search" action="{href(base, "index.html")}">
      {ICON_SEARCH}
      <input id="search-input" name="q" type="search" placeholder="输入法规名称、关键词，如「劳动合同」「刑法」…" autocomplete="off">
      <button class="btn btn--primary search-go" type="submit">搜索</button>
    </form>
    <div class="search-results" id="search-results"></div>
    <div class="hero-stats">
      <span><strong>{stats["total"]:,}</strong>收录法规</span>
      <span><strong>{stats["categories"]}</strong>分类</span>
      <span><strong>{len(packs)}</strong>数据包</span>
      <span><strong>{stats["updated"]}</strong>更新日期</span>
    </div>
  </section>

  <section class="section container">
    <div class="fav-panel" id="fav-panel">
      <div class="section-head"><h2>我的收藏</h2></div>
      <div class="row-list" id="fav-list"></div>
    </div>
  </section>

  <section class="section container">
    <div class="section-head"><h2>快速入口</h2><a class="more" href="{href(base, "catalog/index.html")}">全部分类 →</a></div>
    <div class="card-grid">{''.join(cards)}</div>
  </section>

  <section class="section container two-col">
    <div>
      <div class="section-head"><h2>近期修订</h2><a class="more" href="{href(base, "catalog/index.html")}">更多 →</a></div>
      <div class="row-list">{feed_rows(recent, "修订")}</div>
    </div>
    <div>
      <div class="section-head"><h2>即将施行</h2><a class="more" href="{href(base, "catalog/index.html")}">更多 →</a></div>
      <div class="row-list">{feed_rows(upcoming, "施行")}</div>
    </div>
  </section>
</main>
"""
    return shell(base, "中国法律快查手册", body,
                 description="收录中国法律、行政法规、司法解释、部门规章、地方性法规与指导案例，支持分类浏览和关键词搜索。")


def catalog_tree(base: str, items: list[Category]) -> str:
    nodes: dict[str, dict] = {}

    def node_for(folder: str) -> dict:
        if folder not in nodes:
            parent = folder.rsplit("/", 1)[0] if "/" in folder else None
            node = {"segment": folder.rsplit("/", 1)[-1], "folder": folder,
                    "category": None, "children": {}, "order": None}
            nodes[folder] = node
            if parent:
                node_for(parent)["children"][folder] = node
        return nodes[folder]

    for category in items:
        node = node_for(category.folder)
        node["category"] = category
        if category.order is not None:
            node["order"] = (min(node["order"], category.order)
                             if node["order"] is not None else category.order)
    roots = [node for folder, node in nodes.items()
             if "/" not in folder or folder.rsplit("/", 1)[0] not in nodes]

    # 先递归算出每个节点的总收录数（含子分类）
    def compute_count(node: dict) -> int:
        total = node["category"].count if node["category"] else 0
        for child in node["children"].values():
            total += compute_count(child)
        node["count"] = total
        return total

    for root in roots:
        compute_count(root)

    def render(node: dict, depth: int) -> str:
        category = node["category"]
        if category:
            row = (f'<a class="catalog-node" href="{href(base, category.url)}" data-filter-item>'
                   f'<span class="name">{esc(category.name)}</span>'
                   f'<span class="folder">{esc(category.folder)}</span>'
                   f'<span class="count badge badge--count">{category.count}</span></a>')
        else:
            row = (f'<div class="catalog-node" data-filter-item>'
                   f'<span class="name">{esc(node["segment"])}</span>'
                   f'<span class="count badge badge--count">{node["count"]}</span></div>')
        children = "".join(render(child, depth + 1)
                           for _, child in sorted(node["children"].items(),
                                                  key=lambda pair: (pair[1]["order"] is None,
                                                                    pair[1]["order"] or 0,
                                                                    pair[0])))
        if children:
            row += f'<div class="catalog-children">{children}</div>'
        return row

    return "".join(render(root, 0) for root in roots)


def render_catalog(base: str, categories: list[Category], laws: list[Law]) -> str:
    groups = catalog_groups(categories)
    sections = []
    for name, items in groups:
        total = sum(c.count for c in items)
        anchor = "group-" + str(GROUP_ORDER.index(name)) if name in GROUP_ORDER else "group-other"
        sections.append(
            f'<section class="catalog-group" id="{anchor}" data-filter-group>'
            f'<h2>{esc(name)} <span class="badge badge--count">{total}</span></h2>'
            f'<div class="catalog-tree">{catalog_tree(base, items)}</div></section>'
        )
    body = f"""
<main class="container">
  <div class="page-head">
    <h1>分类浏览</h1>
    <p class="desc">按分组和目录浏览全部收录法规。</p>
  </div>
  <div class="filter-bar" data-filter-scope>
    <div class="search">{ICON_SEARCH}<input data-filter type="search" placeholder="筛选分类，如「教育部」「检察」…"></div>
    <div class="chip-row">
      <a class="chip" href="{href(base, "index.html")}">返回首页</a>
      <a class="chip" href="{href(base, "exam/index.html")}">法考汇编</a>
    </div>
  </div>
  {''.join(sections)}
</main>
"""
    return shell(base, "分类浏览 · 中国法律快查手册", body)


def render_region(base: str, packs: list[Pack], categories: list[Category],
                  laws: list[Law]) -> str:
    """地方法规页：按相对方位排布的省份块地图 + 区域列表，点省直达该省分类页。"""
    by_province = {pack.province: pack for pack in packs if pack.province}
    cells = []
    for province, (column, row) in MAP_PLACEMENT.items():
        pack = by_province.get(province)
        if pack is None:
            continue
        cells.append(
            f'<a class="province" href="{href(base, pack.url)}" '
            f'style="grid-column:{column};grid-row:{row}" data-filter-item>'
            f'<span class="province-name">{esc(province)}</span>'
            f'<span class="province-count">{pack.count} 部</span></a>'
        )

    sections = []
    for region in REGION_ORDER:
        items = [by_province[name] for name in REGION_PROVINCES[region]
                 if name in by_province]
        if not items:
            continue
        rows = "\n".join(
            f'<a class="law-row" href="{href(base, pack.url)}" data-filter-item>'
            f'<span class="dot-status current"></span>'
            f'<span class="title">{esc(pack.province)}</span>'
            f'<span class="meta">{esc(region)}</span>'
            f'<span class="badge badge--count">{pack.count} 部</span></a>'
            for pack in sorted(items, key=lambda item: -item.count)
        )
        sections.append(f'<section class="region-group"><h2>{esc(region)}</h2>'
                        f'<div class="law-list">{rows}</div></section>')

    local_total = sum(pack.count for pack in by_province.values())
    body = f"""
<main class="container">
  <nav class="crumbs">
    <a href="{href(base, "index.html")}">首页</a><span class="sep"></span>
    <span>地方法规</span>
  </nav>
  <div class="page-head">
    <h1>地方法规</h1>
    <p class="desc">{len(by_province)} 个省级行政区 · {local_total} 部现行有效地方性法规与规章。
    按相对方位排布的省份块，点击即可进入该省的分类页；方块只表示大致位置，不绘制边界。</p>
  </div>
  <div class="filter-bar" data-filter-scope>
    <div class="search">{ICON_SEARCH}<input data-filter type="search" placeholder="筛选省份，如「江苏」「内蒙古」…"></div>
    <div class="chip-row">
      <a class="chip" href="{href(base, "catalog/index.html")}">分类浏览</a>
    </div>
  </div>
  <div class="map-wrap">
    <div class="map-grid">{"".join(cells)}</div>
    <p class="map-note">地图为方位示意，不代表实际面积与边界。</p>
  </div>
  {''.join(sections)}
</main>
"""
    return shell(base, "地方法规 · 中国法律快查手册", body)


def render_region_pack(base: str, pack: Pack, categories: list[Category],
                       laws: list[Law]) -> str:
    """某个省/地区的分类汇总页。"""
    items = [category for category in categories if category.pack == pack.name and category.count]
    items.sort(key=lambda category: (category.order is None, category.order or 0, category.folder))
    listed = sum(1 for law in laws if law.shown and law.pack == pack.name)
    region = PROVINCE_REGION.get(pack.province, "")
    body = f"""
<main class="container">
  <nav class="crumbs">
    <a href="{href(base, "index.html")}">首页</a><span class="sep"></span>
    <a href="{href(base, "region/index.html")}">地方法规</a><span class="sep"></span>
    <span>{esc(pack.province)}</span>
  </nav>
  <div class="page-head">
    <h1>{esc(pack.label)}</h1>
    <p class="desc">{esc(region)} · 共 {listed} 部，{len(items)} 个分类。</p>
  </div>
  <div class="filter-bar" data-filter-scope>
    <div class="search">{ICON_SEARCH}<input data-filter type="search" placeholder="筛选分类…"></div>
    <div class="chip-row">
      <a class="chip" href="{href(base, "region/index.html")}">返回地方法规</a>
      <a class="chip" href="{href(base, "catalog/index.html")}">分类浏览</a>
    </div>
  </div>
  <div class="catalog-tree">{catalog_tree(base, items) if items else '<p class="empty">这个地区还没有收录法规</p>'}</div>
</main>
"""
    return shell(base, f"{pack.label} · 中国法律快查手册", body)


def render_category(base: str, category: Category, laws: list[Law]) -> str:
    rows = [law for law in laws if law.shown and law.pack == category.pack
            and law.folder == category.folder]
    rows.sort(key=lambda item: item.rank_key())
    levels = {law.level for law in rows}
    statuses = {law.status for law in rows}
    chips = ['<button class="chip active" type="button" data-status-filter="">全部 '
             f'({len(rows)})</button>']
    for status in ("current", "pending", "revised", "abolished", "expired"):
        if status not in statuses:
            continue
        count = sum(1 for law in rows if law.status == status)
        chips.append(f'<button class="chip" type="button" data-status-filter="{status}">'
                     f'{STATUS_LABELS[status]} ({count})</button>')
    body = f"""
<main class="container">
  <nav class="crumbs">
    <a href="{href(base, "index.html")}">首页</a><span class="sep"></span>
    <a href="{href(base, "catalog/index.html")}">全部分类</a><span class="sep"></span>
    <span>{esc(category.name)}</span>
  </nav>
  <div class="page-head">
    <h1>{esc(category.name)}</h1>
    <p class="desc">{esc(category.folder)} · 共 {len(rows)} 部{(" · " + "、".join(sorted(levels))) if levels else ""}</p>
  </div>
  <div class="filter-bar" data-status-scope>
    <div class="search">{ICON_SEARCH}<input type="search" placeholder="在本分类内筛选…" data-filter></div>
    <div class="chip-row">{''.join(chips)}</div>
  </div>
  <div class="law-list" data-category-list>
    {paginated_rows(base, rows) if rows else '<p class="empty">这个分类还没有收录法规</p>'}
  </div>
</main>
"""
    return shell(base, f"{category.name} · 中国法律快查手册", body)


def _article_index_html(entries: list[dict]) -> str:
    """详情页侧栏的条标索引：全部「第 N 条」+ 摘要，可按字词过滤。"""
    rows = []
    for entry in entries:
        summary = f' <span class="art-summary">{esc(entry["text"])}</span>' if entry["text"] else ""
        rows.append(
            f'<li data-filter-item data-keywords="{esc(entry.get("keywords") or entry["label"])}">'
            f'<a href="#{esc(entry["id"])}" data-article-link>'
            f'<span class="art-label">{esc(entry["label"])}</span>{summary}</a></li>'
        )
    return (
        '<div class="art-index" data-filter-scope>'
        f'<p class="toc-title">条标索引 <span class="badge badge--count">{len(entries)}</span></p>'
        f'<div class="search search--mini">{ICON_SEARCH}'
        '<input type="search" data-article-filter placeholder="按条号或字词过滤，如「20」「正当防卫」…"></div>'
        f'<ul class="art-index-list">{"".join(rows)}</ul></div>'
    )


def _version_label(law: Law) -> str:
    if law.publish:
        return law.publish
    if law.valid_from:
        return law.valid_from + " 施行"
    return law.name


def _relation_html(base: str, law: Law, relations: dict) -> str:
    info = relations.get(law.id)
    if not info:
        return ""
    parts: list[str] = []
    for rel_type in REL_TYPES:
        items = info["out"].get(rel_type) or []
        if not items:
            continue
        links = "\n".join(
            f'<li><a href="{href(base, target.url)}">{esc(target.name)}</a>'
            f'<span class="rel-type">{esc(rel_type)}</span>'
            + (f'<span class="rel-type">{esc(when)}</span>' if when else "")
            + "</li>"
            for target, when in items[:60]
        )
        parts.append(f'<div class="related-group"><div class="rel-title">'
                     f'{esc(REL_OUT[rel_type])}（{len(items)}）</div><ul>{links}</ul></div>')
    for rel_type in REL_TYPES:
        count = info["in_counts"].get(rel_type, 0)
        if not count:
            continue
        items = info["in_items"].get(rel_type) or []
        links = "\n".join(
            f'<li><a href="{href(base, source.url)}">{esc(source.name)}</a>'
            f'<span class="rel-type">{esc(rel_type)}</span></li>'
            for source, _ in items[:6]
        )
        parts.append(f'<div class="related-group"><div class="rel-title">'
                     f'{esc(REL_IN[rel_type])}（{count}）</div><ul>{links}</ul></div>')
    if not parts:
        return ""
    return f'<section class="related"><h2>相关法规</h2>{"".join(parts)}</section>'


def render_law(base: str, law: Law, by_id: dict[str, Law], by_url: dict[str, Law],
               relations: dict) -> str:
    if law.md is None:
        body_html = "<p>这部法规的正文文件缺失。</p>"
        info_html = ""
        toc: list[dict] = []
        article_index: list[dict] = []
    else:
        text = law.md.read_text(encoding="utf-8", errors="replace")
        _title, info, body = split_document(text)
        rendered = render(body)
        info_rendered = render(info)
        body_html = rendered.html
        info_html = info_rendered.html
        toc = rendered.toc
        article_index = rendered.article_index

    id_map = by_id
    versions = [id_map[vid] for vid in law.versions if vid in id_map]
    version_bar = ""
    if len(versions) > 1:
        links = []
        for item in versions:
            current = item.id == law.id
            links.append(
                f'<a class="version-link{" current" if current else ""}" '
                f'href="{href(base, item.url)}">{_version_label(item)}</a>'
            )
        version_bar = (f'<div class="version-bar"><span class="label">版本（{len(versions)}）</span>'
                       + "".join(links) + "</div>")

    toc_html = ""
    if toc or article_index:
        blocks: list[str] = []
        jump = (f'<form class="jump" data-jump-form><input type="text" inputmode="numeric" '
                f'placeholder="第几条" aria-label="跳到第几条"><button class="btn" type="submit">跳转</button>'
                f'<span class="jump-msg" data-jump-msg hidden></span></form>')
        if toc:
            items = "".join(
                f'<li><a class="lvl-{entry["level"]}" href="#{entry["id"]}" data-toc-link>'
                f'{esc(entry["text"])}</a></li>'
                for entry in toc
            )
            blocks.append(f'<p class="toc-title">本页目录</p>{jump}<ul class="toc-list">{items}</ul>')
        elif article_index:
            blocks.append(jump)
        if article_index:
            blocks.append(_article_index_html(article_index))
        toc_html = f'<aside class="toc">{"".join(blocks)}</aside>'

    info_block = f'<div class="law-info">{info_html}</div>' if info_html else ""

    dates = []
    if law.publish:
        dates.append(f"公布 {law.publish}")
    if law.valid_from:
        dates.append(f"施行 {law.valid_from}")
    if law.known_valid_to:
        dates.append(f"失效 {iso(law.known_valid_to)}")
    if law.subtitle:
        dates.insert(0, law.subtitle)

    prevnext = ""
    if law.prev or law.next:
        links = []
        if law.prev:
            prev = by_url.get(law.prev)
            label = esc(prev.name) if prev else "上一部"
            links.append(f'<a href="{href(base, law.prev)}"><span class="label">上一部</span>{label}</a>')
        if law.next:
            nxt = by_url.get(law.next)
            label = esc(nxt.name) if nxt else "下一部"
            links.append(f'<a href="{href(base, law.next)}"><span class="label">下一部</span>{label}</a>')
        prevnext = f'<div class="law-foot-nav"><div class="prevnext">{"".join(links)}</div></div>'

    body = f"""
<main class="container">
  <nav class="crumbs">
    <a href="{href(base, "index.html")}">首页</a><span class="sep"></span>
    <a href="{href(base, "catalog/index.html")}">全部分类</a><span class="sep"></span>
    <a href="{href(base, law.cat_url)}">{esc(law.category_name)}</a><span class="sep"></span>
    <span>{esc(law.name)}</span>
  </nav>
  <div class="law-layout">
    {toc_html}
    <article class="law">
      <header class="law-head">
        <div class="law-status">
          <span class="badge badge--{law.status}">{STATUS_LABELS[law.status]}</span>
          <span class="badge badge--level">{esc(law.level)}</span>
          {f'<span class="badge badge--pack">{esc(law.pack_label)}</span>' if law.pack != "main" else ""}
          <span>{" · ".join(dates)}</span>
        </div>
        <h1 class="law-title">{esc(law.name)}</h1>
        {f'<p class="law-subtitle">{esc(law.subtitle)}</p>' if law.subtitle else ""}
        {info_block}
        <div class="law-toolbar">
          <button class="btn" type="button" data-fav data-name="{esc(law.name)}" data-pub="{esc(law.publish or "")}" aria-pressed="false">{ICON_STAR}<span data-fav-label>收藏</span></button>
          <button class="btn" type="button" data-copy>{ICON_LINK}<span data-copy-label>复制链接</span></button>
          <button class="btn font-a font-a--sm" type="button" data-font-step="-1" aria-label="减小字号">A</button>
          <button class="btn font-a font-a--lg" type="button" data-font-step="1" aria-label="增大字号">A</button>
          <button class="btn" type="button" data-print>{ICON_PRINT}打印</button>
        </div>
        {version_bar}
      </header>
      <div class="law-body" id="law-body">{body_html}</div>
      {prevnext}
      {_relation_html(base, law, relations)}
    </article>
  </div>
</main>
"""
    return shell(base, f"{law.name} · 中国法律快查手册", body,
                 description=f"{law.name}（{law.level}，{STATUS_LABELS[law.status]}）全文。")


def load_exam(root: Path, laws: list[Law]) -> tuple[list[dict], dict]:
    """读取法考汇编结构并匹配本地法规。返回 (groups, overview)。"""
    structure = root / "法考汇编" / "law_exam_structure.json"
    if not structure.is_file():
        return [], {"group_count": 0, "entry_count": 0, "matched": 0, "unmatched": 0}

    payload = json.loads(structure.read_text(encoding="utf-8"))
    corpus = [law for law in laws if law.pack == "main" and law.md is not None]
    by_name: dict[str, list[Law]] = {}
    by_norm: dict[str, list[Law]] = {}
    for law in corpus:
        by_name.setdefault(law.name, []).append(law)
        by_norm.setdefault(normalize_law_name(law.name), []).append(law)

    def pick(items: list[Law]) -> Law:
        return sorted(items, key=lambda item: (
            0 if (item.valid_to or "") == FOREVER else 1,
            -(item.ver or 0),
            item.order if item.order is not None else 0,
            item.name,
        ))[0]

    overrides: dict[str, tuple[str | None, str]] = {}
    main_db = root / "db.sqlite3"
    if main_db.is_file():
        conn = sqlite3.connect(f"file:{main_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            if "law_exam_entry" in _tables(conn):
                for row in conn.execute(
                        "SELECT e.code, e.law_id, e.match_mode, g.code AS group_code"
                        " FROM law_exam_entry e JOIN law_exam_group g ON g.id = e.group_id"):
                    overrides[(row["group_code"], row["code"])] = (
                        row["law_id"], row["match_mode"] or "unmatched")
        finally:
            conn.close()

    id_map = {law.id: law for law in laws}
    groups: list[dict] = []
    matched = unmatched = 0
    for index, group in enumerate(payload.get("groups") or [], start=1):
        entries_out = []
        for order, item in enumerate(group.get("laws") or [], start=1):
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            law_id, mode = overrides.get((str(group.get("code")), str(item.get("code"))), (None, ""))
            if law_id:
                target = id_map.get(law_id)
            else:
                target = pick(by_name[name]) if by_name.get(name) else (
                    pick(by_norm[normalize_law_name(name)]) if by_norm.get(normalize_law_name(name)) else None)
            is_matched = bool(target)
            matched += is_matched
            unmatched += not is_matched
            entries_out.append({
                "name": name, "law": target, "matched": is_matched,
                "mode": mode or ("manual" if law_id else "auto"),
            })
        groups.append({
            "name": str(group.get("name") or ""),
            "order": int(group.get("order") or index),
            "entries": entries_out,
        })
    groups.sort(key=lambda item: item["order"])
    overview = {
        "group_count": len(groups),
        "entry_count": matched + unmatched,
        "matched": matched,
        "unmatched": unmatched,
    }
    return groups, overview


def render_exam(base: str, groups: list[dict], overview: dict) -> str:
    if not groups:
        body = """
<main class="container"><div class="page-head"><h1>法考汇编</h1>
<p class="desc">仓库里没有找到法考汇编结构文件。</p></div></main>
"""
        return shell(base, "法考汇编 · 中国法律快查手册", body)

    sections = []
    for index, group in enumerate(groups):
        entries = group["entries"]
        matched = sum(1 for entry in entries if entry["matched"])
        rows = []
        for entry in entries:
            law = entry["law"]
            if law is not None:
                rows.append(
                    f'<li class="exam-entry matched" data-filter-item>'
                    f'<span class="entry-name">{esc(entry["name"])}</span>'
                    f'<a class="entry-law" href="{href(base, law.url)}">{esc(law.name)}'
                    f'{(" · " + law.publish) if law.publish else ""}</a></li>'
                )
            else:
                rows.append(
                    f'<li class="exam-entry unmatched" data-filter-item>'
                    f'<span class="entry-name">{esc(entry["name"])}</span>'
                    f'<span class="badge badge--count">未收录</span></li>'
                )
        open_attr = " open" if index == 0 else ""
        sections.append(
            f'<details class="exam-group" data-filter-group{open_attr}>'
            f'<summary><span class="caret">▸</span><span>{esc(group["name"])}</span>'
            f'<span class="exam-group-meta">{matched}/{len(entries)} 已匹配</span></summary>'
            f'<ul class="exam-entries">{"".join(rows)}</ul></details>'
        )

    body = f"""
<main class="container">
  <div class="page-head">
    <h1>法考汇编</h1>
    <p class="desc">对照国家统一法律职业资格考试主观题机考法规检索库整理的分组清单，点击已匹配条目查看本地收录的法规全文。</p>
    <div class="exam-stats">
      <span><strong>{overview["group_count"]}</strong>分组</span>
      <span><strong>{overview["entry_count"]}</strong>条目</span>
      <span><strong>{overview["matched"]}</strong>已匹配</span>
      <span><strong>{overview["unmatched"]}</strong>未收录</span>
    </div>
  </div>
  <div class="filter-bar" data-filter-scope>
    <div class="search">{ICON_SEARCH}<input data-filter type="search" placeholder="筛选分组或法规名…"></div>
    <div class="chip-row">
      <a class="chip" href="{href(base, "catalog/index.html")}">分类浏览</a>
    </div>
  </div>
  {''.join(sections)}
</main>
"""
    return shell(base, "法考汇编 · 中国法律快查手册", body)


# ---------- 构建 ----------


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def search_entries(base: str, laws: list[Law]) -> list[dict]:
    entries = []
    for law in laws:
        if not law.shown:
            continue
        entries.append({
            "i": law.id,
            "n": law.name,
            "s": law.subtitle or "",
            "l": law.level,
            "p": law.publish or "",
            "f": law.valid_from or "",
            "t": iso(law.known_valid_to),
            "st": law.status,
            "g": law.group or law.folder.split("/")[0],
            "c": law.category_name,
            "d": law.folder,
            "k": law.pack_label,
            "u": href(base, law.url),
        })
    return entries


def build(root: Path, out: Path, base: str = "", source: str = "", with_sitemap: bool = True) -> dict:
    packs, laws, categories, relationships = load_data(root)

    assign_urls(laws, categories)
    for pack in packs:
        if not pack.url:
            pack.url = f"region/{short_id(f'pack|{pack.name}')}/index.html"
    compute_states(laws, relationships)
    relations = build_relations(laws, relationships)

    for category in categories:
        category.count = sum(1 for law in laws
                             if law.shown and law.pack == category.pack
                             and law.folder == category.folder)
    for pack in packs:
        pack.count = sum(1 for law in laws if law.shown and law.pack == pack.name)

    stats = build_stats(laws, categories)
    recent, upcoming = build_feed(laws)
    groups, exam_overview = load_exam(root, laws)

    if out.exists():
        if out.resolve() in (root.resolve(), HERE.resolve()):
            raise SystemExit(f"拒绝覆盖目录：{out}")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    write_file(out / "index.html", render_home(base, packs, laws, categories, stats, recent, upcoming))
    write_file(out / "catalog/index.html", render_catalog(base, categories, laws))
    for category in categories:
        write_file(out / category.url, render_category(base, category, laws))
    write_file(out / "exam/index.html", render_exam(base, groups, exam_overview))
    write_file(out / "region/index.html", render_region(base, packs, categories, laws))
    for pack in packs:
        if pack.province:
            write_file(out / pack.url, render_region_pack(base, pack, categories, laws))

    missing = [law for law in laws if law.md is None]
    by_id = {law.id: law for law in laws}
    by_url = {law.url: law for law in laws}
    for law in laws:
        write_file(out / law.url, render_law(base, law, by_id, by_url, relations))

    assets = out / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ASSETS / "style.css", assets / "style.css")
    shutil.copyfile(ASSETS / "app.js", assets / "app.js")
    favicon = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
               '<rect width="64" height="64" rx="14" fill="#c8102e"/>'
               '<text x="32" y="44" font-size="36" text-anchor="middle" fill="#fff">⚖</text></svg>')
    write_file(assets / "favicon.svg", favicon)

    entries = search_entries(base, laws)
    write_file(assets / "search-index.json",
               json.dumps(entries, ensure_ascii=False, separators=(",", ":")))

    if with_sitemap:
        urls = [href(base, "index.html"), href(base, "catalog/index.html"),
                href(base, "exam/index.html"), href(base, "region/index.html")]
        urls += [href(base, c.url) for c in categories]
        urls += [href(base, p.url) for p in packs if p.province]
        urls += [href(base, law.url) for law in laws]
        today = TODAY.isoformat()
        xml = ['<?xml version="1.0" encoding="UTF-8"?>',
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for url in urls:
            xml.append(f"<url><loc>{url}</loc><lastmod>{today}</lastmod></url>")
        xml.append("</urlset>")
        write_file(out / "sitemap.xml", "\n".join(xml))

    write_file(out / "robots.txt", "User-agent: *\nAllow: /\nSitemap: "
               + href(base, "sitemap.xml") + "\n")

    return {
        "packs": len(packs),
        "laws": len(laws),
        "listed": len([law for law in laws if law.shown]),
        "missing": len(missing),
        "categories": len(categories),
        "exam": exam_overview,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成静态法规查询页（GitHub Pages）")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="Laws 仓库根目录（默认脚本上一级）")
    parser.add_argument("--out", type=Path, default=HERE / "public",
                        help="输出目录（默认 site/public）")
    parser.add_argument("--base", default="", help="站内链接前缀，GitHub 项目页填 /仓库名")
    parser.add_argument("--source", default="", help="保留参数，未使用")
    parser.add_argument("--no-sitemap", action="store_true", help="不生成 sitemap.xml")
    args = parser.parse_args(argv)

    summary = build(args.root, args.out, args.base, args.source, not args.no_sitemap)
    print(f"数据包 {summary['packs']} 个，法规 {summary['laws']} 条（列出 "
          f"{summary['listed']} 部），分类 {summary['categories']} 个，"
          f"缺正文 {summary['missing']} 条", file=sys.stderr)
    if summary["exam"]["entry_count"]:
        exam = summary["exam"]
        print(f"法考汇编：{exam['group_count']} 组 / {exam['entry_count']} 条，"
              f"已匹配 {exam['matched']}", file=sys.stderr)
    print(f"输出：{args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())