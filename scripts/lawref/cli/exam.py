"""法考法律汇编：把结构文件同步进数据库并按名称匹配本地法规。"""
from pathlib import Path

import click

from lawref.db import Database
from lawref.exam import DEFAULT_STRUCTURE, sync_exam_structure
from lawref.paths import MAIN_DB, ROOT


def _shown(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


@click.command("exam-sync")
@click.option("--structure", "structure_path", type=click.Path(dir_okay=False, path_type=Path),
              default=None, help=f"结构文件，默认 {_shown(DEFAULT_STRUCTURE)}")
@click.argument("databases", nargs=-1, type=click.Path(dir_okay=False, path_type=Path))
def exam_sync(structure_path: Path | None, databases: tuple[Path, ...]):
    """导入法考法律汇编的大分组与细分组，并按归一化名称匹配本体法规。

    不传数据库时只更新本体 db.sqlite3。手工指定的匹配不会被覆盖。
    """
    targets = list(databases) or [MAIN_DB]
    for path in targets:
        if len(targets) > 1:
            click.echo(f"\n=== {_shown(path)}")
        with Database(path):
            result = sync_exam_structure(structure=structure_path)
        click.echo(
            f"分组 {result.group_count}（新建 {result.groups_created}，更新 {result.groups_updated}），"
            f"条目 {result.total_entries}（新建 {result.entries_created}，更新 {result.entries_updated}）"
        )
        click.echo(
            f"匹配 {result.matched}（自动 {result.matched - result.manual} · 手工 {result.manual}），"
            f"未匹配 {result.unmatched}"
        )
