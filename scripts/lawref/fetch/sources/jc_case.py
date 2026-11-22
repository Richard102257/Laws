"""最高人民检察院指导性案例（检例）：https://www.spp.gov.cn/spp/jczdal/

索引页 ``index.shtml``、``index_2.shtml``……共 60 个批次（再往后 302 回首页），
第 15、22、24 批的发布页不在索引里，单独列出。一页一批，正文在 ``div#fontzoom``，
一案由「（检例第M号）」开始，标题是它前面连续的几段（有的案名分成两段）。

- ``指导案例/检察/第N批/检例第M号(日期).md``，文件里的 # 标题是「检例第M号：案名」
- 通知前言（检察委员会会议决定、印发日期）写进头部信息，署名的承办检察官、案例撰稿人丢掉
"""
import html as html_module
import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path

import bs4

from lawref.dates import iso, to_cn
from lawref.fetch import net
from lawref.fetch.cache import CacheType
from lawref.fetch.models import LawItem
from lawref.parse.base import ParsedDocument, ParseError
from lawref.text import clean

from .base import Provider
from .gov_local import page_headers

BASE = "https://www.spp.gov.cn"
FOLDER = "指导案例/检察"
INDEX_URL = f"{BASE}/spp/jczdal/index{{}}.shtml"

# 索引里没有的批次：第 15 批是新闻发布会稿，第 24 批是发布会报道，
# 检例正文后面还跟着典型案例、答记者问，按 stop 截断。
# 第 22 批只有地方检察院的全文页（spp 原页没有这一批），页面自身日期 2020-12-08，
# 文件名用官方印发日，所以带固定日期。
NX_STOP_RE = re.compile(r"^【作者】|^footer \{|\.[\w-]+\s*\{|@media only screen|params\.doctitle")
EXTRA_PAGES = [
    (f"{BASE}/zdgz/201909/t20190925_432908.shtml", re.compile(r"^行政检察典型案例$"), None),
    (f"{BASE}/xwfbh/wsfbt/202012/t20201222_490159.shtml",
     re.compile(r"^(?:强化刑事立案监督|——最高检.+答记者问)"), None),
    ("https://nx.jcy.gov.cn/pyxy/ajjj/202012/t20201208_609141.html", NX_STOP_RE, "2020-11-24"),
]

CASE_RE = re.compile(r"^（检例第([一二三四五六七八九十百零\d]+)号）$")
BATCH_RE = re.compile(r"第([一二三四五六七八九十百零\d]+)批指导性案例")
NOTICE_RE = re.compile(r"^关于印发最高人民检察院第([一二三四五六七八九十百零\d]+)批指导性案例的通知$")
DECISION_RE = re.compile(
    r"经(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)(最高人民检察院.+?会议)(决定|通过)")
ISSUE_DATE_RE = re.compile(r"^\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日$")
TIME_RE = re.compile(r"时间[:：]\s*(\d{4})-(\d{2})-(\d{2})")
PUBLISHED_RE = re.compile(r"发布时间[:：]\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
SOURCE_RE = re.compile(r"来源[:：]\s*([^\s，,。；;、\]\)]{1,20})")

# 标题前的「1.」「案例一：」是编号，不是案名的一部分
TITLE_PREFIX_RE = re.compile(r"^(?:\d+[.、．]\s*|案例[一二三四五六七八九十百零]+[:：]?\s*)")
# 这些行不是案名：小节、法条、署名、通知抬头、日期落款……
TITLE_NOISE_RE = re.compile(
    r"^(?:【|《|（|[（(])|"
    r"^关于印发最高人民检察院第.+批指导性案例的通知$|"
    r"^最高人民检察院第[一二三四五六七八九十百零\d]+批指导性案例$|"
    r"^\d{4}年\d{1,2}月\d{1,2}日$|"
    r"^最高人民检察院$|"
    r"^发布时间[:：]|"
    r"^(?:办案检察院|承办检察官|办案检察官|案例撰写人|案例撰稿人)[:：]|"
    r".*：$"
)
# 正文里的署名、版面信息
BODY_NOISE_RE = re.compile(
    r"^(?:办案检察院|承办检察官|办案检察官|案例撰写人|案例撰稿人)[:：]|"
    r"^发布时间[:：]|^分享到|^字号[:：]"
)
CN_NUM = "零一二三四五六七八九"


def cn2int(text: str) -> int:
    """「六十三」「257」→ 63、257。只处理到百位，够批次和检例号用。"""
    if text.isdigit():
        return int(text)
    section = number = 0
    for ch in text:
        if ch in CN_NUM[1:]:
            number = CN_NUM.index(ch)
        elif ch == "十":
            section += (number or 1) * 10
            number = 0
        elif ch == "百":
            section += (number or 1) * 100
            number = 0
    return section + number


@dataclass
class Case:
    """一页里的一个检例。"""
    number: str
    title: str
    lines: list[str] = field(default_factory=list)

    @property
    def full_title(self) -> str:
        return f"检例第{self.number}号：{self.title}"


@dataclass
class BatchPage:
    """一页一批指导性案例。"""
    batch: int
    name: str
    date: str | None
    source: str
    note: str
    cases: list[Case]


def _text_lines(soup: bs4.BeautifulSoup) -> list[str]:
    """正文容器的文本行。div#fontzoom 里没有 <p> 时用 <br>、</div> 分行。"""
    box = soup.select_one("div#fontzoom") or soup.body or soup
    frag = re.sub(r"<br\s*/?>", "\n", str(box), flags=re.I)
    frag = re.sub(r"</(?:p|div|tr|h\d|li)>", "\n", frag, flags=re.I)
    text = html_module.unescape(re.sub(r"<[^>]+>", "", frag))
    return [line for line in (clean(part) for part in text.split("\n")) if line]


def _is_title(line: str) -> bool:
    """案名那一段：短、不以句号结尾、不是小节或法条或落款。"""
    text = TITLE_PREFIX_RE.sub("", line)
    return len(text) <= 90 and not re.search(r"[。；]$", text) \
        and "《" not in text and not TITLE_NOISE_RE.match(text)


def _normalize_section(line: str) -> str:
    """「【要 旨】」→「【要旨】」。"""
    def strip_space(m: re.Match) -> str:
        return "【" + re.sub(r"\s+", "", m.group(1)) + "】"

    return re.sub(r"【([^】]*)】", strip_space, line)


def _meta_text(soup: bs4.BeautifulSoup) -> str:
    """页面头部「时间：…　作者：…　来源：…」那一行。"""
    el = soup.select_one("div.detail_extend1")
    return clean(el.get_text(" ")) if el else ""


def parse_page(text: str, url: str = "", stop: re.Pattern | None = None,
               date: str | None = None) -> BatchPage:
    """把一个批次页拆成一批检例。stop 用来截掉正文后面的其他文章；
    date 给页面日期和官方印发日不一致的批次用。"""
    soup = bs4.BeautifulSoup(text, "html.parser")
    lines = _text_lines(soup)
    markers = [i for i, line in enumerate(lines) if CASE_RE.match(line)]
    if not markers:
        raise ParseError(f"页面里找不到检例：{url}")

    title = soup.title.get_text() if soup.title else ""
    batch_m = BATCH_RE.search(title) or BATCH_RE.search("\n".join(lines[:markers[0]]))
    if not batch_m:
        raise ParseError(f"页面里找不到批次名：{url}")
    batch = cn2int(batch_m.group(1))

    # 通知前言：会议决定和印发日期写进头部信息
    preamble = "\n".join(lines[:markers[0]])
    decision = DECISION_RE.search(preamble)
    decision_text = "".join(decision.groups()) if decision else None
    issue_date = next((line.strip() for line in reversed(lines[:markers[0]])
                       if ISSUE_DATE_RE.match(line.strip())), None)

    # 头部元信息在 div.detail_extend1 里，发布会稿（第 24 批）没有，回退到去标签的全文
    meta = _meta_text(soup)
    plain = clean(html_module.unescape(re.sub(r"<[^>]+>", " ", text)))
    date_m = TIME_RE.search(meta) or PUBLISHED_RE.search(meta) \
        or TIME_RE.search(plain) or PUBLISHED_RE.search(plain)
    if date:
        pass
    elif date_m:
        date = iso(*date_m.groups())
    elif url_m := re.search(r"/t(\d{4})(\d{2})(\d{2})_", url):
        date = iso(*url_m.groups())
    else:
        date = None
    source_m = SOURCE_RE.search(meta) or SOURCE_RE.search(plain)
    source = source_m.group(1).rstrip("：: ") if source_m else ""
    # 「来源：作者：」这类没填来源的头部回退成高检网
    if not source or "作者" in source or "编辑" in source:
        source = "高检网"

    note = _make_note(batch_m.group(0), decision_text, issue_date, to_cn(date) if date else None)

    cases: list[Case] = []
    # 每案的标题是 marker 前面连续的几段；正文到下一案的标题为止
    title_starts = []
    for k, i in enumerate(markers):
        lower = markers[k - 1] + 1 if k else 0
        j = i
        while j > lower and _is_title(lines[j - 1]):
            j -= 1
        title_starts.append(j)
    for k, i in enumerate(markers):
        end = title_starts[k + 1] if k + 1 < len(markers) else len(lines)
        title = "".join(TITLE_PREFIX_RE.sub("", line)
                        for line in lines[title_starts[k]:i])
        body = [_normalize_section(line) for line in lines[i + 1:end]
                if not BODY_NOISE_RE.match(line)]
        if stop:
            cut = next((j for j, line in enumerate(body) if stop.search(line)), None)
            if cut is not None:
                body = body[:cut]
        cases.append(Case(number=CASE_RE.match(lines[i]).group(1),
                          title=title, lines=body))
    return BatchPage(batch=batch, name=batch_m.group(0), date=date, source=source,
                     note=note, cases=cases)


def _make_note(batch_name: str, decision: str | None, issue_date: str | None,
               date_cn: str | None) -> str:
    """头部的题注：「（2026年4月3日…会议决定　2026年7月15日最高人民检察院印发第六十三批指导性案例）」。"""
    parts = []
    if decision:
        parts.append(decision)
    if issue_date:
        parts.append(f"{issue_date}最高人民检察院印发{batch_name}")
    elif date_cn:
        parts.append(f"{date_cn}最高人民检察院发布{batch_name}")
    return f"（{'　'.join(parts)}）" if parts else ""


def index_links(text: str) -> list[tuple[str, int]]:
    """索引页里的批次链接：[(链接, 批次号)]。"""
    soup = bs4.BeautifulSoup(text, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/jczdal/20" not in href:
            continue
        m = BATCH_RE.search(a.get_text(" "))
        if not m:
            continue
        url = href if href.startswith("http") else BASE + href
        links.append((url, cn2int(m.group(1))))
    return links


class JcCaseProvider(Provider):
    """最高检指导性案例（检例）。一页多案，每案一个文件。"""

    def __init__(self, cache=None) -> None:
        super().__init__(cache)
        self._pages: dict[Path, tuple[BatchPage, re.Pattern | None, str | None]] = {}

    async def fetch_document(self, item: LawItem) -> Path:
        return await self.cache.download(item.file_link, CacheType.HTML,
                                         headers=page_headers(item.file_link))

    async def _fetch_page(self, url: str) -> Path:
        return await self.fetch_document(LawItem(
            id=url, title="", released_by="", folder=FOLDER, file_link=url))

    async def items(self) -> list[LawItem]:
        """索引页加两个单独的发布页，每个检例一个条目。"""
        urls: dict[int, tuple[str, re.Pattern | None, str | None]] = {}
        # 索引分页是 index.shtml、index_2.shtml、index_3.shtml（没有 index_1，
        # 请求它会跳回首页），最后一页之后没有链接
        for page_num in itertools.count(1):
            url = INDEX_URL.format("" if page_num == 1 else f"_{page_num}")
            try:
                path = await self._fetch_page(url)
            except net.HTTPError:
                break
            links = index_links(path.read_text(encoding="utf-8"))
            if not links or page_num >= 10:
                break
            for link, batch in links:
                urls.setdefault(batch, (link, None, None))
        for url, stop, date in EXTRA_PAGES:
            path = await self._fetch_page(url)
            page = parse_page(path.read_text(encoding="utf-8"), url, stop, date)
            urls.setdefault(page.batch, (url, stop, date))

        items = []
        seen: set[str] = set()
        for batch, (url, stop, date) in sorted(urls.items()):
            path = await self._fetch_page(url)
            page = parse_page(path.read_text(encoding="utf-8"), url, stop, date)
            self._pages[path] = (page, stop, date)
            for case in page.cases:
                if not case.title or case.number in seen:
                    continue
                seen.add(case.number)
                items.append(LawItem(
                    id=url,
                    title=f"检例第{case.number}号",
                    full_title=case.full_title,
                    released_by=page.source,
                    folder=f"{FOLDER}/第{batch}批",
                    publication_date=page.date,
                    file_link=url,
                    note=page.note,
                ))
        return items

    async def note(self, item: LawItem) -> str:
        return item.note or ""

    async def parse_document(self, path: Path, title: str) -> ParsedDocument:
        """按检例号从页面里取出对应的那一个。"""
        if path not in self._pages:
            self._pages[path] = (parse_page(path.read_text(encoding="utf-8")), None, None)
        page, _stop, _date = self._pages[path]
        m = re.search(r"检例第([一二三四五六七八九十百零\d]+)号", title)
        found = next((c for c in page.cases if m and c.number == m.group(1)), None)
        if found is None:
            raise ParseError(f"页面里找不到检例：{title}")
        return ParsedDocument(found.full_title, "", found.lines)
