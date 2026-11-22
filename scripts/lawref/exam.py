"""法考法律汇编：结构导入、名称匹配与统计。

结构来自仓库根目录 ``法考汇编/law_exam_structure.json``（对照觉晓法考「法律
法规汇编」整理，仅含分组与法规名称，不含正文）。``code`` 保留上游节点 id，
重复导入按 code 幂等 upsert。

匹配规则与知序客户端 ``normalizeLawName`` 一致：先去掉「中华人民共和国」，
再去掉括号注释与空白，然后比较。手工指定的匹配（``match_mode='manual'``）
不会被同步覆盖。

表在本体 ``db.sqlite3`` 里，由 ``lawref.db.store.Database.prepare`` 建好；
DLC 数据库也会带上这两张空表，便于跨库统一操作。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import peewee

from lawref.db.models import FOREVER, BaseModel, Law
from lawref.paths import ROOT

DEFAULT_STRUCTURE = ROOT / "法考汇编" / "law_exam_structure.json"
SCHEMA_VERSION = 1

# 与客户端 normalizeLawName 相同的三步归一化。
_COUNTRY_PREFIX = "中华人民共和国"
_PARENTHETICAL = re.compile(r"[（(][^）)]*[）)]")
_WHITESPACE = re.compile(r"[\s　]")


def normalize_law_name(raw: str) -> str:
    """把法规名称归一化成可比较的形式（与客户端规则一致）。"""

    value = raw.replace(_COUNTRY_PREFIX, "")
    value = _PARENTHETICAL.sub("", value)
    return _WHITESPACE.sub("", value)


class LawExamGroup(BaseModel):
    """法考汇编的一个大分组（如「刑法」）。"""

    code = peewee.TextField(unique=True)
    name = peewee.TextField()
    sort_order = peewee.IntegerField(default=0)
    enabled = peewee.BooleanField(default=True)

    class Meta:
        table_name = "law_exam_group"

    def __repr__(self) -> str:
        return f"<LawExamGroup {self.name}>"


class LawExamEntry(BaseModel):
    """大分组下的一个细分组（法规名称）。

    ``law_id`` 指向本地 ``law`` 语料；``match_mode`` 区分自动匹配、管理员
    手工指定与未匹配。
    """

    group = peewee.ForeignKeyField(LawExamGroup, backref="entries", on_delete="CASCADE")
    code = peewee.TextField()
    name = peewee.TextField()
    sort_order = peewee.IntegerField(default=0)
    law_id = peewee.UUIDField(null=True)
    match_mode = peewee.TextField(default="unmatched")
    note = peewee.TextField(null=True)
    enabled = peewee.BooleanField(default=True)

    class Meta:
        table_name = "law_exam_entry"
        indexes = ((("group", "code"), True),)

    def __repr__(self) -> str:
        return f"<LawExamEntry {self.name}>"


@dataclass(frozen=True)
class StructureLaw:
    code: str
    name: str


@dataclass(frozen=True)
class StructureGroup:
    code: str
    name: str
    order: int
    laws: tuple[StructureLaw, ...]


@dataclass
class SyncResult:
    groups_created: int = 0
    groups_updated: int = 0
    entries_created: int = 0
    entries_updated: int = 0
    matched: int = 0
    unmatched: int = 0
    manual: int = 0
    total_entries: int = 0
    group_count: int = 0


def load_structure(path: Path | None = None) -> list[StructureGroup]:
    """读取并校验结构文件。"""

    target = path or DEFAULT_STRUCTURE
    payload: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(f"不支持的法考汇编结构版本: {version!r}")

    groups: list[StructureGroup] = []
    for index, item in enumerate(payload.get("groups") or [], start=1):
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        name = str(item.get("name") or "").strip()
        if not code or not name:
            continue
        laws = tuple(
            StructureLaw(code=str(law.get("code") or "").strip(),
                         name=str(law.get("name") or "").strip())
            for law in (item.get("laws") or [])
            if isinstance(law, dict)
            and str(law.get("code") or "").strip()
            and str(law.get("name") or "").strip()
        )
        order = item.get("order")
        groups.append(StructureGroup(
            code=code, name=name,
            order=int(order) if isinstance(order, int) else index,
            laws=laws,
        ))
    return groups


def _law_rank(law: Law) -> tuple[int, int, int, str]:
    """同名多版本时的择优顺序：现行有效、版本更高、排序更靠前。"""

    return (
        0 if law.valid_to == FOREVER else 1,
        -(law.ver or 0),
        law.order if law.order is not None else 0,
        law.name,
    )


class LawMatcher:
    """把汇编中的法规名称对应到本地 ``law`` 语料。"""

    def __init__(self, laws: list[Law]) -> None:
        self._by_name: dict[str, list[Law]] = {}
        self._by_normalized: dict[str, list[Law]] = {}
        for law in laws:
            self._by_name.setdefault(law.name, []).append(law)
            self._by_normalized.setdefault(normalize_law_name(law.name), []).append(law)

    def match(self, name: str) -> Law | None:
        exact = self._by_name.get(name)
        if exact:
            return sorted(exact, key=_law_rank)[0]
        normalized = self._by_normalized.get(normalize_law_name(name))
        if normalized:
            return sorted(normalized, key=_law_rank)[0]
        return None


def sync_exam_structure(*, structure: Path | None = None,
                        laws: list[Law] | None = None) -> SyncResult:
    """把结构文件同步进当前打开的数据库，并自动匹配未手工指定的条目。

    需要在 ``with Database(...)`` 里调用（模型绑定在共用连接上）。
    """

    groups = load_structure(structure)
    corpus = list(laws) if laws is not None else list(Law.select())
    matcher = LawMatcher(corpus)

    existing_groups = {g.code: g for g in LawExamGroup.select()}
    existing_entries = {(e.group_id, e.code): e for e in LawExamEntry.select()}

    result = SyncResult(group_count=len(groups))
    for item in groups:
        group = existing_groups.get(item.code)
        if group is None:
            group = LawExamGroup.create(code=item.code, name=item.name,
                                        sort_order=item.order, enabled=True)
            existing_groups[item.code] = group
            result.groups_created += 1
        elif group.name != item.name or group.sort_order != item.order:
            group.name = item.name
            group.sort_order = item.order
            group.save()
            result.groups_updated += 1

        for order, law_item in enumerate(item.laws, start=1):
            entry = existing_entries.get((group.id, law_item.code))
            if entry is None:
                entry = LawExamEntry.create(
                    group=group, code=law_item.code, name=law_item.name,
                    sort_order=order, enabled=True)
                existing_entries[(group.id, law_item.code)] = entry
                result.entries_created += 1
            elif entry.name != law_item.name or entry.sort_order != order:
                entry.name = law_item.name
                entry.sort_order = order
                entry.save()
                result.entries_updated += 1

            result.total_entries += 1
            if entry.match_mode == "manual":
                result.manual += 1
                if entry.law_id is not None:
                    result.matched += 1
                else:
                    result.unmatched += 1
                continue
            matched = matcher.match(law_item.name)
            if matched is None:
                if entry.law_id is not None or entry.match_mode != "unmatched":
                    entry.law_id = None
                    entry.match_mode = "unmatched"
                    entry.save()
                result.unmatched += 1
            else:
                if entry.law_id != matched.id or entry.match_mode != "auto":
                    entry.law_id = matched.id
                    entry.match_mode = "auto"
                    entry.save()
                result.matched += 1
    return result


@dataclass(frozen=True)
class ExamOverview:
    group_count: int
    enabled_group_count: int
    entry_count: int
    matched_count: int
    unmatched_count: int
    manual_count: int
    auto_count: int


def exam_overview() -> ExamOverview:
    """汇总匹配情况。"""

    groups = list(LawExamGroup.select())
    entries = list(LawExamEntry.select())
    matched = sum(1 for e in entries if e.law_id is not None)
    manual = sum(1 for e in entries if e.match_mode == "manual")
    return ExamOverview(
        group_count=len(groups),
        enabled_group_count=sum(1 for g in groups if g.enabled),
        entry_count=len(entries),
        matched_count=matched,
        unmatched_count=len(entries) - matched,
        manual_count=manual,
        auto_count=matched - manual,
    )


def group_summaries() -> list[dict[str, Any]]:
    """每个分组的条目数与匹配情况。"""

    summaries: list[dict[str, Any]] = []
    for group in LawExamGroup.select().order_by(LawExamGroup.sort_order):
        rows = list(group.entries)
        matched = sum(1 for e in rows if e.law_id is not None)
        summaries.append({
            "id": group.id,
            "code": group.code,
            "name": group.name,
            "sort_order": group.sort_order,
            "enabled": group.enabled,
            "entry_count": len(rows),
            "matched_count": matched,
            "unmatched_count": len(rows) - matched,
        })
    return summaries
