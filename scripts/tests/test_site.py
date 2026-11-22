"""静态查询页生成器：Markdown 渲染、URL/状态计算与端到端构建。"""
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

SITE_DIR = Path(__file__).resolve().parents[2] / "site"
sys.path.insert(0, str(SITE_DIR))

from build_site import (  # noqa: E402
    assign_urls,
    build,
    compute_states,
    law_status,
    load_data,
    normalize_law_name,
    render_catalog,
    render_category,
    render_exam,
    render_home,
    render_law,
)
from markdownlite import render, split_document  # noqa: E402


class SplitDocumentTest(unittest.TestCase):

    def test_title_info_and_body(self):
        text = "# 中华人民共和国测试法\n\n2020年1月1日 通过\n\n<!-- INFO END -->\n\n第一条 内容\n"
        title, info, body = split_document(text)
        self.assertEqual(title, "中华人民共和国测试法")
        self.assertEqual(info, "2020年1月1日 通过")
        self.assertEqual(body, "第一条 内容")

    def test_without_marker_treats_rest_as_body(self):
        title, info, body = split_document("# 标题\n\n第一条 内容\n")
        self.assertEqual((title, info), ("标题", ""))
        self.assertIn("第一条 内容", body)

    def test_comments_removed(self):
        title, info, body = split_document("# 标题\n<!-- 注释 -->\n<!-- INFO END -->\n正文")
        self.assertNotIn("注释", title + info + body)


class RenderTest(unittest.TestCase):

    def test_headings_anchor_and_toc(self):
        result = render("## 第一章 总则\n\n正文\n\n### 第一节 细则\n")
        self.assertIn('id="第一章-总则"', result.html)
        self.assertIn('id="第一节-细则"', result.html)
        self.assertEqual([entry["level"] for entry in result.toc], [2, 3])

    def test_article_anchor(self):
        result = render("第一条 本法自公布之日起施行。\n\n第二条 另一条。\n")
        self.assertEqual(result.articles, ["第一条", "第二条"])
        self.assertIn('class="art"', result.html)
        self.assertIn('id="第一条"', result.html)

    def test_table_and_list(self):
        result = render("| 列 | 说明 |\n| --- | --- |\n| a | b |\n\n- 甲\n- 乙\n")
        self.assertIn("<table>", result.html)
        self.assertIn("<ul>", result.html)
        self.assertIn("<li>甲</li>", result.html)

    def test_inline_bold_link_and_escape(self):
        result = render("这是 **加粗** 和 [链接](https://example.com/a?b=1) ，还有 <script>。\n")
        self.assertIn("<strong>加粗</strong>", result.html)
        self.assertIn('href="https://example.com/a?b=1"', result.html)
        self.assertNotIn("<script>", result.html)

    def test_chinese_numbered_list_is_not_ordered_list(self):
        """“1.劳动合同…”这类正文行不应被当成 Markdown 有序列表。"""
        result = render("1.劳动合同和打卡记录\n2.谈话录音\n")
        self.assertNotIn("<ol>", result.html)


def _make_db(root: Path, folder: str, name: str, *, publish: str, valid_from: str,
             valid_to: str = "2099-12-31", filename: str | None = None) -> Path:
    db_path = root / "db.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE category (
            id TEXT PRIMARY KEY, name TEXT, folder TEXT, isSubFolder INTEGER,
            "group" TEXT, "order" INTEGER);
        CREATE TABLE law (
            id TEXT PRIMARY KEY, level TEXT, name TEXT, publish TEXT,
            "order" INTEGER, subtitle TEXT, valid_from TEXT, tags TEXT,
            category_id TEXT, valid_to TEXT, ver INTEGER, flags INTEGER,
            filename TEXT);
        CREATE TABLE relationship (
            source_id TEXT, target_id TEXT, type TEXT, date TEXT);
        """
    )
    category_id = "cat-1"
    conn.execute(
        'INSERT INTO category VALUES (?,?,?,?,?,?)',
        (category_id, folder.split("/")[-1], folder, 1, "法律法规", 1),
    )
    conn.execute(
        "INSERT INTO law VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"law-{name}", "法律", name, publish, None, None, valid_from, None,
         category_id, valid_to, 1, 0, filename),
    )
    conn.commit()
    conn.close()

    md = root / folder / f"{filename or f'{name}({publish[:10]})'}.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(f"# 中华人民共和国{name}\n\n{publish} 通过\n\n<!-- INFO END -->\n\n"
                  f"第一条 {name}第一条。\n", encoding="utf-8")
    return db_path


@pytest.fixture()
def mini_repo(tmp_path):
    """一个只有本体库的微型仓库，用于端到端构建。"""
    root = tmp_path / "Laws"
    root.mkdir()
    _make_db(root, "刑法", "测试法", publish="2020-01-01", valid_from="2020-02-01")
    _make_db(root, "民法典", "旧版法", publish="2019-01-01", valid_from="2019-02-01",
             valid_to="2020-06-30")
    (root / "法考汇编").mkdir()
    (root / "法考汇编" / "law_exam_structure.json").write_text(
        '{"schema_version":1,"source":"t","note":"","groups":[{"code":"g1","name":"刑法",'
        '"order":1,"laws":[{"code":"l1","name":"中华人民共和国测试法"}]}]}', encoding="utf-8")
    return root


class EndToEndTest(unittest.TestCase):

    def test_build_outputs_all_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Laws"
            root.mkdir()
            _make_db(root, "刑法", "测试法", publish="2020-01-01", valid_from="2020-02-01")
            out = Path(tmp) / "public"

            summary = build(root, out, base="/Laws")
            self.assertEqual(summary["laws"], 1)
            self.assertEqual(summary["missing"], 0)

            index_html = (out / "index.html").read_text(encoding="utf-8")
            self.assertIn('href="/Laws/catalog/index.html"', index_html)
            self.assertIn('src="/Laws/assets/app.js"', index_html)

            catalog = (out / "catalog" / "index.html").read_text(encoding="utf-8")
            self.assertIn("分类浏览", catalog)

            exam = (out / "exam" / "index.html").read_text(encoding="utf-8")
            self.assertIn("法考汇编", exam)

            self.assertTrue((out / "assets" / "search-index.json").is_file())
            self.assertTrue((out / "sitemap.xml").is_file())
            self.assertIn("测试法", (out / "assets" / "search-index.json").read_text(encoding="utf-8"))

    def test_missing_body_marks_not_shown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Laws"
            root.mkdir()
            _make_db(root, "刑法", "测试法", publish="2020-01-01", valid_from="2020-02-01")
            packs, laws, categories, relationships = load_data(root)
            (root / "刑法" / "测试法(2020-01-01).md").unlink()
            packs, laws, categories, relationships = load_data(root)
            assign_urls(laws, categories)
            compute_states(laws, relationships)
            self.assertFalse(laws[0].shown)

    def test_expired_law_is_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Laws"
            root.mkdir()
            _make_db(root, "刑法", "旧法", publish="2000-01-01", valid_from="2000-02-01",
                     valid_to="2020-01-01")
            packs, laws, categories, relationships = load_data(root)
            assign_urls(laws, categories)
            compute_states(laws, relationships)
            self.assertEqual(laws[0].status, "expired")
            self.assertFalse(laws[0].shown)

    def test_status_labels(self):
        from build_site import Law

        def make(**kwargs):
            base = dict(id="x", name="n", subtitle=None, filename=None, level="法律",
                        publish="2020-01-01", valid_from=None, valid_to="2099-12-31",
                        order=None, ver=1, flags=0, pack="main", pack_label="本体",
                        folder="刑法", category_name="刑法", group="法律法规")
            base.update(kwargs)
            return Law(**base)

        self.assertEqual(law_status(make(valid_to="2020-01-01"), False, False), "expired")
        self.assertEqual(law_status(make(valid_to="2020-01-01"), True, False), "revised")
        self.assertEqual(law_status(make(valid_to="2020-01-01"), False, True), "abolished")
        self.assertEqual(law_status(make(valid_from="2999-01-01"), False, False), "pending")
        self.assertEqual(law_status(make(), False, False), "current")


class CliTest(unittest.TestCase):

    def test_module_runs_as_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Laws"
            root.mkdir()
            _make_db(root, "刑法", "测试法", publish="2020-01-01", valid_from="2020-02-01")
            out = Path(tmp) / "public"
            proc = subprocess.run(
                [sys.executable, str(SITE_DIR / "build_site.py"), "--root", str(root),
                 "--out", str(out)],
                capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((out / "index.html").is_file())

    def test_refuses_to_overwrite_repo_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Laws"
            root.mkdir()
            proc = subprocess.run(
                [sys.executable, str(SITE_DIR / "build_site.py"), "--root", str(root),
                 "--out", str(root)],
                capture_output=True, text=True, timeout=120,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("拒绝覆盖", proc.stderr)


if __name__ == "__main__":
    unittest.main()
