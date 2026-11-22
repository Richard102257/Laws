"""法考法律汇编：结构导入、归一化匹配、幂等与手工匹配保护。"""
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from lawref.db import Database
from lawref.db.models import FOREVER, Category, Law
from lawref.exam import (
    DEFAULT_STRUCTURE,
    SCHEMA_VERSION,
    LawExamEntry,
    LawExamGroup,
    exam_overview,
    group_summaries,
    load_structure,
    normalize_law_name,
    sync_exam_structure,
)


def write_structure(root: Path, groups: list[dict], version: int = SCHEMA_VERSION) -> Path:
    path = root / "structure.json"
    path.write_text(json.dumps(
        {"schema_version": version, "source": "test", "note": "", "groups": groups},
        ensure_ascii=False), encoding="utf-8")
    return path


class NormalizeTest(unittest.TestCase):

    def test_country_prefix_parentheses_and_whitespace(self):
        self.assertEqual(normalize_law_name("中华人民共和国 刑法（含修正案）"), "刑法")
        self.assertEqual(normalize_law_name("中华人民共和国刑法"), "刑法")

    def test_structure_loads_and_validates_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_structure(Path(tmp), [
                {"code": "g1", "name": "刑法", "order": 2,
                 "laws": [{"code": "l1", "name": "中华人民共和国刑法"}]},
            ])
            groups = load_structure(path)
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].order, 2)
            self.assertEqual(groups[0].laws[0].name, "中华人民共和国刑法")

            bad = write_structure(Path(tmp), [], version=99)
            with self.assertRaises(ValueError):
                load_structure(bad)

    def test_default_structure_is_present(self):
        self.assertTrue(DEFAULT_STRUCTURE.is_file())


class SyncTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.structure = write_structure(self.root, [
            {"code": "g1", "name": "刑法", "order": 1, "laws": [
                {"code": "l1", "name": "中华人民共和国刑法"},
                {"code": "l2", "name": "中华人民共和国刑事诉讼法"},
                {"code": "l3", "name": "中华人民共和国 刑法（修正）"},
            ]},
            {"code": "g2", "name": "民法", "order": 2, "laws": [
                {"code": "l4", "name": "中华人民共和国民法典"},
            ]},
        ])

    def tearDown(self):
        self.tmp.cleanup()

    @contextmanager
    def store(self):
        with Database(self.root / "db.sqlite3") as database:
            yield database

    def seed_laws(self):
        category = Category.create(name="法律", folder="法律")
        new = Law.create(level="法律", name="刑法", publish="2020-12-26", ver=1,
                         valid_from="2021-03-01", valid_to=FOREVER, order=1,
                         category_id=category.id)
        old = Law.create(level="法律", name="刑法", publish="1997-03-14", ver=0,
                         valid_from="1997-10-01", valid_to="2020-12-25", order=0,
                         category_id=category.id)
        Law.create(level="法律", name="刑事诉讼法", publish="2018-10-26",
                   category_id=category.id)
        return new, old

    def entry(self, group_code, code):
        return LawExamEntry.select().join(LawExamGroup).where(
            LawExamGroup.code == group_code, LawExamEntry.code == code).get()

    def test_sync_matches_normalized_name_and_picks_current_version(self):
        with self.store():
            new, old = self.seed_laws()
            result = sync_exam_structure(structure=self.structure)
            self.assertEqual(result.groups_created, 2)
            self.assertEqual(result.entries_created, 4)
            self.assertEqual(result.matched, 3)
            self.assertEqual(result.unmatched, 1)

            entry = self.entry("g1", "l3")
            self.assertEqual(entry.law_id, new.id)
            self.assertNotEqual(entry.law_id, old.id)

    def test_second_sync_is_idempotent(self):
        with self.store():
            self.seed_laws()
            first = sync_exam_structure(structure=self.structure)
            second = sync_exam_structure(structure=self.structure)
        self.assertEqual((first.groups_created, first.entries_created), (2, 4))
        self.assertEqual((second.groups_created, second.groups_updated,
                          second.entries_created, second.entries_updated),
                         (0, 0, 0, 0))

    def test_manual_match_is_not_overwritten(self):
        with self.store():
            new, _old = self.seed_laws()
            sync_exam_structure(structure=self.structure)
            entry = self.entry("g1", "l1")
            entry.law_id = new.id
            entry.match_mode = "manual"
            entry.note = "手工指定"
            entry.save()
            second = sync_exam_structure(structure=self.structure)
            entry = LawExamEntry.get_by_id(entry.id)
        self.assertEqual(entry.match_mode, "manual")
        self.assertEqual(entry.law_id, new.id)
        self.assertEqual(entry.note, "手工指定")
        self.assertEqual(second.manual, 1)
        self.assertEqual(second.matched, 3)

    def test_unmatched_after_data_added(self):
        with self.store():
            self.seed_laws()
            first = sync_exam_structure(structure=self.structure)
            self.assertEqual(first.unmatched, 1)
            category = Category.get(folder="法律")
            Law.create(level="法律", name="民法典", publish="2020-05-28",
                       category_id=category.id)
            second = sync_exam_structure(structure=self.structure)
            self.assertEqual(second.unmatched, 0)

    def test_overview_and_summaries(self):
        with self.store():
            self.seed_laws()
            sync_exam_structure(structure=self.structure)
            overview = exam_overview()
            summaries = group_summaries()
        self.assertEqual(overview.group_count, 2)
        self.assertEqual(overview.entry_count, 4)
        self.assertEqual(overview.matched_count, 3)
        self.assertEqual(overview.unmatched_count, 1)
        self.assertEqual([s["code"] for s in summaries], ["g1", "g2"])
        self.assertEqual(summaries[0]["entry_count"], 3)
        self.assertEqual(summaries[0]["matched_count"], 3)


if __name__ == "__main__":
    unittest.main()
