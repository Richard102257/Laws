"""读取 Laws 仓库里的法规数据，以及 Web 管理端自己的用户、任务、设置。"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from lawref.paths import CACHE_DIR, DLC_DIR, MAIN_DB, OUT_DIR, ROOT

WEB_DB = CACHE_DIR / "web.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    returncode INTEGER,
    created_by TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS task_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    message TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "jobs": "4",
    "app_key": "",
    "app_name": "",
    "auto_sync": "false",
    "auto_sync_time": "03:30",
}


def now() -> float:
    return time.time()


# ---------- 连接 ----------


def web_db() -> sqlite3.Connection:
    WEB_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(WEB_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def read_db(path: Path) -> sqlite3.Connection:
    """只读打开一个数据包数据库，不存在时返回 None。"""
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------- 仓库数据 ----------


def packs() -> list[tuple[Path, Path]]:
    """数据包：本体在前，然后按 DLC 目录名排序。返回 (根目录, 数据库文件)。"""
    result = []
    if MAIN_DB.is_file():
        result.append((ROOT, MAIN_DB))
    if DLC_DIR.is_dir():
        for folder in sorted(d for d in DLC_DIR.iterdir() if d.is_dir()):
            db = folder / "db.sqlite3"
            if db.is_file():
                result.append((folder, db))
    return result


def law_files(law: dict, pack_root: Path) -> list[Path]:
    """和 App 的 TLaw.getNames 一致：filename、name(publish)、name、subtitle。"""
    names = [law.get("filename") or ""]
    if law.get("publish"):
        names.append(f"{law['name']}({law['publish']})")
    names += [law.get("name") or "", law.get("subtitle") or ""]
    folder = law.get("folder") or ""
    return [pack_root / folder / f"{stem}.md" for stem in dict.fromkeys(names) if stem]


def resolve_md(row: dict, pack_root: Path) -> str | None:
    for path in law_files(row, pack_root):
        if path.is_file():
            return str(path.relative_to(ROOT))
    return None


def query_laws(keyword: str = "", level: str = "", group: str = "", date_from: str = "",
               date_to: str = "") -> list[dict]:
    """跨本体和 DLC 查法规。条件为空表示不限。"""
    keyword = (keyword or "").strip()
    rows: list[dict] = []
    for pack_root, db_path in packs():
        conn = read_db(db_path)
        if conn is None:
            continue
        try:
            for law in conn.execute(
                    'SELECT l.id, l.name, l.subtitle, l.filename, l.level, l.publish, l.valid_from,'
                    ' l.valid_to, l.ver, l.tags, c.folder, c."group", c.name AS category_name'
                    ' FROM law l JOIN category c ON c.id = l.category_id'):
                row = dict(law)
                row["pack"] = pack_root.name
                row["md_path"] = resolve_md(row, pack_root)
                if keyword and not any(keyword in str(row.get(field) or "")
                                       for field in ("name", "subtitle", "tags", "category_name")):
                    continue
                if level and row["level"] != level:
                    continue
                if group and (row.get("group") or "") != group:
                    continue
                if date_from and (row.get("publish") or "") < date_from:
                    continue
                if date_to and (row.get("publish") or "") > date_to:
                    continue
                rows.append(row)
        finally:
            conn.close()
    return rows


def levels_and_groups() -> tuple[list[str], list[str]]:
    levels, groups = set(), set()
    for _, db_path in packs():
        conn = read_db(db_path)
        if conn is None:
            continue
        try:
            levels.update(r[0] for r in conn.execute("SELECT DISTINCT level FROM law"))
            groups.update(r[0] for r in conn.execute("SELECT DISTINCT \"group\" FROM category WHERE \"group\" IS NOT NULL"))
        finally:
            conn.close()
    return sorted(levels), sorted(groups)


def stats() -> dict:
    total = ok = missing = 0
    by_level: dict[str, int] = {}
    for pack_root, db_path in packs():
        conn = read_db(db_path)
        if conn is None:
            continue
        try:
            for row in conn.execute(
                    'SELECT l.name, l.subtitle, l.filename, l.level, l.publish, c.folder FROM law l'
                    ' JOIN category c ON c.id = l.category_id'):
                total += 1
                law = {key: row[key] for key in ("name", "subtitle", "filename", "level", "publish", "folder")}
                by_level[law["level"]] = by_level.get(law["level"], 0) + 1
                if resolve_md(law, pack_root):
                    ok += 1
                else:
                    missing += 1
        finally:
            conn.close()
    pending = len(list(OUT_DIR.rglob("*.md"))) if OUT_DIR.is_dir() else 0
    return {"total": total, "ok": ok, "missing": missing, "by_level": by_level,
            "packs": len(packs()), "pending_out": pending}


# ---------- 用户与会话 ----------


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex() == digest


def seed_admin(conn: sqlite3.Connection, password: str | None = None) -> None:
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return
    password = password or os.environ.get("LAWS_ADMIN_PASSWORD") or "admin123"
    conn.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, 'admin', ?)",
                 ("admin", hash_password(password), now()))
    conn.commit()


def create_session(conn: sqlite3.Connection, user_id: int, days: int = 7) -> str:
    token = secrets.token_hex(24)
    conn.execute("INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
                 (token, user_id, now() + days * 86400))
    conn.commit()
    return token


def user_of_token(conn: sqlite3.Connection, token: str | None) -> dict | None:
    if not token:
        return None
    row = conn.execute(
        "SELECT u.id, u.username, u.role, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token = ?", (token,)).fetchone()
    if row is None or row["expires_at"] < now():
        return None
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


# ---------- 设置 ----------


def get_settings(conn: sqlite3.Connection) -> dict[str, str]:
    values = dict(DEFAULT_SETTINGS)
    values.update({r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")})
    return values


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                 (key, value, value))
    conn.commit()


# ---------- 任务 ----------


def create_task(conn: sqlite3.Connection, name: str, kind: str, params: dict,
                created_by: str | None = None) -> int:
    cur = conn.execute("INSERT INTO tasks (name, kind, params, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                       (name, kind, json.dumps(params, ensure_ascii=False), created_by, now()))
    conn.commit()
    return cur.lastrowid


def get_task(conn: sqlite3.Connection, task_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def update_task(conn: sqlite3.Connection, task_id: int, **fields: Any) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{key} = ?" for key in fields)
    conn.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", (*fields.values(), task_id))
    conn.commit()


def list_tasks(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,))]


def add_log(conn: sqlite3.Connection, task_id: int, seq: int, message: str) -> None:
    conn.execute("INSERT INTO task_logs (task_id, seq, message, ts) VALUES (?, ?, ?, ?)",
                 (task_id, seq, message, now()))


def logs_after(conn: sqlite3.Connection, task_id: int, last: int, limit: int = 1000) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, seq, message FROM task_logs WHERE task_id = ? AND seq > ? ORDER BY seq LIMIT ?",
        (task_id, last, limit))]


def next_seq(conn: sqlite3.Connection, task_id: int) -> int:
    row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM task_logs WHERE task_id = ?", (task_id,)).fetchone()
    return int(row[0])
