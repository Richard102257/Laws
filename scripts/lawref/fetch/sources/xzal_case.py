"""司法部的指导案例：https://www.moj.gov.cn/

一页发布一批（类）案例，每案一个文件：

- 行政复议各批次：``指导案例/行政复议/<批次>/名称(日期).md``
- 其他司法行政工作：``指导案例/司法行政/<类别>/名称(日期).md``

正文在 ``div.TRS_Editor`` 里，一案由「案例一」「案例1」开始，标题可以分成几段，
随后是【关键词】【基本案情】【复议办理】【典型意义】等小节。「专家点评」
「案例编号」「点击查看详情」不是案例正文。开头连续的「案例N：标题」是目录。

2021–2022 年的早期批次，moj 原页和 12348 案例库都已下线，改用仍可访问的转载页
（见 ``REPOST_SOURCES``）。这些页面版式各异，正文容器按 ``BOX_CANDIDATES`` 探测；
发布日和来源以清单为准，并在头部注明「原载司法部官网，转自某站」。
下载时 JS 验证页交给无头浏览器渲染，GBK 页面转成 UTF-8；有的转载页去掉了
「案例N」标记，整页就是一案。
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import bs4

from lawref.fetch import browser
from lawref.fetch.cache import CacheType
from lawref.fetch.errors import EmptyContentError
from lawref.fetch.models import LawItem
from lawref.parse.base import ParsedDocument, ParseError
from lawref.text import clean, norm

from .base import Provider
from .gov_local import (
    HTML_SUFFIXES,
    cache_filename,
    classify_content,
    page_headers,
    with_known_suffix,
)

BASE = "https://www.moj.gov.cn/pub/sfbgw"

# 行政复议各批次（顺序即批次号）。第 9 批是两高联合发布，正文混有行政诉讼案例
XZFY_DIR = "指导案例/行政复议"
XZFY_BATCHES = [
    ("第1批", f"{BASE}/gwxw/xwyw/szywbnyw/202406/t20240612_500275.html"),
    ("第2批", f"{BASE}/gwxw/xwyw/202406/t20240619_500658.html"),
    ("第3批", f"{BASE}/gwxw/xwyw/szywbnyw/202409/t20240924_506517.html"),
    ("第4批", f"{BASE}/gwxw/xwyw/szywbnyw/202411/t20241119_509843.html"),
    ("第5批", f"{BASE}/gwxw/xwyw/szywbnyw/202412/t20241231_512191.html"),
    ("第6批", f"{BASE}/gwxw/xwyw/szywbnyw/202502/t20250228_514923.html"),
    ("第7批", f"{BASE}/gwxw/xwyw/szywbnyw/202505/t20250521_519738.html"),
    ("第8批", f"{BASE}/zwxxgk/fdzdgknr/fdzdgknrtzwj/202508/t20250807_523566.html"),
    ("第9批", f"{BASE}/gwxw/xwyw/szywbnyw/202509/t20250917_525180.html"),
    ("第10批", f"{BASE}/fzgz/fzgzxzzf/fzgzfygz/202512/t20251213_529329.html"),
    ("第11批", f"{BASE}/fzgz/fzgzxzzf/fzgzfygz/202602/t20260212_531732.html"),
    ("第12批", f"{BASE}/gwxw/xwyw/szywbnyw/202604/t20260421_534071.html"),
    ("第13批", f"{BASE}/gwxw/xwyw/szywbnyw/202607/t20260728_537933.html"),
]

# 其他司法行政工作指导案例，一页一类
OTHER_DIR = "指导案例/司法行政"
OTHER_CATEGORIES = [
    ("仲裁", f"{BASE}/gwxw/xwyw/202301/t20230111_470637.html"),
    ("法治宣传教育", f"{BASE}/gwxw/xwyw/202302/t20230201_471390.html"),
    ("戒毒", f"{BASE}/gwxw/xwyw/202306/t20230626_481347.html"),
    ("司法鉴定", f"{BASE}/gwxw/xwyw/202307/t20230705_482079.html"),
    ("公证", f"{BASE}/gwxw/xwyw/202308/t20230803_484010.html"),
    ("监狱", f"{BASE}/gwxw/xwyw/202308/t20230830_485280.html"),
    ("社区矫正", f"{BASE}/gwxw/xwyw/202309/t20230920_486540.html"),
    ("法律援助", f"{BASE}/gwxw/xwyw/202309/t20230927_487012.html"),
    ("防范青少年药物滥用", f"{BASE}/gwxw/xwyw/202407/t20240704_501796.html"),
]

# 2023 年下半年以来的新专题：行政执法监督、涉企执法专项，以及老专题的第二批及以后。
# 同一专题的多批放在一个目录里；这些页面都是 moj 原页，日期从正文题注读。
SPECIAL_SERIES = [
    ("行政执法监督", f"{BASE}/gwxw/xwyw/szywbnyw/202411/t20241112_509377.html"),
    ("行政执法监督", f"{BASE}/gwxw/xwyw/szywbnyw/202501/t20250115_513148.html"),
    ("行政执法监督", f"{BASE}/gwxw/xwyw/szywbnyw/202505/t20250522_519797.html"),
    ("涉企执法为民办实事", f"{BASE}/gwxw/xwyw/szywbnyw/202509/t20250922_525358.html"),
    ("涉企执法为民办实事", f"{BASE}/gwxw/xwyw/szywbnyw/202510/t20251029_527071.html"),
    ("涉企执法为民办实事", f"{BASE}/gwxw/xwyw/szywbnyw/202601/t20260113_530545.html"),
    ("涉企执法为民办实事", f"{BASE}/gwxw/xwyw/szywbnyw/202605/t20260521_535398.html"),
    ("涉企执法为民办实事", f"{BASE}/gwxw/xwyw/szywbnyw/202608/t20260817_538578.html"),
    # 涉企执法专项行动第一批典型案例发在专题频道，不在要闻频道
    ("涉企执法典型案例", f"{BASE}/zwgkztzl/2025nianzhuanti/gfsqqxzzfzxxd20250417/bsyq20250417/202509/t20250915_525080.html"),
    ("涉企执法典型案例", f"{BASE}/gwxw/xwyw/szywbnyw/202510/t20251013_526078.html"),
    ("涉企执法典型案例", f"{BASE}/gwxw/xwyw/szywbnyw/202512/t20251215_529345.html"),
    ("涉企执法典型案例", f"{BASE}/gwxw/xwyw/szywbnyw/202601/t20260129_531166.html"),
    ("法律援助", f"{BASE}/fzgz/fzgzggflfwx/fzgzggflfw/202308/t20230823_484865.html"),
    ("法律援助", f"{BASE}/fzgz/fzgzggflfwx/fzgzggflfw/202510/t20251029_527096.html"),
    ("法律援助", f"{BASE}/gwxw/xwyw/szywbnyw/202605/t20260517_535070.html"),
    ("法律援助", f"{BASE}/gwxw/xwyw/szywbnyw/202606/t20260601_535835.html"),
    ("法律援助", f"{BASE}/gwxw/xwyw/szywbnyw/202610/t20261016_540796.html"),
    ("法治宣传教育", f"{BASE}/gwxw/xwyw/szywbnyw/202609/t20260908_539408.html"),
]


@dataclass(frozen=True)
class Repost:
    """早期批次改用的转载页：原页下线，日期和来源以清单为准。"""
    category: str
    date: str        # 司法部原始发布日 YYYY-MM-DD
    source: str      # 实际抓取的转载站
    url: str


REPOST_SOURCES = [
    Repost("人民调解", "2021-08-20", "厦门大学法学院网站",
           "https://sfbbase.xmu.edu.cn/info/1015/1457.htm"),
    Repost("公证", "2021-09-07", "厦门大学法学院网站",
           "https://sfbbase.xmu.edu.cn/info/1015/1465.htm"),
    Repost("监狱", "2021-04-16", "西藏自治区监狱管理局",
           "https://jyglj.xizang.gov.cn/xxgk_75/yswgk/202104/t20210421_200254.html"),
    Repost("戒毒", "2021-07-13", "黄石市司法局",
           "https://sfj.huangshi.gov.cn/gzdt/ywdt/jdgz/202107/t20210713_815217.html"),
    Repost("律师", "2022-03-26", "盘锦市司法局",
           "https://sfj.panjin.gov.cn/2022_03/28_08/content-366708.html"),
    Repost("司法鉴定", "2022-04-20", "盘锦市司法局",
           "https://sfj.panjin.gov.cn/2022_04/21_08/content-369469.html"),
    Repost("社区矫正", "2022-05-31", "湖北省司法厅",
           "https://sft.hubei.gov.cn/fbjd/zfyw/202206/t20220601_4155998.shtml"),
    Repost("监狱", "2022-07-08", "湖南省监狱管理局",
           "http://hnjyj.hunan.gov.cn/hnjyj/xxgk/jcdt/202207/t20220713_27556542.html"),
    Repost("戒毒", "2022-06-24", "抚顺市司法局",
           "https://fsssfj.fushun.gov.cn/gkgz/002001/002001002/002001002008/20220627/e62be922-3634-4e28-a826-fc4c4568dbe4.html"),
    Repost("人民调解", "2022-08-02", "厦门大学法学院网站",
           "https://sfbbase.xmu.edu.cn/info/1015/1567.htm"),
    Repost("法律援助", "2022-09-05", "厦门大学法学院网站",
           "https://sfbbase.xmu.edu.cn/info/1015/1577.htm"),
    Repost("公证", "2022-10-27", "厦门大学法学院网站",
           "https://sfbbase.xmu.edu.cn/info/1015/1587.htm"),
    Repost("律师", "2022-12-30", "黄石市司法局",
           "https://sfj.huangshi.gov.cn/gzdt/ywdt/lsgz/202301/t20230103_978522.html"),
    # 2021 年环境损害司法鉴定指导案例（10 案），moj 原页已下线，
    # 转载站每案一页，正文在 .editor_content_air 的版式表格里，页面是 GBK 且带 JS 验证
    Repost("司法鉴定", "2021-06-05", "山东环境学科研学实践活动组委会网站",
           "http://www.fwzk.cn/doc_23594896.html"),
    Repost("司法鉴定", "2021-06-05", "山东环境学科研学实践活动组委会网站",
           "http://www.fwzk.cn/doc_23594893.html"),
    Repost("司法鉴定", "2021-06-05", "山东环境学科研学实践活动组委会网站",
           "http://www.fwzk.cn/doc_23594891.html"),
    Repost("司法鉴定", "2021-06-05", "山东环境学科研学实践活动组委会网站",
           "http://www.fwzk.cn/doc_23594886.html"),
    Repost("司法鉴定", "2021-06-05", "山东环境学科研学实践活动组委会网站",
           "http://www.fwzk.cn/doc_23594857.html"),
    Repost("司法鉴定", "2021-06-05", "山东环境学科研学实践活动组委会网站",
           "http://www.fwzk.cn/doc_23594853.html"),
    Repost("司法鉴定", "2021-06-05", "山东环境学科研学实践活动组委会网站",
           "http://www.fwzk.cn/doc_23594849.html"),
    Repost("司法鉴定", "2021-06-05", "山东环境学科研学实践活动组委会网站",
           "http://www.fwzk.cn/doc_23594844.html"),
    Repost("司法鉴定", "2021-06-05", "山东环境学科研学实践活动组委会网站",
           "http://www.fwzk.cn/doc_23594841.html"),
    Repost("司法鉴定", "2021-06-05", "山东环境学科研学实践活动组委会网站",
           "http://www.fwzk.cn/doc_23594836.html"),
]

# 正文容器候选，按顺序取第一个含案例编号的。editor_content_air 放最前：有的转载站
# （fwzk）把正文放在它里面、又套一层 class 叫 TRS_Editor 的容器，取外层才完整
BOX_CANDIDATES = (".editor_content_air", ".TRS_Editor", ".v_news_content", ".zoomCon",
                  ".articleCont", ".ewb-article-info", ".trs_paper_default", ".view",
                  "#vsb_content_1005")
CASE_HINT_RE = re.compile(r"案例[一二三四五六七八九十百零\d]")

# 标题最多累积这么长，超出的行算正文：有的页面标题后直接是正文，没有小节名
TITLE_MAX = 60

# 案例小节名。裸标题（「案例简介：」）归一成【案例简介】
SECTIONS = ("关键词", "基本案情", "复议办理", "复议结果", "典型意义", "裁判结果", "诉讼过程",
            "指导意义", "相关规定", "要旨", "审理经过", "争议焦点", "处理结果", "案例分析",
            "案例简介", "案情简介", "案件简介", "案例概况")

# 标题分隔符有「案例一：」「案例一、」「案例一.」几种写法
CASE_RE = re.compile(r"^案例([一二三四五六七八九十百零\d]{1,3})(?:[：:、．.](.*))?$")
MARKER_RE = re.compile(r"^【([^】]{1,15})】\s*(.*)$")
BARE_SECTION_RE = re.compile(rf"^({'|'.join(SECTIONS)})[:：]?\s*(.*)$")
# 案例编号独占一行时是「SHGNZC1645584177」这样的编号
CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{5,}$")
NOISE_RE = re.compile(r"^(点击查看详情|案例编号[:：]?|附件[:：]?|责任编辑|分享到|打印本页|打印|微信扫一扫|商城|字号|邮箱[:：]|电话[:：])")
COMMENT_RE = re.compile(r"^案例[一二三四五六七八九十百零\d]+专家点评|^专家点评")
DATE_RE = re.compile(r"发布时间[:：]\s*(\d{4})-(\d{2})-(\d{2})")


@dataclass
class Case:
    """一页里的一个案例。"""
    number: str
    title: str
    heads: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    @property
    def is_xzfy(self) -> bool:
        """行政复议案例看小节名：两高联合发布的批次里混有行政诉讼案例。"""
        return bool({"复议办理", "复议结果"} & set(self.heads))


def _paragraphs(box: bs4.Tag, skip_table: bool = True) -> list[str]:
    ret = []
    for p in box.find_all("p"):
        # 只取最内层的 <p>：有的转载页把整篇正文包在一个 <p> 里
        if p.find("p"):
            continue
        # moj 原页的正文不在表格里，表格多是导航；版式怪异的转载页正文本身就在表格里
        if skip_table and p.find_parent("table"):
            continue
        line = clean(p.get_text(""))
        if line:
            ret.append(line)
    return ret


def _page_parts(text: str) -> tuple[list[str], bool]:
    """取出正文段落，并判断是不是 moj 原页（转载页的标题要去掉排版空格）。"""
    soup = bs4.BeautifulSoup(text, "html.parser")
    box = None
    repost = False
    skip_table = True
    for sel in BOX_CANDIDATES:
        node = soup.select_one(sel)
        if node is not None and CASE_HINT_RE.search(node.get_text()):
            box = node
            repost = sel != ".TRS_Editor"
            skip_table = not repost
            break
    if box is None:
        # 有的转载页把「案例N」标记去掉了，整页就是一案：取正文最长的一个容器。
        # 这类版面的正文本身在表格里，段落不能按表格过滤掉
        for sel in BOX_CANDIDATES:
            node = soup.select_one(sel)
            if node is not None and len(node.get_text()) > 500:
                box = node
                repost = True
                skip_table = False
                break
    if box is None:
        box = soup.select_one(".TRS_Editor") or soup.body or soup
    ps = _paragraphs(box, skip_table)
    if not ps and skip_table:
        # 正文本身在版式表格里，按表格过滤会全丢掉
        ps = _paragraphs(box, False)
    return ps, repost


def split_cases(text: str) -> list[Case]:
    """把一页拆成各个案例。开头连续的「案例N：标题」是目录，跳过。"""
    ps, repost = _page_parts(text)
    start = 0
    first = next((i for i, p in enumerate(ps) if CASE_RE.match(p)), None)
    synthetic = first is None and repost and bool(ps)
    if first is not None:
        end = first
        while end < len(ps) and CASE_RE.match(ps[end]):
            end += 1
        # 目录各项都带标题，正文标题多为「案例N」不带标题；保留最后一项作为正文起点
        start = end - 1 if end - first >= 2 else first

    cases: list[Case] = []
    cur: Case | None = None
    title: list[str] = []
    lines: list[tuple[str, str]] = []
    state = "title"
    if synthetic:
        # 转载页去掉了「案例N」标记，整页一案
        cur = Case(number="", title="")

    def flush():
        nonlocal cur, title, lines
        if cur is not None and title and lines:
            # 转载页的标题常被 <br> 拆开，拼接后残留空格；文件名和 H1 都不要空格
            cur.title = norm("".join(title)) if repost else "".join(title)
            cur.heads = [text for kind, text in lines if kind == "H"]
            cur.lines = [f"【{text}】" if kind == "H" else text for kind, text in lines]
            cases.append(cur)
        cur, title, lines = None, [], []

    for p in ps[start:]:
        if NOISE_RE.match(p) or CODE_RE.match(p):
            continue
        m = CASE_RE.match(p)
        if m:
            flush()
            cur = Case(number=m.group(1), title="")
            title = [m.group(2).strip()] if m.group(2) else []
            lines = []
            state = "title"
            continue
        if COMMENT_RE.match(p):
            state = "comment"
            continue
        if state == "comment":
            continue
        marker = MARKER_RE.match(p)
        if marker:
            state = "body"
            lines.append(("H", marker.group(1).strip()))
            if marker.group(2).strip():
                lines.append(("P", marker.group(2).strip()))
            continue
        bare = BARE_SECTION_RE.match(p)
        if bare:
            state = "body"
            lines.append(("H", bare.group(1)))
            if bare.group(2).strip():
                lines.append(("P", bare.group(2).strip()))
            continue
        if state == "title":
            # 标题后直接是正文时，靠长度认出正文起点
            if title and sum(len(t) for t in title) + len(p) > TITLE_MAX:
                state = "body"
                lines.append(("P", p))
            else:
                title.append(p)
        else:
            lines.append(("P", p))
    flush()
    return cases


def page_meta(text: str, url: str) -> tuple[str | None, str]:
    """页面的发布时间和来源，来源写在正文外，取不到日期时按 URL 里的 t20240612。"""
    m = DATE_RE.search(text)
    if m:
        date = "-".join(m.groups())
    else:
        m = re.search(r"/t(\d{4})(\d{2})(\d{2})_", url)
        date = "-".join(m.groups()) if m else None
    source = "司法部"
    m = re.search(r"来源[:：]\s*(\S+?)\s+发布时间", text)
    if m:
        source = m.group(1)
    return date, source


async def _checked_html(path: Path, url: str) -> Path:
    """JS 验证页用无头浏览器渲染；GBK 转载页转成 UTF-8，后面读文件才不会乱码。"""
    path = with_known_suffix(path)
    if path.suffix.lower() not in HTML_SUFFIXES:
        return path
    raw = path.read_bytes()
    if classify_content(raw):
        rendered = await browser.render_html(url, headers=page_headers(url))
        if not rendered:
            raise EmptyContentError(f"拿到的不是正文：{url}")
        path.write_text(rendered, encoding="utf-8")
        return path
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        path.write_text(raw.decode("gb18030", errors="replace"), encoding="utf-8")
    return path


class XzfyCaseProvider(Provider):
    """行政复议各批次及其他司法行政工作指导案例。一页多案，每案一个文件。"""

    async def fetch_document(self, item: LawItem) -> Path:
        url = item.file_link or item.id
        path = await self.cache.download(url, CacheType.HTML, headers=page_headers(url),
                                         filename=cache_filename(url))
        return await _checked_html(path, url)

    async def note(self, item: LawItem) -> str:
        """转载的早期批次在头部注明实际来源；moj 原页的批次原文里就有题注。"""
        repost = next((r for r in REPOST_SOURCES if r.url == item.file_link), None)
        if repost is None:
            return ""
        year, month, day = repost.date.split("-")
        return (f"（{year}年{int(month)}月{int(day)}日 司法部发布　"
                f"原载司法部官网，转自{repost.source}）")

    async def items(self) -> list[LawItem]:
        """按批次清单下载并拆分，每个案例一个条目。同批次文章被多个条目复用缓存。"""
        groups = [(f"{XZFY_DIR}/{batch}", url, True, None) for batch, url in XZFY_BATCHES]
        groups += [(f"{OTHER_DIR}/{name}", url, False, None) for name, url in OTHER_CATEGORIES]
        groups += [(f"{OTHER_DIR}/{r.category}", r.url, False, r) for r in REPOST_SOURCES]
        groups += [(f"{OTHER_DIR}/{name}", url, False, None) for name, url in SPECIAL_SERIES]
        items = []
        for folder, url, xzfy_only, repost in groups:
            text = (await self.fetch_document(LawItem(
                id=url, title="", released_by="", folder=folder, file_link=url))).read_text(encoding="utf-8")
            cases = split_cases(text)
            if repost is not None:
                date, source = repost.date, repost.source
            else:
                date, source = page_meta(text, url)
            for case in cases:
                if xzfy_only and not case.is_xzfy:
                    continue
                items.append(LawItem(
                    id=url,
                    title=case.title,
                    released_by=source,
                    folder=folder,
                    publication_date=date,
                    file_link=url,
                ))
        return items

    async def parse_document(self, path: Path, title: str) -> ParsedDocument:
        """按标题从页面里取出对应的那一个案例。"""
        cases = split_cases(path.read_text(encoding="utf-8"))
        wanted = norm(title)
        found = next((c for c in cases if norm(c.title) == wanted), None)
        if found is None:
            # heading 可能是「指导性案例N号：案名」这类全称
            matched = [c for c in cases if wanted and norm(c.title) in wanted]
            found = matched[0] if len(matched) == 1 else None
        if found is None:
            raise ParseError(f"页面里找不到案例：{title}")
        return ParsedDocument(found.title, "", found.lines)
