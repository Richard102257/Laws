"""国家规章库 Laws 仓库的 Web 管理端。

数据来自仓库里的 db.sqlite3（本体）和 DLC/<地区>地方法规/db.sqlite3，
采集任务在后台调用 `laws` 命令，日志通过 SSE 实时推给浏览器。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from lawref.chinamap import render_legend, render_map
from lawref.paths import CACHE_DIR, ROOT
from web import db, runner

STATIC_DIR = Path(__file__).resolve().parent / "static"
SESSION_COOKIE = "laws_session"


_scheduler: BackgroundScheduler | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _scheduler
    scheduler = BackgroundScheduler(daemon=True)
    with _store() as conn:
        settings = db.get_settings(conn)
    _schedule(scheduler, settings)
    scheduler.start()
    _scheduler = scheduler
    yield
    scheduler.shutdown(wait=False)
    _scheduler = None


app = FastAPI(title="Laws 管理端", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _schedule(scheduler: BackgroundScheduler, settings: dict[str, str]) -> None:
    scheduler.remove_all_jobs()
    if settings.get("auto_sync") != "true":
        return
    hour, _, minute = settings.get("auto_sync_time", "03:30").partition(":")
    scheduler.add_job(_auto_sync, CronTrigger(hour=hour or "3", minute=minute or "30"),
                      id="auto-sync", replace_existing=True, kwargs={"settings": settings})


def _auto_sync(settings: dict[str, str]) -> None:
    with _store() as conn:
        task_id = db.create_task(conn, "定时增量同步地方政府规章", "download-local",
                                 {"regions": "1-31"}, created_by="system")
    runner.start(task_id, ["download-local", "1-31", "--limit", "0", "-j", settings.get("jobs", "4")],
                 _task_env(settings))


@contextlib.contextmanager
def _store():
    conn = db.web_db()
    try:
        yield conn
    finally:
        conn.close()


def _task_env(settings: dict[str, str]) -> dict[str, str]:
    env = {}
    if settings.get("app_key"):
        env["ATHENA_APP_KEY"] = settings["app_key"]
    if settings.get("app_name"):
        env["ATHENA_APP_NAME"] = settings["app_name"]
    return env


# ---------- 登录 ----------


class LoginForm(BaseModel):
    username: str
    password: str


def current_user(request: Request) -> dict | None:
    with _store() as conn:
        return db.user_of_token(conn, request.cookies.get(SESSION_COOKIE))


def require(*roles: str):
    def dependency(request: Request) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="未登录")
        if roles and user["role"] not in roles and user["role"] != "admin":
            raise HTTPException(status_code=403, detail="没有权限")
        return user

    return dependency


@app.post("/api/login")
def login(form: LoginForm, response: JSONResponse):
    with _store() as conn:
        db.seed_admin(conn)
        row = conn.execute("SELECT * FROM users WHERE username = ?", (form.username,)).fetchone()
        if row is None or not db.verify_password(form.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="用户名或密码不对")
        token = db.create_session(conn, row["id"])
    response = JSONResponse({"username": row["username"], "role": row["role"]})
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=7 * 86400)
    return response


@app.post("/api/logout")
def logout(request: Request, response: JSONResponse):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with _store() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/api/me")
def me(user: dict = Depends(require())):
    return user


# ---------- 概览与查询 ----------


@app.get("/api/stats")
def stats(user: dict = Depends(require())):
    return db.stats()


@app.get("/api/filters")
def filters(user: dict = Depends(require())):
    levels, groups = db.levels_and_groups()
    return {"levels": levels, "groups": groups}


@app.get("/api/catalog")
def catalog(user: dict = Depends(require())):
    return {"items": db.catalog()}


@app.get("/api/regions/map")
def regions_map(user: dict = Depends(require())):
    """真实中国地图 SVG：按法规数量着色，省份地块带 data-pack 供前端点击筛选。"""
    items = db.catalog()
    counts = {item["province"]: item["total"] for item in items if not item["empty"]}
    actions = {
        item["province"]: {"data-action": "pick-pack-quick", "data-pack": item["name"]}
        for item in items if not item["empty"]
    }
    return {"map": render_map(counts, actions=actions), "legend": render_legend(counts)}


@app.get("/api/exam/overview")
def exam_overview(user: dict = Depends(require())):
    return db.exam_overview()


@app.get("/api/exam/tree")
def exam_tree(group_id: int | None = None, match_mode: str = "", q: str = "",
              user: dict = Depends(require())):
    return {"items": db.exam_tree(group_id, match_mode, q)}


@app.get("/api/laws")
def laws(keyword: str = "", level: str = "", group: str = "", date_from: str = "", date_to: str = "",
         pack: str = "", category: str = "", sort: str = "publish_desc",
         page: int = 1, size: int = 20, user: dict = Depends(require())):
    rows = db.query_laws(keyword, level, group, date_from, date_to, pack, category)
    if sort == "name_asc":
        rows.sort(key=lambda r: (r.get("name") or "", r.get("publish") or ""))
    elif sort == "name_desc":
        rows.sort(key=lambda r: (r.get("name") or "", r.get("publish") or ""), reverse=True)
    elif sort == "publish_asc":
        rows.sort(key=lambda r: (r.get("publish") or "", r.get("name") or ""))
    else:
        rows.sort(key=lambda r: (r.get("publish") or "", r.get("name") or ""), reverse=True)
    total = len(rows)
    start = max((page - 1) * size, 0)
    items = rows[start:start + size]
    roots = db.pack_roots()
    for row in items:
        root = roots.get(row["pack"])
        row["md_path"] = db.resolve_md(row, root) if root is not None else None
    return {"total": total, "items": items}


@app.get("/api/laws/{pack}/{law_id}")
def law_detail(pack: str, law_id: str, user: dict = Depends(require())):
    for pack_root, db_path in db.packs():
        if db.pack_name(pack_root) != pack:
            continue
        conn = db.read_db(db_path)
        if conn is None:
            break
        try:
            row = conn.execute(
                'SELECT l.*, c.folder, c."group", c.name AS category_name FROM law l'
                ' JOIN category c ON c.id = l.category_id WHERE l.id = ?', (law_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="找不到法规")
            item = dict(row)
            relative = db.resolve_md(item, pack_root)
            item["md_path"] = relative
            if relative:
                item["content"] = (ROOT / relative).read_text(encoding="utf-8")
            item["relations"] = db.law_relations(pack, law_id)
            return item
        finally:
            conn.close()
    raise HTTPException(status_code=404, detail="找不到法规")


@app.get("/api/laws/{pack}/{law_id}/download")
def law_download(pack: str, law_id: str, user: dict = Depends(require())):
    for pack_root, db_path in db.packs():
        if db.pack_name(pack_root) != pack:
            continue
        conn = db.read_db(db_path)
        if conn is None:
            break
        try:
            row = conn.execute(
                'SELECT l.*, c.folder FROM law l JOIN category c ON c.id = l.category_id WHERE l.id = ?',
                (law_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="找不到法规")
            relative = db.resolve_md(dict(row), pack_root)
            if not relative:
                raise HTTPException(status_code=404, detail="找不到文件")
            path = ROOT / relative
            filename = quote(path.name)
            return FileResponse(path, filename=filename, media_type="text/markdown; charset=utf-8",
                                headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})
        finally:
            conn.close()
    raise HTTPException(status_code=404, detail="找不到法规")


# ---------- 任务 ----------


class TaskForm(BaseModel):
    kind: str
    name: str = ""
    url: str = ""
    index: str = ""
    regions: str = ""
    department: str = ""
    keyword: str = ""
    limit: int = 0
    delay: float = 1.0
    provider: str = "flk"
    folder: str = ""
    title: str = ""
    publish: str = ""
    file_path: str = ""
    names: str = ""
    see_also: bool = False
    apply: bool = True


TASK_NAMES = {
    "download": "按名称下载（flk）",
    "download-all": "全量下载（法律/行政法规/司法解释）",
    "download-list": "名单批量下载",
    "download-url": "单页下载（维基文库）",
    "download-dlc": "地方性法规采集",
    "download-local": "地方政府规章采集",
    "download-gz": "部门规章采集",
    "download-guide": "最高法指导性案例",
    "download-jc": "最高检指导性案例",
    "download-case": "司法行政指导案例",
    "download-versions": "维基文库全部版本",
    "download-wikisource": "维基文库索引页",
    "parse-file": "解析本地文件",
    "organize": "整理入仓",
    "db-update": "更新数据库",
    "exam-sync": "法考汇编结构同步",
}

# 每种类型需要的表单字段（前端据此显示/隐藏）。
TASK_FIELDS = {
    "download": ["name"],
    "download-all": ["limit"],
    "download-list": ["names", "provider", "delay", "limit"],
    "download-url": ["url"],
    "download-dlc": ["regions", "limit"],
    "download-local": ["regions", "limit"],
    "download-gz": ["department", "limit"],
    "download-guide": ["limit"],
    "download-jc": ["limit"],
    "download-case": ["limit"],
    "download-versions": ["name"],
    "download-wikisource": ["index", "folder", "see_also", "limit"],
    "parse-file": ["file_path", "folder", "title", "publish"],
    "organize": ["apply"],
    "db-update": [],
    "exam-sync": [],
}


def _regions(raw: str) -> list[str]:
    parts = [r for r in re.split(r"[\s,，、]+", raw or "") if r]
    if not parts:
        raise HTTPException(status_code=400, detail="要指定地区，如「湖北」或「1-31」")
    return parts


def _names_file(names: str) -> str:
    text = (names or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="名单不能为空，每行一个名称")
    folder = CACHE_DIR / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"list-{int(time.time() * 1000)}.txt"
    path.write_text(text + "\n", encoding="utf-8")
    return str(path)


def build_argv(form: TaskForm, jobs: str) -> list[str]:
    limit = ["--limit", str(form.limit or 0)]
    common = ["-j", jobs]
    kind = form.kind

    if kind == "download":
        if not form.name.strip():
            raise HTTPException(status_code=400, detail="要填写法规名称")
        return ["download", form.name.strip(), *common]
    if kind == "download-all":
        return ["download-all", *limit, *common]
    if kind == "download-list":
        argv = ["download-list", _names_file(form.names),
                "--provider", form.provider or "flk", "--delay", str(form.delay or 1.0)]
        return [*argv, *limit, *common]
    if kind == "download-url":
        if not form.url.strip():
            raise HTTPException(status_code=400, detail="要填写页面链接")
        return ["download-url", form.url.strip(), *common]
    if kind in ("download-dlc", "download-local"):
        return [kind, *_regions(form.regions), *limit, *common]
    if kind == "download-gz":
        return ["download-gz", form.department.strip(), *limit, *common]
    if kind in ("download-guide", "download-jc", "download-case"):
        return [kind, *limit, *common]
    if kind == "download-versions":
        if not form.name.strip():
            raise HTTPException(status_code=400, detail="要填写法规名称")
        return ["download-versions", form.name.strip(), *common]
    if kind == "download-wikisource":
        if not form.index.strip():
            raise HTTPException(status_code=400, detail="要填写索引页名称")
        argv = ["download-wikisource", form.index.strip()]
        if form.see_also:
            argv.append("--see-also")
        argv += ["--type", form.folder.strip() or "行政法规"]
        return [*argv, *limit, *common]
    if kind == "parse-file":
        if not form.file_path.strip():
            raise HTTPException(status_code=400, detail="要填写服务器上的文件路径")
        argv = ["parse-file", form.file_path.strip(), "--type", form.folder.strip() or "法律"]
        if form.title.strip():
            argv += ["--title", form.title.strip()]
        if form.publish.strip():
            argv += ["--date", form.publish.strip()]
        return argv
    if kind == "organize":
        return ["organize", *(["--apply"] if form.apply else [])]
    if kind == "db-update":
        return ["db", "update"]
    if kind == "exam-sync":
        return ["exam-sync"]
    raise HTTPException(status_code=400, detail=f"不支持的任务：{kind}")


@app.get("/api/task-defs")
def task_defs(user: dict = Depends(require())):
    """采集任务类型与每种类型的表单字段，前端据此渲染表单。"""
    return {"names": TASK_NAMES, "fields": TASK_FIELDS}


@app.post("/api/tasks", status_code=201)
def create_task(form: TaskForm, request: Request, user: dict = Depends(require("editor"))):
    with _store() as conn:
        running = conn.execute("SELECT 1 FROM tasks WHERE status IN ('pending','running') LIMIT 1").fetchone()
        if running:
            raise HTTPException(status_code=409, detail="已有任务在跑，同一时间只运行一个")
        settings = db.get_settings(conn)
        argv = build_argv(form, settings.get("jobs", "4"))
        params: dict[str, Any] = form.model_dump()
        params["argv"] = argv
        task_id = db.create_task(conn, TASK_NAMES.get(form.kind, form.kind), form.kind, params,
                                 created_by=user["username"])
    runner.start(task_id, argv, _task_env(settings))
    return {"id": task_id}


@app.get("/api/tasks")
def tasks(user: dict = Depends(require())):
    with _store() as conn:
        return {"items": db.list_tasks(conn)}


@app.get("/api/tasks/{task_id}")
def task_detail(task_id: int, user: dict = Depends(require())):
    with _store() as conn:
        task = db.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="找不到任务")
    return task


@app.get("/api/tasks/{task_id}/logs")
def task_logs(task_id: int, last: int = 0, user: dict = Depends(require())):
    with _store() as conn:
        return {"items": db.logs_after(conn, task_id, last)}


@app.get("/api/tasks/{task_id}/stream")
async def task_stream(task_id: int, request: Request, user: dict = Depends(require())):
    async def events():
        last = 0
        while True:
            with _store() as conn:
                rows = db.logs_after(conn, task_id, last)
                task = db.get_task(conn, task_id)
            for row in rows:
                last = row["seq"]
                yield f"id: {last}\ndata: {json.dumps({'message': row['message']}, ensure_ascii=False)}\n\n"
            if task and task["status"] not in ("pending", "running") and not rows:
                yield f"event: end\ndata: {json.dumps({'status': task['status']})}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: int, user: dict = Depends(require("editor"))):
    with _store() as conn:
        task = db.get_task(conn, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="找不到任务")
        if task["status"] not in ("pending", "running"):
            return {"ok": False, "detail": "任务已结束"}
    ok = runner.cancel(task_id)
    if ok:
        with _store() as conn:
            db.update_task(conn, task_id, status="canceled", finished_at=db.now())
    return {"ok": ok}


# ---------- 设置 ----------


@app.get("/api/settings")
def get_settings_route(user: dict = Depends(require("admin"))):
    with _store() as conn:
        return db.get_settings(conn)


class SettingForm(BaseModel):
    key: str
    value: str


@app.put("/api/settings")
def put_setting(form: SettingForm, user: dict = Depends(require("admin"))):
    with _store() as conn:
        db.set_setting(conn, form.key, form.value)
        settings = db.get_settings(conn)
    _reschedule(settings)
    return settings


def _reschedule(settings: dict[str, str]) -> None:
    if _scheduler is not None:
        _schedule(_scheduler, settings)


# ---------- 用户 ----------


class UserForm(BaseModel):
    username: str
    password: str = ""
    role: str = "viewer"


@app.get("/api/users")
def list_users(user: dict = Depends(require("admin"))):
    with _store() as conn:
        return {"items": [dict(r) for r in conn.execute("SELECT id, username, role, created_at FROM users")]}


@app.post("/api/users", status_code=201)
def create_user(form: UserForm, user: dict = Depends(require("admin"))):
    if form.role not in ("admin", "editor", "viewer"):
        raise HTTPException(status_code=400, detail="角色只能是 admin、editor、viewer")
    with _store() as conn:
        try:
            conn.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                         (form.username, db.hash_password(form.password), form.role, db.now()))
            conn.commit()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"创建失败：{e}") from e
    return {"ok": True}


@app.put("/api/users/{user_id}")
def update_user(user_id: int, form: UserForm, user: dict = Depends(require("admin"))):
    if form.role not in ("admin", "editor", "viewer"):
        raise HTTPException(status_code=400, detail="角色只能是 admin、editor、viewer")
    with _store() as conn:
        if form.password:
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (db.hash_password(form.password), user_id))
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (form.role, user_id))
        conn.commit()
    return {"ok": True}


@app.delete("/api/users/{user_id}")
def delete_user(user_id: int, user: dict = Depends(require("admin"))):
    with _store() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


def main() -> None:
    uvicorn.run("web.app:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
