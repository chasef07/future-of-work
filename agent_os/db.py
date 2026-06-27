from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Optional, Union


DEFAULT_BOUNDARY = (
    "Do bounded work. Do not send, spend, deploy, delete, publish, or make "
    "customer-facing changes without explicit approval."
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def default_db_path() -> Path:
    configured = os.environ.get("AGENT_OS_DB")
    if configured:
        return Path(configured).expanduser()
    return Path.cwd() / "agent_os.sqlite"


def connect(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    schema = resources.files("agent_os").joinpath("schema.sql").read_text()
    conn.executescript(schema)
    ensure_column(conn, "runs", "thread_id", "TEXT")
    ensure_column(conn, "runs", "thread_url", "TEXT")
    ensure_column(conn, "runs", "dispatched_at", "TEXT")
    now = utc_now()
    conn.execute(
        """
        INSERT OR IGNORE INTO workspaces (id, slug, name, created_at)
        VALUES (?, 'default', 'Default', ?)
        """,
        ("ws_default", now),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO actors (id, slug, name, actor_type, created_at)
        VALUES (?, 'human', 'Human', 'human', ?)
        """,
        ("actor_human", now),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO actors (id, slug, name, actor_type, created_at)
        VALUES (?, 'codex', 'Codex', 'agent', ?)
        """,
        ("actor_codex", now),
    )
    conn.commit()


def ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    columns = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column in columns:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def workspace_id(conn: sqlite3.Connection, slug: str) -> str:
    row = conn.execute("SELECT id FROM workspaces WHERE slug = ?", (slug,)).fetchone()
    if row:
        return str(row["id"])
    now = utc_now()
    new_id = make_id("ws")
    conn.execute(
        "INSERT INTO workspaces (id, slug, name, created_at) VALUES (?, ?, ?, ?)",
        (new_id, slug, slug.replace("-", " ").title(), now),
    )
    conn.commit()
    return new_id


def add_task_event(
    conn: sqlite3.Connection,
    task_id: str,
    event_type: str,
    from_state: Optional[str],
    to_state: Optional[str],
    note: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO task_events
          (id, task_id, event_type, from_state, to_state, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (make_id("te"), task_id, event_type, from_state, to_state, note, utc_now()),
    )


def set_task_state(
    conn: sqlite3.Connection,
    task_id: str,
    to_state: str,
    note: str,
) -> None:
    row = conn.execute("SELECT state FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise ValueError(f"unknown task: {task_id}")
    from_state = str(row["state"])
    if from_state == to_state:
        return
    conn.execute(
        "UPDATE tasks SET state = ?, updated_at = ? WHERE id = ?",
        (to_state, utc_now(), task_id),
    )
    add_task_event(conn, task_id, "task.state_changed", from_state, to_state, note)
