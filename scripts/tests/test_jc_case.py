import asyncio
import tempfile
import unittest
from pathlib import Path

from lawref.fetch.sources.jc_case import (
    EXTRA_PAGES,
    JcCaseProvider,
    cn2int,
    index_links,
    parse_page,
)
from lawref.parse.base import ParseError


def modern_page() -> str:
    paras = [
        "关于印发最高人民检察院第六十三批指导性案例的通知",
        "各省、自治区、直辖市人民检察院，解放军军事检察院，新疆生产建设兵团人民检察院：",
        "经2026年4月3日最高人民检察院第十四届检察委员会第七十四次会议决定，"
        "现将“陈某抢劫再审抗诉案”等五件案例（检例第257—261号）作为第六十三批指导性案例发布。",
        "最高人民检察院",
        "2026年7月15日",
        "陈某抢劫再审抗诉案",
        "（检例第257号）",
        "【关键词】",
        "再审抗诉　抢劫",
        "【要旨】",
        "要旨内容",
        "办案检察院：河北省人民检察院",
        "承办检察官：张三",
        "李某盗窃案",
        "（检例第258号）",
        "【要旨】",
        "要旨内容二",
    ]
    body = "".join(f"<p>{p}</p>" for p in paras)
    return ("<html><head><title>第六十三批指导性案例_中华人民共和国最高人民检察院</title></head>"
            "<body><div class=\"detail_extend1\">时间：2026-08-05　作者：　来源：高检网</div>"
            f'<div id="fontzoom">{body}</div></body></html>')


class CnTest(unittest.TestCase):

    def test_cn2int(self):
        self.assertEqual(cn2int("六十三"), 63)
        self.assertEqual(cn2int("二十五"), 25)
        self.assertEqual(cn2int("257"), 257)
        self.assertEqual(cn2int("一百零一"), 101)


class ParsePageTest(unittest.TestCase):

    def test_modern_page(self):
        page = parse_page(modern_page(), "https://www.spp.gov.cn/spp/jczdal/202608/t20260805_733868.shtml")
        self.assertEqual(page.batch, 63)
        self.assertEqual(page.name, "第六十三批指导性案例")
        self.assertEqual(page.date, "2026-08-05")
        self.assertEqual(page.source, "高检网")
        self.assertEqual(
            page.note,
            "（2026年4月3日最高人民检察院第十四届检察委员会第七十四次会议决定　"
            "2026年7月15日最高人民检察院印发第六十三批指导性案例）")
        self.assertEqual([c.number for c in page.cases], ["257", "258"])
        self.assertEqual(page.cases[0].title, "陈某抢劫再审抗诉案")
        self.assertEqual(page.cases[1].title, "李某盗窃案")
        # 署名行丢掉，正文到下一案的标题为止
        self.assertEqual(page.cases[0].lines,
                         ["【关键词】", "再审抗诉 抢劫", "【要旨】", "要旨内容"])
        self.assertEqual(page.cases[1].lines, ["【要旨】", "要旨内容二"])

    def test_old_page_date_from_url(self):
        html = ('<html><head><title>第一批指导性案例_中华人民共和国最高人民检察院</title></head>'
                '<body><div id="fontzoom"><div>施某某等17人聚众斗殴案<br>（检例第1号）'
                "<br>【要旨】<br>要旨内容</div></div></body></html>")
        page = parse_page(html, "https://www.spp.gov.cn/spp/jczdal/201608/t20160811_162301.shtml")
        self.assertEqual(page.batch, 1)
        self.assertEqual(page.date, "2016-08-11")
        self.assertEqual(page.source, "高检网")
        self.assertEqual(page.note, "（2016年8月11日最高人民检察院发布第一批指导性案例）")
        self.assertEqual(page.cases[0].full_title, "检例第1号：施某某等17人聚众斗殴案")
        self.assertEqual(page.cases[0].lines, ["【要旨】", "要旨内容"])

    def test_spaced_section_is_normalized(self):
        html = ('<html><head><title>第三批指导性案例_中华人民共和国最高人民检察院</title></head>'
                '<body><div id="fontzoom"><div>李泽强编造虚假恐怖信息案<br>（检例第9号）'
                "<br>【要 旨】<br>要旨内容</div></div></body></html>")
        page = parse_page(html, "https://www.spp.gov.cn/x.shtml")
        self.assertEqual(page.cases[0].lines, ["【要旨】", "要旨内容"])

    def test_stop_cuts_following_article(self):
        html = modern_page().replace(
            "</div></body>",
            "<p>强化刑事立案监督 依法保障非公经济健康发展</p>"
            "<p>答记者问正文</p></div></body>")
        page = parse_page(html, "https://www.spp.gov.cn/x.shtml", EXTRA_PAGES[1][1])
        self.assertEqual(page.cases[-1].lines, ["【要旨】", "要旨内容二"])

    def test_extra_stop_regex(self):
        html = modern_page().replace(
            "</div></body>", "<p>行政检察典型案例</p><p>其他案例正文</p></div></body>")
        page = parse_page(html, "https://www.spp.gov.cn/x.shtml", EXTRA_PAGES[0][1])
        self.assertEqual(page.cases[-1].lines, ["【要旨】", "要旨内容二"])

    def test_extra_page_date_override_and_stop(self):
        # 第 22 批只有地方检察院全文页：页面日期 2020-12-08，文件名用印发日；
        # 来源栏写的是「作者：」，要回退成高检网；正文后的 CSS/脚本按 stop 截掉
        url, stop, date = EXTRA_PAGES[2]
        html = ('<html><head><title>最高检第二十二批指导性案例（全文）</title></head>'
                '<body><div id="fontzoom">'
                "<p>关于印发最高人民检察院第二十二批指导性案例的通知</p>"
                "<p>经2020年9月28日最高人民检察院第十三届检察委员会第五十二次会议决定，"
                "现将无锡F警用器材公司虚开增值税专用发票案等四件指导性案例（检例第81-84号）发布。</p>"
                "<p>2020年11月24日</p>"
                "<p>无锡F警用器材公司虚开增值税专用发票案</p>"
                "<p>（检例第81号）</p>"
                "<p>【要旨】</p><p>要旨内容</p>"
                "<p>【作者】：张三</p>"
                "<p>footer { color: red; }</p>"
                "<p>params.doctitle='x';</p>"
                "</div>"
                '<div class="detail_extend1">时间：2020-12-08　来源：作者：</div>'
                "</body></html>")
        page = parse_page(html, url, stop, date)
        self.assertEqual(page.batch, 22)
        self.assertEqual(page.date, "2020-11-24")
        self.assertEqual(page.source, "高检网")
        self.assertEqual(page.note,
                         "（2020年9月28日最高人民检察院第十三届检察委员会第五十二次会议决定　"
                         "2020年11月24日最高人民检察院印发第二十二批指导性案例）")
        self.assertEqual([c.number for c in page.cases], ["81"])
        self.assertEqual(page.cases[0].lines, ["【要旨】", "要旨内容"])


class IndexLinksTest(unittest.TestCase):

    def test_links(self):
        html = ('<html><body><a href="/spp/jczdal/202608/t20260805_733868.shtml">第六十三批指导性案例</a>'
                '<a href="/spp/jczdal/202603/t20260303_721548.shtml">第六十二批指导性案例</a>'
                '<a href="/spp/jgsz/index.shtml">机构设置</a></body></html>')
        self.assertEqual(index_links(html), [
            ("https://www.spp.gov.cn/spp/jczdal/202608/t20260805_733868.shtml", 63),
            ("https://www.spp.gov.cn/spp/jczdal/202603/t20260303_721548.shtml", 62),
        ])


class ProviderTest(unittest.TestCase):

    def test_parse_document_by_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.html"
            path.write_text(modern_page(), encoding="utf-8")
            p = JcCaseProvider()
            doc = asyncio.run(p.parse_document(path, "检例第258号"))
        self.assertEqual(doc.title, "检例第258号：李某盗窃案")
        self.assertEqual(doc.lines, ["【要旨】", "要旨内容二"])

    def test_parse_document_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.html"
            path.write_text(modern_page(), encoding="utf-8")
            p = JcCaseProvider()
            with self.assertRaises(ParseError):
                asyncio.run(p.parse_document(path, "检例第999号"))


if __name__ == "__main__":
    unittest.main()
