"""最高人民法院指导性案例：https://www.court.gov.cn/shenpan/gengduo/77.html

索引分页是 ``77.html``、``77_2.html``、``77_3.html``……（没有 ``77_1``，最后一页之后 404），
每条是「指导案例N号：案名」加发布日期，链接到 ``/shenpan/xiangqing/<id>.html``。
一案一页，正文在 ``div.txt_txt`` 里：

    指导案例1号
    上海中原物业顾问有限公司诉陶德华
    居间合同纠纷案
    （最高人民法院审判委员会讨论通过 2011年12月20日发布）
    关键词 民事 居间合同 二手房买卖 违约
    裁判要点
    ……

- ``指导案例/指导案例N号(日期).md``，文件里的 # 标题是「指导性案例N号：案名」
- 题注写进头部信息；末尾「（生效裁判审判人员：…）」不是正文

第 45 号不在索引里（索引从 46 号起），只有《最高人民法院公报》的合刊页
（见 ``EXTRA_CASES``）。那一页没有 ``div.txt_txt``，一页含 45–52 号，
标题和案名写在同一行，解析时按编号截取。
"""
import html as html_module
import itertools
import re
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

BASE = "https://www.court.gov.cn"
FOLDER = "指导案例"
INDEX_URL = f"{BASE}/shenpan/gengduo/77{{}}.html"

# 公报合刊页把案名写在编号同一行：「指导案例45号 北京百度网讯科技有限公司…」
HEAD_RE = re.compile(r"^指导(?:性)?案例(\d+)号(?:\s+(.*))?$")
INDEX_TITLE_RE = re.compile(r"^指导(?:性)?案例(\d+)号[:：]?\s*(.*)$")
NOTE_RE = re.compile(
    r"^[（(]最高人民法院审判委员会讨论通过\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日发布[）)]$")
PUBLISHED_RE = re.compile(r"发布时间[:：]\s*(\d{4})-(\d{1,2})-(\d{1,2})")
SOURCE_RE = re.compile(r"来源[:：]\s*([^\s<>，,。；;]{1,30})")

# 小节名。有的页面「关键词」后面直接跟着内容，如「关键词 民事 居间合同」
SECTION_WORDS = ("关键词", "裁判要点", "基本案情", "裁判结果", "裁判理由", "相关法条")
# 正文后面的署名、版面信息
TAIL_RE = re.compile(r"^（生效裁判审判人员[:：]|^责任编辑[:：]|^分享到|^字号[:：]")

# 索引里缺的案例：第 45 号只有公报合刊页（一页含 45–52 号）
EXTRA_CASES = [
    ("http://gongbao.court.gov.cn/Details/0ac4252b80a1e3a9d4e417ab4218d9.html",
     45, "北京百度网讯科技有限公司诉青岛奥商网络技术有限公司等不正当竞争纠纷案",
     "2015-04-15"),
]


def _text_lines(soup: bs4.BeautifulSoup) -> list[str]:
    """div.txt_txt 的文本行。旧页面用 <br>、</div> 分行。"""
    box = soup.select_one("div.txt_txt") or soup.body or soup
    frag = re.sub(r"<br\s*/?>", "\n", str(box), flags=re.I)
    frag = re.sub(r"</(?:p|div|tr|h\d|li)>", "\n", frag, flags=re.I)
    text = html_module.unescape(re.sub(r"<[^>]+>", "", frag))
    return [line for line in (clean(part) for part in text.split("\n")) if line]


def index_entries(text: str) -> list[tuple[int, str, str, str]]:
    """索引页里的案例条目：[(编号, 案名, 链接, 发布日期)]。分页、「首页」等链接不算。"""
    soup = bs4.BeautifulSoup(text, "html.parser")
    entries = []
    for a in soup.select("div.sec_list ul li a[href]"):
        href = a["href"]
        if "/shenpan/xiangqing/" not in href:
            continue
        m = INDEX_TITLE_RE.match(clean(a.get("title") or a.get_text()))
        if not m:
            continue
        date = ""
        if d := a.find_parent("li").find("i", class_="date"):
            date = clean(d.get_text())
        url = href if href.startswith("http") else BASE + href
        entries.append((int(m.group(1)), m.group(2).strip(), url, date))
    return entries


def _split_sections(lines: list[str]) -> list[str]:
    """「关键词 民事 居间合同」拆成「关键词」和「民事 居间合同」两行。"""
    out = []
    for line in lines:
        for word in SECTION_WORDS:
            if line == word:
                out.append(word)
                break
            if line.startswith(word + "：") or line.startswith(word + " "):
                out.append(word)
                rest = line[len(word):].lstrip("： ").strip()
                if rest:
                    out.append(rest)
                break
        else:
            out.append(line)
    return out


def parse_detail(text: str, url: str = "", number: int | str | None = None) -> ParsedDocument:
    """把详情页解析成标题、题注和正文。number 给合刊页用，只取该编号那一段。"""
    soup = bs4.BeautifulSoup(text, "html.parser")
    lines = _text_lines(soup)
    heads = [i for i, line in enumerate(lines) if HEAD_RE.match(line)]
    if not heads:
        raise ParseError(f"页面里找不到指导案例编号：{url}")
    wanted = None if number is None else str(number)
    head = next((i for i in heads
                 if wanted is None or HEAD_RE.match(lines[i]).group(1) == wanted), None)
    if head is None:
        raise ParseError(f"页面里找不到指导案例{wanted}号：{url}")
    # 公报合刊页把案名写在编号同一行；普通详情页案名在后面几行
    num = HEAD_RE.match(lines[head]).group(1)
    note_i = next((i for i in range(head + 1, len(lines)) if NOTE_RE.match(lines[i])), None)
    if note_i is None:
        raise ParseError(f"页面里找不到题注：{url}")
    same_line = HEAD_RE.match(lines[head]).group(2) or ""
    name = same_line + "".join(lines[head + 1:note_i])
    if not name:
        raise ParseError(f"页面里找不到案名：{url}")

    end = next((i for i in heads if i > head), len(lines))
    body = _split_sections(lines[note_i + 1:end])
    for i, line in enumerate(body):
        if TAIL_RE.match(line):
            body = body[:i]
            break

    note_m = NOTE_RE.match(lines[note_i])
    note = f"（{to_cn(iso(*note_m.groups()))} 最高人民法院审判委员会讨论通过并发布）"
    return ParsedDocument(f"指导性案例{num}号：{name}", note, body)


def detail_meta(text: str) -> tuple[str | None, str]:
    """详情页的（发布日期，来源）。索引页日期相同时用索引的即可。"""
    plain = clean(html_module.unescape(re.sub(r"<[^>]+>", " ", text)))
    date = None
    if m := PUBLISHED_RE.search(plain):
        date = iso(*m.groups())
    source_m = SOURCE_RE.search(plain)
    return date, source_m.group(1) if source_m else ""


class GuideCaseProvider(Provider):
    """最高法指导性案例。一案一页，每案一个文件。"""

    def __init__(self, cache=None) -> None:
        super().__init__(cache)

    async def fetch_document(self, item: LawItem) -> Path:
        return await self.cache.download(item.file_link, CacheType.HTML,
                                         headers=page_headers(item.file_link))

    async def _fetch_index(self, suffix: str) -> Path:
        url = INDEX_URL.format(suffix)
        return await self.cache.download(url, CacheType.HTML, headers=page_headers(url),
                                         filename=f"guide_index{suffix}.html")

    async def items(self) -> list[LawItem]:
        """翻索引页，每个案例一个条目。已经收录的由调用方过滤。"""
        found: dict[int, LawItem] = {}
        # 第一页是 77.html，之后是 77_2.html、77_3.html……没有 77_1
        pages = itertools.chain([""], (f"_{n}" for n in itertools.count(2)))
        for i, suffix in enumerate(pages):
            if i >= 20:
                break
            try:
                path = await self._fetch_index(suffix)
            except net.HTTPError:
                break
            entries = index_entries(path.read_text(encoding="utf-8"))
            if not entries:
                break
            for number, _name, url, date in entries:
                found.setdefault(number, LawItem(
                    id=url, title=f"指导案例{number}号", released_by="", folder=FOLDER,
                    publication_date=date or None, file_link=url))
        # 索引缺的案例（第 45 号）只有公报合刊页
        for url, number, name, date in EXTRA_CASES:
            found.setdefault(number, LawItem(
                id=url, title=f"指导案例{number}号",
                full_title=f"指导性案例{number}号：{name}", released_by="", folder=FOLDER,
                publication_date=date, file_link=url))
        return [found[number] for number in sorted(found)]

    async def parse_document(self, path: Path, title: str) -> ParsedDocument:
        m = re.search(r"指导(?:性)?案例(\d+)号", title)
        return parse_detail(path.read_text(encoding="utf-8"), str(path),
                            int(m.group(1)) if m else None)
