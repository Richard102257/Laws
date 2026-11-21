"""国家规章库的地方政府规章：https://www.gov.cn/zhengce/xxgk/gjgzk/

和部门规章同一张表，类别字段是「地方政府规章」。原文在各地政府网站上，不在 gov.cn，
所以不按域名过滤；写到 out/<地区>地方法规/地方性法规/<地区>/，organize 再移进 DLC/<地区>地方法规/。

同一部法规的原文可能同时有省级（KIND=2）和市级（KIND=3）的记录，按类别检索两种都取。
"""
import asyncio
import hashlib
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import tqdm

from lawref.fetch import browser, net
from lawref.fetch.cache import CacheType
from lawref.fetch.errors import DeadLinkError, EmptyContentError
from lawref.fetch.models import LawItem, Page
from lawref.regions import dlc_name

from .gov import GovRegulationDatabaseProvider, dates_from_note

# 扩展名不在 parse.HTML_SUFFIXES 里时按文件头判断，见 with_known_suffix
HTML_SUFFIXES = (".htm", ".html", ".shtml")
ZIP_MAGIC = b"PK\x03\x04"
OLD_WORD_MAGIC = b"\xd0\xcf\x11\xe0"
HTML_MAGIC = (b"<!doctype html", b"<html", b"<head", b"<body", b"<?xml")

# 已下载的原文里这些标记说明拿到的不是正文。缓存下来只会一直失败，所以识别后要删掉
HTTPS_STUB_MARKERS = (b"targetprotocol", b"window.location.protocol")
CHALLENGE_MARKERS = (b"wafdirection", b"environment checking", b"antiy",
                     b"browserschallenge", b"window.location.reload")
INVALID_MARKERS = ("无效的访问地址".encode(), "页面不存在".encode())
CONTENT_TAGS = (b"<div", b"<p", b"<table", b"<br", b"<span", b"<font")
MIN_CONTENT = 400


def classify_content(raw: bytes) -> str | None:
    """已下载的 HTML 是不是空壳、JS 验证页、失效页，是则返回原因，否则 None。"""
    low = raw.lower()
    if any(marker in low for marker in CHALLENGE_MARKERS):
        return "JS 验证页"
    if any(marker in raw for marker in INVALID_MARKERS):
        return "失效页"
    if any(marker in low for marker in HTTPS_STUB_MARKERS):
        return "http→https 跳转桩"
    if len(raw) < MIN_CONTENT:
        return "内容过少"
    if b"<body" not in low and not any(tag in low for tag in CONTENT_TAGS):
        return "没有 body"
    return None


class _HttpsStub(Exception):
    """http 的原文只回了一个 JS 跳转桩，换 https 再下。"""


def cache_filename(url: str) -> str:
    """URL 里没有文件名时（如以 / 结尾的 …/detail/），用 URL 的哈希当文件名。"""
    name = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return name or hashlib.md5(url.encode("utf-8")).hexdigest()


def with_known_suffix(path: Path) -> Path:
    """各地政府网站的原文链接常常没有扩展名（…/detail/66）或用的是 .jhtml，
    parse_file 按扩展名选 parser，会当成 Word。下载后按文件头补上正确的扩展名。"""
    if path.suffix.lower() in (*HTML_SUFFIXES, ".epub", ".docx", ".doc"):
        return path
    raw = path.open("rb").read(512)
    if raw.startswith(ZIP_MAGIC):
        suffix = ".docx"
    elif raw.startswith(OLD_WORD_MAGIC):
        suffix = ".doc"
    elif raw.lstrip().lower().startswith(HTML_MAGIC) or (b"<" in raw and any(
            tag in raw.lower() for tag in (b"<div", b"<p", b"<table", b"<h1", b"<h2", b"<meta"))):
        suffix = ".html"
    else:
        return path
    target = path.with_suffix(suffix)
    if target == path:
        return path
    target.unlink(missing_ok=True)
    path.rename(target)
    return target


# 各地政府网站前的防火墙常返回这些状态码。net.py 只重试 429 和 5xx，
# 所以在这里按站点暂停再重试，避免一次 412/521 就永久失败
WAF_STATUS = {403, 405, 412, 521}
WAF_DELAYS = (5, 15, 30, 60, 120)
_waf_until: dict[str, float] = {}
_warmed_up: set[str] = set()
_no_browser_hint: set[str] = set()


async def _wait_if_blocked(host: str) -> None:
    while (delay := _waf_until.get(host, 0) - time.monotonic()) > 0:
        await asyncio.sleep(delay)


def _block_host(host: str, delay: float, status) -> None:
    until = time.monotonic() + delay
    if until > _waf_until.get(host, 0):
        _waf_until[host] = until
        tqdm.tqdm.write(f"{host} {status}，暂停 {delay:g} 秒", file=sys.stderr)


def _retry_after(headers) -> float | None:
    """防火墙有时会在 Retry-After 里说明要等多久。"""
    try:
        return max(0.0, float((headers or {}).get("Retry-After", "")))
    except ValueError:
        return None


async def _warm_up(scheme: str, host: str) -> None:
    """被拦过之后先访问一次首页拿 cookie，再取正文。只对每个站点做一次。"""
    key = f"{scheme}://{host}"
    if key in _warmed_up:
        return
    _warmed_up.add(key)
    try:
        async with net.session().get(f"{key}/", headers=page_headers(f"{key}/")) as response:
            response.raise_for_status()
            await response.read()
    except Exception:
        pass


def _hint_browser(host: str) -> None:
    """验证页靠静态下载过不去，提示一次装 playwright 就能自动兜底。"""
    if browser.installed() or host in _no_browser_hint:
        return
    _no_browser_hint.add(host)
    tqdm.tqdm.write(f"{host} 是 JS 验证页；装 playwright（pip install playwright && "
                    "playwright install chromium）后重跑可自动兜底", file=sys.stderr)


def page_headers(url: str) -> dict[str, str]:
    """下载原文用的请求头，比只带 UA 更像浏览器，减少被防火墙拦下的机会。"""
    parts = urlparse(url)
    return {
        "User-Agent": GovRegulationDatabaseProvider.HEADERS["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": f"{parts.scheme}://{parts.netloc}/",
    }

# 检索字段里的省名 → 地区名（REGION_CODE_MAP 的键，也是 DLC 目录名的一部分）
PROVINCE_TO_REGION = {
    "北京市": "北京", "天津市": "天津", "上海市": "上海", "重庆市": "重庆",
    "河北省": "河北", "山西省": "山西", "辽宁省": "辽宁", "吉林省": "吉林",
    "黑龙江省": "黑龙江", "江苏省": "江苏", "浙江省": "浙江", "安徽省": "安徽",
    "福建省": "福建", "江西省": "江西", "山东省": "山东", "河南省": "河南",
    "湖北省": "湖北", "湖南省": "湖南", "广东省": "广东", "海南省": "海南",
    "四川省": "四川", "贵州省": "贵州", "云南省": "云南", "陕西省": "陕西",
    "甘肃省": "甘肃", "青海省": "青海",
    "内蒙古自治区": "内蒙古", "广西壮族自治区": "广西", "西藏自治区": "西藏",
    "宁夏回族自治区": "宁夏", "新疆维吾尔自治区": "新疆",
}
REGION_TO_PROVINCE = {region: province for province, region in PROVINCE_TO_REGION.items()}


def text(value) -> str:
    """字段有时是列表、有时带高亮标签，取第一个非空值并去掉标签。"""
    if isinstance(value, list):
        value = next((v for v in value if v), "")
    return re.sub(r"<[^>]*>", "", str(value or "")).strip()


def region_of(province: str) -> str | None:
    """「湖北省」「宁夏回族自治区」→「湖北」「宁夏」；不在地区表里返回 None。"""
    name = text(province)
    if name in PROVINCE_TO_REGION:
        return PROVINCE_TO_REGION[name]
    # 检索接口偶尔给不带行政区的写法
    return next((r for p, r in PROVINCE_TO_REGION.items() if p.startswith(name) and name), None)


class GovLocalRegulationProvider(GovRegulationDatabaseProvider):
    """地方政府规章。字段与部门规章相同，只是类别不同、原文不在 gov.cn。"""

    CLASS = "f_202321807875"
    LOCAL_CLASS = "地方政府规章"
    PROVINCE = "f_202321423473"
    CITY = "f_20232380533"

    async def search(self, page_num: int = 1, region: str | None = None, search_word: str = "",
                     page_size: int = 50) -> Page:
        """地方政府规章，按公布日期从新到旧排列。region 为空时取全部地区。"""
        fields = [{"fieldName": self.CLASS, "searchWord": self.LOCAL_CLASS, "searchType": "TERM"}]
        if region:
            fields.insert(0, {"fieldName": self.PROVINCE,
                              "searchWord": REGION_TO_PROVINCE.get(region, region), "searchType": "TERM"})
        if search_word:
            fields.append({"fieldName": self.FULL_TEXT, "searchWord": search_word, "withHighLight": True})
        payload = {
            "code": "18258ab0ac9",
            "searchFields": fields,
            "sorts": [{"sortField": self.PUBLISHED_AT, "sortOrder": "DESC"}],
            "resultFields": [self.TITLE, self.NOTE, self.CLASS, self.KIND, self.DEPARTMENT_NAME,
                             self.PROVINCE, self.CITY, self.PUBLISHED_AT, self.URL_FIELD],
            "tableName": "t_1860c735d31",
            "trackTotalHits": "true",
            "pageNo": page_num,
            "pageSize": str(page_size),
            "granularity": "ALL",
        }
        data = (await net.get_json("POST", self.URL, headers=self.headers(), json=payload))["result"]["data"]

        items = []
        for row in data.get("list", []):
            url = text(row.get(self.URL_FIELD))
            if not url:
                continue
            name = region or region_of(text(row.get(self.PROVINCE)))
            if not name:
                continue
            publish, effective = dates_from_note(row.get(self.NOTE))
            published_at = text(row.get(self.PUBLISHED_AT))
            items.append(LawItem(
                id=url,
                title=text(row.get(self.TITLE)),
                released_by=text(row.get(self.DEPARTMENT_NAME)) or text(row.get(self.CITY)) or name,
                publication_date=publish or published_at[:10] or None,
                in_effect_date=effective,
                # 写到 out/<地区>地方法规/地方性法规/<地区>/，省级和市级规章都在这个地区的目录下
                folder=f"{dlc_name(name)}/地方性法规/{name}",
                file_link=url,
                note=row.get(self.NOTE) or None,
            ))
        return Page(total=data["pager"]["total"], items=items)

    async def fetch_document(self, item: LawItem) -> Path:
        url = item.file_link
        scheme, host = urlparse(url).scheme, urlparse(url).netloc
        upgraded = False
        for attempt in range(len(WAF_DELAYS) + 1):
            await _wait_if_blocked(host)
            try:
                return await self._download_checked(url, host)
            except _HttpsStub:
                if upgraded:
                    raise EmptyContentError(f"内容为空（http→https 跳转桩）：{url}") from None
                upgraded = True
                scheme = "https"
                url = f"https://{url.split('://', 1)[-1]}"
                continue
            except DeadLinkError:
                raise
            except (EmptyContentError, aiohttp.ClientResponseError) as e:
                status = getattr(e, "status", None)
                if status is not None and status not in WAF_STATUS:
                    raise
                if attempt == len(WAF_DELAYS):
                    raise
                delay = _retry_after(getattr(e, "headers", None)) or WAF_DELAYS[attempt]
                _block_host(host, delay, f"返回 {status}" if status else str(e))
                await _warm_up(scheme, host)
        raise AssertionError("unreachable")

    async def _download_checked(self, url: str, host: str) -> Path:
        """下载并确认拿到的是正文；坏响应删掉缓存，让下次重跑还有机会。"""
        path = await self.cache.download(url, CacheType.HTML, headers=page_headers(url),
                                         filename=cache_filename(url))
        path = with_known_suffix(path)
        if path.suffix.lower() not in HTML_SUFFIXES:
            return path  # Word、EPUB 交给 parse_file

        label = classify_content(path.read_bytes())
        if label is None:
            return path

        path.unlink(missing_ok=True)
        if label == "http→https 跳转桩":
            raise _HttpsStub(url)
        if label == "失效页":
            raise DeadLinkError(f"链接失效：{url}")

        html = await browser.render_html(url, headers=page_headers(url))
        if html:
            target = self.cache.path_of(url, CacheType.HTML, cache_filename(url))
            if target.suffix.lower() not in HTML_SUFFIXES:
                target = target.with_suffix(".html")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(html, encoding="utf-8")
            return target
        if label == "JS 验证页":
            _hint_browser(host)
        raise EmptyContentError(f"内容为空（{label}）：{url}")
