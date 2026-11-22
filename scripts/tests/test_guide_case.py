import asyncio
import tempfile
import unittest
from pathlib import Path

from lawref.fetch.sources.guide_case import (
    BASE,
    GuideCaseProvider,
    detail_meta,
    index_entries,
    parse_detail,
)
from lawref.parse.base import ParseError

INDEX = """<html><body><div class="sec_list"><ul>
<li><a title="指导性案例279号：西某工业软件有限公司诉广州沃某模具有限公司侵害计算机软件著作权纠纷案"
   href="/shenpan/xiangqing/490521.html">指导性案例279号：西某…</a><i class="date">2026-02-28</i></li>
<li><a title=" 指导性案例231号：东莞市丰某海运有限公司诉东营市鑫某物流有限责任公司

    海难救助纠纷案 " href="/shenpan/xiangqing/448611.html">指导性案例231号：东莞市…</a><i class="date">2024-11-26</i></li>
<li><a href="/shenpan/gengduo/77_2.html">下一页</a><i class="date"></i></li>
</ul></div></body></html>"""

DETAIL = """<html><head><title>指导性案例279号 - 中华人民共和国最高人民法院</title></head><body>
<div class="txt_txt"><div>指导性案例279号<br>西某工业软件有限公司诉广州沃某模具有限公司侵害<br>
计算机软件著作权纠纷案<br>（最高人民法院审判委员会讨论通过2026年2月28日发布）<br>
关键词<br>民事/侵害计算机软件著作权<br>裁判要点<br>要点内容<br>
相关法条<br>《中华人民共和国著作权法》第10条<br>（生效裁判审判人员：张三、李四）</div></div>
</body></html>"""

DETAIL_HALF = """<html><body><div class="txt_txt"><div>指导案例51号<br>
阿卜杜勒·瓦希德诉中国东方航空股份有限公司航空旅客运输合同纠纷案<br>
(最高人民法院审判委员会讨论通过2015年4月15日发布)<br>
关键词 民事 航空旅客运输合同<br>裁判要点<br>要点内容</div></div></body></html>"""

# 公报合刊页：没有 div.txt_txt，一页含 45–52 号，标题和案名写在同一行
GONGBAO = """<html><head><title>最高人民法院公报</title></head><body>
<div>指导案例45号 北京百度网讯科技有限公司诉青岛奥商网络技术有限公司等不正当竞争纠纷案<br>
（最高人民法院审判委员会讨论通过2015年4月15日发布）<br>
关键词<br>民事 不正当竞争 网络服务<br>裁判要点<br>要点内容<br>
指导案例46号 山东鲁锦实业有限公司诉鄄城县鲁锦工艺品有限责任公司侵害商标权案<br>
（最高人民法院审判委员会讨论通过2015年4月15日发布）<br>
关键词<br>其他内容</div>
</body></html>"""


class IndexTest(unittest.TestCase):

    def test_entries(self):
        self.assertEqual(index_entries(INDEX), [
            (279, "西某工业软件有限公司诉广州沃某模具有限公司侵害计算机软件著作权纠纷案",
             f"{BASE}/shenpan/xiangqing/490521.html", "2026-02-28"),
            (231, "东莞市丰某海运有限公司诉东营市鑫某物流有限责任公司 海难救助纠纷案",
             f"{BASE}/shenpan/xiangqing/448611.html", "2024-11-26"),
        ])

    def test_pagination_is_ignored(self):
        # 「下一页」没有案例编号，不算条目
        self.assertEqual(len(index_entries(INDEX)), 2)


class ParseDetailTest(unittest.TestCase):

    def test_modern_page(self):
        doc = parse_detail(DETAIL, "https://www.court.gov.cn/shenpan/xiangqing/490521.html")
        self.assertEqual(doc.title, "指导性案例279号：西某工业软件有限公司诉广州沃某模具有限公司侵害计算机软件著作权纠纷案")
        self.assertEqual(doc.note, "（2026年2月28日 最高人民法院审判委员会讨论通过并发布）")
        self.assertEqual(doc.lines, ["关键词", "民事/侵害计算机软件著作权", "裁判要点",
                                     "要点内容", "相关法条", "《中华人民共和国著作权法》第10条"])

    def test_inline_section_and_half_width_parens(self):
        doc = parse_detail(DETAIL_HALF)
        self.assertEqual(doc.title, "指导性案例51号：阿卜杜勒·瓦希德诉中国东方航空股份有限公司航空旅客运输合同纠纷案")
        self.assertEqual(doc.note, "（2015年4月15日 最高人民法院审判委员会讨论通过并发布）")
        self.assertEqual(doc.lines, ["关键词", "民事 航空旅客运输合同", "裁判要点", "要点内容"])

    def test_missing_heading(self):
        with self.assertRaises(ParseError):
            parse_detail("<html><body><div class='txt_txt'>没有编号</div></body></html>")

    def test_gongbao_multi_case_page(self):
        # 合刊页一案接一案，标题和案名同行；按编号只取对应那一段
        doc = parse_detail(GONGBAO, "https://gongbao.court.gov.cn/x", 45)
        self.assertEqual(doc.title,
                         "指导性案例45号：北京百度网讯科技有限公司诉青岛奥商网络技术有限公司等不正当竞争纠纷案")
        self.assertEqual(doc.note, "（2015年4月15日 最高人民法院审判委员会讨论通过并发布）")
        self.assertEqual(doc.lines, ["关键词", "民事 不正当竞争 网络服务",
                                     "裁判要点", "要点内容"])
        first = parse_detail(GONGBAO, "https://gongbao.court.gov.cn/x")
        self.assertTrue(first.title.startswith("指导性案例45号"))
        second = parse_detail(GONGBAO, "https://gongbao.court.gov.cn/x", 46)
        self.assertEqual(second.title,
                         "指导性案例46号：山东鲁锦实业有限公司诉鄄城县鲁锦工艺品有限责任公司侵害商标权案")

    def test_gongbao_missing_number(self):
        with self.assertRaises(ParseError):
            parse_detail(GONGBAO, "https://gongbao.court.gov.cn/x", 47)


class MetaTest(unittest.TestCase):

    def test_detail_meta(self):
        html = ("<html><body><div>来源：最高人民法院新闻局</div>"
                "<div>发布时间：2026-02-28 18:50:04</div></body></html>")
        self.assertEqual(detail_meta(html), ("2026-02-28", "最高人民法院新闻局"))


class ProviderTest(unittest.TestCase):

    def test_items_from_index_pages(self):
        pages = {"": INDEX, "_2": INDEX, "_3": ""}
        with tempfile.TemporaryDirectory() as tmp:
            for suffix, html in pages.items():
                (Path(tmp) / f"guide_index{suffix}.html").write_text(html, encoding="utf-8")
            p = GuideCaseProvider()

            async def fake(suffix: str) -> Path:
                return Path(tmp) / f"guide_index{suffix}.html"

            p._fetch_index = fake
            items = asyncio.run(p.items())
        # 索引缺的第 45 号由 EXTRA_CASES 补齐，排在最前
        self.assertEqual([(i.title, i.publication_date) for i in items],
                         [("指导案例45号", "2015-04-15"), ("指导案例231号", "2024-11-26"),
                          ("指导案例279号", "2026-02-28")])
        extra = next(i for i in items if i.title == "指导案例45号")
        self.assertTrue(extra.full_title.startswith("指导性案例45号："))
        self.assertTrue(extra.file_link.startswith("http://gongbao.court.gov.cn/"))

    def test_parse_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "490521.html"
            path.write_text(DETAIL, encoding="utf-8")
            doc = asyncio.run(GuideCaseProvider().parse_document(path, "指导案例279号"))
        self.assertEqual(doc.title, "指导性案例279号：西某工业软件有限公司诉广州沃某模具有限公司侵害计算机软件著作权纠纷案")

    def test_parse_document_full_title(self):
        # heading 是「指导性案例N号：案名」时也要能取出编号（合刊页一案一页）
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gongbao.html"
            path.write_text(GONGBAO, encoding="utf-8")
            doc = asyncio.run(GuideCaseProvider().parse_document(
                path, "指导性案例45号：北京百度网讯科技有限公司诉青岛奥商网络技术有限公司等不正当竞争纠纷案"))
        self.assertTrue(doc.title.startswith("指导性案例45号"))


if __name__ == "__main__":
    unittest.main()
