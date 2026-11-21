import tempfile
import unittest
from pathlib import Path
from unittest import mock

import aiohttp

from lawref.fetch.cache import DownloadCache
from lawref.fetch.errors import DeadLinkError, EmptyContentError
from lawref.fetch.models import LawItem
from lawref.fetch.sources import gov_local
from lawref.fetch.sources.gov_local import (
    PROVINCE_TO_REGION,
    GovLocalRegulationProvider,
    cache_filename,
    classify_content,
    region_of,
    with_known_suffix,
)
from lawref.regions import REGION_CODE_MAP


class RegionTest(unittest.TestCase):

    def test_region_of(self):
        self.assertEqual(region_of("湖北省"), "湖北")
        self.assertEqual(region_of("宁夏回族自治区"), "宁夏")
        self.assertEqual(region_of("内蒙古自治区"), "内蒙古")
        self.assertEqual(region_of("<em>上海市</em>"), "上海")
        self.assertIsNone(region_of("香港特别行政区"))

    def test_all_provinces(self):
        # 31 个地区都有对应的省名，按名称或编号指定时不会漏
        self.assertEqual(set(PROVINCE_TO_REGION.values()), set(REGION_CODE_MAP))

    def test_cache_filename(self):
        self.assertEqual(cache_filename("https://a.gov.cn/x/1.html"), "1.html")
        self.assertEqual(cache_filename("https://a.gov.cn/x/1.html?y=2"), "1.html")
        fallback = cache_filename("https://a.gov.cn/detail/")
        self.assertTrue(fallback)
        self.assertNotIn("/", fallback)

    def test_with_known_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / "66"
            html.write_bytes(b"<!DOCTYPE html><html><body>x</body></html>")
            self.assertEqual(with_known_suffix(html), Path(tmp) / "66.html")
            docx = Path(tmp) / "a.jhtml"
            docx.write_bytes(b"PK\x03\x04rest")
            self.assertEqual(with_known_suffix(docx), Path(tmp) / "a.docx")
            fragment = Path(tmp) / "8888"
            fragment.write_bytes(b"\r\n<div class='x'><p>y</p></div>")
            self.assertEqual(with_known_suffix(fragment), Path(tmp) / "8888.html")
            known = Path(tmp) / "b.html"
            known.write_bytes(b"<html></html>")
            self.assertEqual(with_known_suffix(known), known)


DATA = {"pager": {"total": 2}, "list": [
    {"f_202321360426": "武汉市<em>江滩</em>管理办法", "f_202344311304": None,
     "doc_pub_url": "https://www.wuhan.gov.cn/x.shtml", "f_202321807875": "地方政府规章",
     "f_202321864401": 3, "f_202321915922": "2026-11-03 11:10:00",
     "f_202321423473": "<em>湖北省</em>", "f_20232380533": "武汉"},
    {"f_202321360426": "湖北省某办法", "f_202344311304": None,
     "doc_pub_url": "https://www.gov.cn/zhengce/202606/content_1.htm", "f_202321807875": "地方政府规章",
     "f_202321864401": 2, "f_202321915922": "2026-06-06 00:00:00",
     "f_202321423473": ["湖北省"], "f_20232380533": None},
]}


class SearchTest(unittest.IsolatedAsyncioTestCase):

    async def test_search_region(self):
        p = GovLocalRegulationProvider()
        with mock.patch("lawref.fetch.net.get_json", return_value={"result": {"data": DATA}}) as get_json:
            page = await p.search(2, region="湖北", page_size=5)
        payload = get_json.call_args.kwargs["json"]
        # 按类别取，省级（KIND=2）和市级（KIND=3）都在内；省份用全称检索
        self.assertEqual([(f["fieldName"], f["searchWord"]) for f in payload["searchFields"]],
                         [("f_202321423473", "湖北省"), ("f_202321807875", "地方政府规章")])
        self.assertEqual((payload["pageNo"], payload["pageSize"]), (2, "5"))
        self.assertEqual(page.total, 2)
        first, second = page.items
        self.assertEqual(first.title, "武汉市江滩管理办法")
        self.assertEqual(first.folder, "湖北地方法规/地方性法规/湖北")
        self.assertEqual(first.publication_date, "2026-11-03")
        self.assertEqual(second.folder, "湖北地方法规/地方性法规/湖北")

    async def test_search_without_region_uses_row_province(self):
        p = GovLocalRegulationProvider()
        with mock.patch("lawref.fetch.net.get_json", return_value={"result": {"data": DATA}}):
            page = await p.search()
        self.assertEqual({item.folder for item in page.items}, {"湖北地方法规/地方性法规/湖北"})


VALID_HTML = b"<html><body><p>" + "法规正文".encode() * 100 + b"</p></body></html>"


class DownloadTest(unittest.IsolatedAsyncioTestCase):

    def error(self, status: int) -> aiohttp.ClientResponseError:
        return aiohttp.ClientResponseError(request_info=None, history=(), status=status)

    def provider(self, side_effect, cache_dir: Path | None = None) -> GovLocalRegulationProvider:
        p = GovLocalRegulationProvider(DownloadCache(cache_dir) if cache_dir else None)
        p.cache.download = mock.AsyncMock(side_effect=side_effect)
        return p

    def item(self, url: str = "https://a.gov.cn/x") -> LawItem:
        return LawItem(id="u", title="x", released_by="", folder="f", file_link=url)

    async def test_waf_status_is_retried(self):
        item = self.item("https://a.gov.cn/detail/")
        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / "page.html"
            html.write_bytes(VALID_HTML)
            p = self.provider([self.error(412), html])
            with mock.patch.object(gov_local, "_wait_if_blocked", mock.AsyncMock()):
                self.assertEqual(await p.fetch_document(item), html)
            self.assertEqual(p.cache.download.await_count, 2)

    async def test_other_status_fails_immediately(self):
        p = self.provider(self.error(404))
        with (mock.patch.object(gov_local, "_wait_if_blocked", mock.AsyncMock()),
              self.assertRaises(aiohttp.ClientResponseError)):
            await p.fetch_document(self.item())
        self.assertEqual(p.cache.download.await_count, 1)

    async def test_http_stub_upgraded_to_https(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "stub.html"
            stub.write_bytes(b'<html><script>var targetProtocol = "https:"</script></html>')
            good = Path(tmp) / "good.html"
            good.write_bytes(VALID_HTML)
            urls = []

            async def fake_download(url, kind, headers=None, filename=None):
                urls.append(url)
                return stub if len(urls) == 1 else good

            cache = DownloadCache(Path(tmp))
            cache.download = fake_download
            p = GovLocalRegulationProvider(cache)
            with mock.patch.object(gov_local, "_wait_if_blocked", mock.AsyncMock()):
                result = await p.fetch_document(self.item("http://a.gov.cn/x"))
            self.assertEqual(result.read_bytes(), VALID_HTML)
            self.assertTrue(urls[0].startswith("http://"))
            self.assertTrue(urls[1].startswith("https://"))

    async def test_invalid_page_is_dead_link_and_purged(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.html"
            bad.write_text("<html><body>无效的访问地址</body></html>", encoding="utf-8")
            p = self.provider([bad])
            with (mock.patch.object(gov_local, "_wait_if_blocked", mock.AsyncMock()),
                  self.assertRaises(DeadLinkError)):
                await p.fetch_document(self.item())
            self.assertFalse(bad.exists())

    async def test_empty_page_retried_and_purged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.html"

            async def fresh(*args, **kwargs):
                path.write_bytes(b"<html></html>")
                return path

            p = self.provider(fresh)
            with (mock.patch.object(gov_local, "_wait_if_blocked", mock.AsyncMock()),
                  mock.patch.object(gov_local, "_warm_up", mock.AsyncMock()),
                  mock.patch.object(gov_local.browser, "render_html", mock.AsyncMock(return_value=None)),
                  self.assertRaises(EmptyContentError)):
                await p.fetch_document(self.item())
            self.assertEqual(p.cache.download.await_count, len(gov_local.WAF_DELAYS) + 1)

    async def test_challenge_page_falls_back_to_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "challenge.html"
            page.write_bytes(b"<html><body>wafdirection</body></html>")
            cache = DownloadCache(Path(tmp))
            cache.download = mock.AsyncMock(return_value=page)
            rendered = VALID_HTML.decode("utf-8")
            p = GovLocalRegulationProvider(cache)
            with (mock.patch.object(gov_local, "_wait_if_blocked", mock.AsyncMock()),
                  mock.patch.object(gov_local.browser, "render_html",
                                    mock.AsyncMock(return_value=rendered))):
                result = await p.fetch_document(self.item())
            self.assertEqual(result.read_text(encoding="utf-8"), rendered)


class ClassifyContentTest(unittest.TestCase):

    def test_markers(self):
        self.assertEqual(classify_content(b'<script>var targetProtocol = "https:"</script>'),
                         "http→https 跳转桩")
        self.assertEqual(classify_content(b"<html><body>wafdirection</body></html>"), "JS 验证页")
        self.assertEqual(classify_content("无效的访问地址".encode()), "失效页")
        self.assertEqual(classify_content(b"<html></html>"), "内容过少")
        self.assertIsNone(classify_content(VALID_HTML))
