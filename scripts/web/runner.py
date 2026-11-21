"""在后台跑 `laws` 命令，把输出实时写进任务日志。

每个任务一个线程、一个子进程；同一时间只允许一个任务在跑（见 web.app）。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading

from lawref.paths import ROOT
from web import db

_runs: dict[int, subprocess.Popen] = {}
_lock = threading.Lock()


def start(task_id: int, argv: list[str], env_extra: dict[str, str] | None = None) -> None:
    def run() -> None:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", **(env_extra or {})}
        proc = subprocess.Popen(
            [sys.executable, "-m", "lawref", *argv], cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", start_new_session=True,
        )
        with _lock:
            _runs[task_id] = proc
        conn = db.web_db()
        try:
            db.update_task(conn, task_id, status="running", started_at=db.now())
            for seq, line in enumerate(proc.stdout, start=1):
                db.add_log(conn, task_id, seq, line.rstrip("\n"))
                conn.commit()
            returncode = proc.wait()
            db.update_task(conn, task_id, status="done" if returncode == 0 else "failed",
                           returncode=returncode, finished_at=db.now())
        except Exception as e:
            db.add_log(conn, task_id, db.next_seq(conn, task_id), f"运行出错：{e!r}")
            db.update_task(conn, task_id, status="failed", finished_at=db.now())
        finally:
            with _lock:
                _runs.pop(task_id, None)
            conn.close()

    threading.Thread(target=run, daemon=True, name=f"task-{task_id}").start()


def cancel(task_id: int) -> bool:
    with _lock:
        proc = _runs.get(task_id)
    if proc is None or proc.poll() is not None:
        return False
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except ProcessLookupError:
        return False
    return True
