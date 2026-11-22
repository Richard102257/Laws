import asyncio
import tempfile
import unittest
from pathlib import Path

from lawref.fetch.models import LawItem
from lawref.fetch.sources.xzal_case import (
    OTHER_CATEGORIES,
    REPOST_SOURCES,
    SPECIAL_SERIES,
    Case,
    XzfyCaseProvider,
    page_meta,
    split_cases,
)
from lawref.parse.base import ParseError


def page(*paras: str) -> str:
    body = "".join(f"<p>{p}</p>" for p in paras)
    return f'<html><body><div class="TRS_Editor">{body}</div></body></html>'


def repost_page(box: str, *paras: str) -> str:
    """转载页：版式各异，有的还把整篇正文包在一个 <p> 里。"""
    body = "".join(f"<p>{p}</p>" for p in paras)
    return f'<html><body><div class="{box}"><p><div>{body}</div></p></div></body></html>'


class SplitTest(unittest.TestCase):

    def test_toc_and_case_titles(self):
        # 开头连续的「案例N：标题」是目录；正文标题「案例N」后面可以分成几段
        cases = split_cases(page(
            "典型案例目录",
            "案例一：甲公司不服某局行政处罚行政复议案",
            "案例二：乙公司不服某局行政许可行政复议案",
            "案例一",
            "甲公司不服某局",
            "行政处罚行政复议案",
            "【关键词】",
            "罚不相当",
            "【基本案情】",
            "案件内容",
            "【复议办理】",
            "办理内容",
            "案例二",
            "乙公司不服某局行政许可行政复议案",
            "【复议办理】",
            "办理内容",
        ))
        self.assertEqual([c.number for c in cases], ["一", "二"])
        self.assertEqual(cases[0].title, "甲公司不服某局行政处罚行政复议案")
        self.assertEqual(cases[1].title, "乙公司不服某局行政许可行政复议案")
        self.assertEqual(cases[0].lines[:3], ["【关键词】", "罚不相当", "【基本案情】"])
        self.assertIn("【复议办理】", cases[0].lines)
        self.assertTrue(cases[0].is_xzfy)

    def test_comment_and_number_are_dropped(self):
        cases = split_cases(page(
            "案例一",
            "甲公司不服某局行政处罚行政复议案",
            "【基本案情】",
            "案件内容",
            "案例一专家点评",
            "专家点评正文",
            "案例编号：SHGNZC1645584177",
            "案例二",
            "乙公司不服某局行政许可行政复议案",
            "【基本案情】",
            "案件内容",
        ))
        self.assertEqual(len(cases), 2)
        self.assertNotIn("专家点评正文", cases[0].lines)
        self.assertNotIn("案例编号：SHGNZC1645584177", cases[0].lines)
        self.assertEqual(cases[0].lines[-1], "案件内容")

    def test_case_intro(self):
        # 其他司法行政工作指导案例用「案例简介」而不是【】小节
        cases = split_cases(page(
            "案例一",
            "某仲裁委员会关于货物买卖合同纠纷仲裁案",
            "案例简介：",
            "案例内容",
            "案例编号：SHGNZC1645584177",
        ))
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].title, "某仲裁委员会关于货物买卖合同纠纷仲裁案")
        self.assertEqual(cases[0].lines, ["【案例简介】", "案例内容"])

    def test_mixed_batch_filters_litigation(self):
        # 两高联合发布的批次里，行政复议案例标「复议结果」，行政诉讼案例标「裁判结果」
        cases = split_cases(page(
            "案例一",
            "某公司不服某局行政处罚申请行政复议案",
            "【基本案情】",
            "案件内容",
            "【复议结果】",
            "复议内容",
            "案例二",
            "某公司诉某局行政允诺案",
            "【基本案情】",
            "案件内容",
            "【裁判结果】",
            "裁判内容",
        ))
        self.assertEqual(len(cases), 2)
        self.assertTrue(cases[0].is_xzfy)
        self.assertFalse(cases[1].is_xzfy)

    def test_bare_section_without_colon(self):
        cases = split_cases(page(
            "案例五：",
            "某戒毒人员教育矫治案例",
            "案例简介",
            "案例内容",
        ))
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].number, "五")
        self.assertEqual(cases[0].title, "某戒毒人员教育矫治案例")
        self.assertEqual(cases[0].lines, ["【案例简介】", "案例内容"])

    def test_nested_paragraphs_in_repost_page(self):
        # 有的转载页把整篇正文包在一个 <p> 里，只取最内层段落
        html = ('<html><body><div class="trs_paper_default"><p><div>'
                "<p>前言</p>"
                "<p>案例一</p><p>甲公司不服某局行政处罚行政复议案</p>"
                "<p>【基本案情】</p><p>案件内容</p>"
                "</div></p></div></body></html>")
        cases = split_cases(html)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].title, "甲公司不服某局行政处罚行政复议案")
        self.assertEqual(cases[0].lines, ["【基本案情】", "案件内容"])

    def test_repost_title_then_body_without_sections(self):
        # 早期批次没有小节名，标题后直接是正文，靠长度认出正文起点
        cases = split_cases(repost_page("v_news_content",
            "前言",
            "以上案例均可在12348中国法律服务网搜索查询。",
            "案例一：",
            "重庆市潼南区法律援助中心",
            "对谭某等19名 农民工劳动争议",
            "提供法律援助案",
            "谭某等19人系重庆市某科技股份有限公司生产部员工，双方签有书面劳动合同。"
            "2021年4月27日，谭某等人因公司拖欠工资向重庆市潼南区法律援助中心申请法律援助。",
            "第二段正文",
        ))
        self.assertEqual(len(cases), 1)
        # 转载页标题的排版空格去掉
        self.assertEqual(cases[0].title,
                         "重庆市潼南区法律援助中心对谭某等19名农民工劳动争议提供法律援助案")
        self.assertTrue(cases[0].lines[0]
                        .startswith("谭某等19人系重庆市某科技股份有限公司生产部员工"))
        self.assertNotIn("以上案例均可", cases[0].lines)

    def test_moj_title_keeps_spaces(self):
        cases = split_cases(page(
            "案例一",
            "八年攻坚磨一剑 斩断心魔塑新生",
            "【案例简介】",
            "案例内容",
        ))
        self.assertEqual(cases[0].title, "八年攻坚磨一剑 斩断心魔塑新生")

    def test_case_marker_with_enumeration_comma(self):
        # 「贯彻实施法律援助法典型案例」页通篇写「案例一、标题」
        cases = split_cases(page(
            "前言",
            "贯彻实施法律援助法典型案例",
            "案例一、北京市法律援助中心对马某确认劳动关系纠纷提供法律援助案",
            "【基本案情】",
            "案情内容",
            "案例二、上海市闵行区法律援助中心对刘某某工伤赔偿提供法律援助案",
            "【基本案情】",
            "案情内容",
        ))
        self.assertEqual([c.number for c in cases], ["一", "二"])
        self.assertEqual(cases[0].title,
                         "北京市法律援助中心对马某确认劳动关系纠纷提供法律援助案")
        self.assertEqual(cases[0].lines, ["【基本案情】", "案情内容"])

    def test_fwzk_layout_table_box(self):
        # 2021 环境损害司法鉴定转载页：正文在 .editor_content_air 的版式表格里，
        # 编号写「案例1」，末尾是网站联系方式
        html = ('<html><body><div class="editor_content_air"><table><tr><td>'
                "<p>案例1</p>"
                "<p>非法捕捞野生中华鲟鉴定案</p>"
                "<p>关键词：司法鉴定 中华鲟</p>"
                "<p>案情概况</p>"
                "<p>正文段落。</p>"
                "<p>邮箱：yanxue@sdu.edu.cn</p>"
                "<p>电话：0531-66909661</p>"
                "</td></tr></table></div></body></html>")
        cases = split_cases(html)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].number, "1")
        self.assertEqual(cases[0].title, "非法捕捞野生中华鲟鉴定案")
        self.assertEqual(cases[0].lines,
                         ["【关键词】", "司法鉴定 中华鲟", "案情概况", "正文段落。"])

    def test_repost_page_without_marker_is_one_case(self):
        # 有的转载页去掉了「案例N」标记，整页一案；正文在版式表格里
        long_body = "正文" * 300
        html = ('<html><body><div class="editor_content_air"><div class="TRS_Editor">'
                "<table><tr><td>"
                "<p>超标排污污染长江水环境生态环境损害鉴定案</p>"
                "<p>关键词：司法鉴定 超标排污</p>"
                "<p>案情概况</p>"
                f"<p>{long_body}</p>"
                "</td></tr></table></div></div></body></html>")
        cases = split_cases(html)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].title, "超标排污污染长江水环境生态环境损害鉴定案")
        self.assertEqual(cases[0].lines[:3],
                         ["【关键词】", "司法鉴定 超标排污", "案情概况"])
        self.assertEqual(cases[0].lines[3], long_body)


class MetaTest(unittest.TestCase):

    def test_page_meta(self):
        date, source = page_meta("来源：司法部官网 发布时间：2024-06-12 10:00",
                                 "https://www.moj.gov.cn/x.html")
        self.assertEqual(date, "2024-06-12")
        self.assertEqual(source, "司法部官网")

    def test_date_from_url(self):
        date, _ = page_meta("没有来源行", "https://www.moj.gov.cn/pub/sfbgw/gwxw/xwyw/202406/t20240619_500658.html")
        self.assertEqual(date, "2024-06-19")


class ProviderTest(unittest.TestCase):

    def test_parse_document_by_title(self):
        html = page(
            "案例一",
            "甲公司不服某局行政处罚行政复议案",
            "【基本案情】",
            "案件内容",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case.html"
            path.write_text(html, encoding="utf-8")
            p = XzfyCaseProvider()
            doc = asyncio.run(p.parse_document(path, "甲公司不服某局行政处罚行政复议案"))
        self.assertEqual(doc.title, "甲公司不服某局行政处罚行政复议案")
        self.assertEqual(doc.lines, ["【基本案情】", "案件内容"])

    def test_parse_document_matches_full_title(self):
        html = page("案例一", "甲公司不服某局行政处罚行政复议案", "【基本案情】", "案件内容")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case.html"
            path.write_text(html, encoding="utf-8")
            p = XzfyCaseProvider()
            doc = asyncio.run(p.parse_document(path, "指导性案例1号：甲公司不服某局行政处罚行政复议案"))
        self.assertEqual(doc.title, "甲公司不服某局行政处罚行政复议案")

    def test_parse_document_missing(self):
        html = page("案例一", "甲公司不服某局行政处罚行政复议案", "【基本案情】", "案件内容")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case.html"
            path.write_text(html, encoding="utf-8")
            p = XzfyCaseProvider()
            with self.assertRaises(ParseError):
                asyncio.run(p.parse_document(path, "不存在的案例"))


class CaseTest(unittest.TestCase):

    def test_is_xzfy_only_for_review_sections(self):
        self.assertTrue(Case("一", "某案", ["基本案情", "复议结果"], []).is_xzfy)
        self.assertTrue(Case("一", "某案", ["基本案情", "复议办理"], []).is_xzfy)
        self.assertFalse(Case("一", "某案", ["基本案情", "裁判结果"], []).is_xzfy)


class RepostTest(unittest.TestCase):

    def test_note_marks_source_and_date(self):
        repost = REPOST_SOURCES[0]
        item = LawItem(id=repost.url, title="", released_by="", folder="",
                       publication_date=repost.date, file_link=repost.url)
        note = asyncio.run(XzfyCaseProvider().note(item))
        self.assertIn(f"{repost.date[:4]}年{int(repost.date[5:7])}月{int(repost.date[8:])}日",
                      note)
        self.assertIn("司法部发布", note)
        self.assertIn(repost.source, note)

    def test_note_empty_for_moj_page(self):
        item = LawItem(id="", title="", released_by="", folder="", publication_date="2023-01-11",
                       file_link="https://www.moj.gov.cn/pub/sfbgw/gwxw/xwyw/202301/t20230111_470637.html")
        self.assertEqual(asyncio.run(XzfyCaseProvider().note(item)), "")

    def test_repost_urls_are_unique(self):
        urls = [r.url for r in REPOST_SOURCES]
        self.assertEqual(len(urls), len(set(urls)))

    def test_env_series_has_ten_cases(self):
        env = [r for r in REPOST_SOURCES
               if r.category == "司法鉴定" and r.date == "2021-06-05"]
        self.assertEqual(len(env), 10)
        self.assertEqual(len({r.url for r in env}), 10)
        self.assertTrue(all(r.url.startswith("http://www.fwzk.cn/doc_") for r in env))

    def test_manifest_category_names_are_stable(self):
        # 类别名沿用后来批次的叫法；「人民调解」「律师」只有早期批次，是新增目录
        names = {name for name, _ in OTHER_CATEGORIES} | {"人民调解", "律师"}
        for repost in REPOST_SOURCES:
            self.assertIn(repost.category, names)

    def test_special_series_urls_are_unique(self):
        urls = [url for _, url in SPECIAL_SERIES]
        self.assertEqual(len(urls), len(set(urls)))

    def test_special_series_categories_are_known_or_new_topics(self):
        known = {name for name, _ in OTHER_CATEGORIES} | {"行政执法监督", "涉企执法为民办实事",
                                                           "涉企执法典型案例"}
        for name, _ in SPECIAL_SERIES:
            self.assertIn(name, known)


if __name__ == "__main__":
    unittest.main()
