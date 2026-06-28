from __future__ import annotations

import html
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import workspace_id


STATE_COLUMNS = [
    ("needs_approval", "Needs Human"),
    ("running", "Running"),
    ("blocked", "Blocked"),
    ("ready", "Ready"),
    ("waiting", "Waiting"),
    ("done", "Done"),
]


def _h(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _compact(value: object, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _time(value: object) -> str:
    text = str(value or "")
    if not text:
        return "-"
    return text.replace("T", " ").replace("+00:00", " UTC")


def _rows(conn: sqlite3.Connection, query: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
    return list(conn.execute(query, params).fetchall())


def _latest_run(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, status, thread_id, thread_url, started_at, completed_at
        FROM runs
        WHERE task_id = ?
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (task_id,),
    ).fetchone()


def _latest_artifact(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT artifact_type, title, uri, body, created_at
        FROM artifacts
        WHERE task_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (task_id,),
    ).fetchone()


def _load_columns(
    conn: sqlite3.Connection,
    *,
    workspace: str,
    limit: int,
    done_limit: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    ws_id = workspace_id(conn, workspace)
    counts = {
        str(row["state"]): int(row["count"])
        for row in conn.execute(
            """
            SELECT state, COUNT(*) AS count
            FROM tasks
            WHERE workspace_id = ?
            GROUP BY state
            """,
            (ws_id,),
        ).fetchall()
    }

    grouped: dict[str, list[dict[str, Any]]] = {}
    for state, _label in STATE_COLUMNS:
        state_limit = done_limit if state == "done" else limit
        tasks = _rows(
            conn,
            """
            SELECT
              t.id,
              t.title,
              t.goal,
              t.state,
              t.priority,
              t.boundary,
              t.approval_required,
              t.due_at,
              t.created_at,
              t.updated_at,
              e.source,
              e.event_type,
              e.occurred_at
            FROM tasks t
            LEFT JOIN events e ON e.id = t.source_event_id
            WHERE t.workspace_id = ? AND t.state = ?
            ORDER BY t.updated_at DESC
            LIMIT ?
            """,
            (ws_id, state, state_limit),
        )
        grouped[state] = []
        for task in tasks:
            task_dict = dict(task)
            task_dict["latest_run"] = dict(_latest_run(conn, str(task["id"])) or {})
            task_dict["latest_artifact"] = dict(_latest_artifact(conn, str(task["id"])) or {})
            grouped[state].append(task_dict)
    return grouped, counts


def _card(task: dict[str, Any]) -> str:
    run = task["latest_run"]
    artifact = task["latest_artifact"]
    source = task.get("source") or "manual"
    task_id = task["id"]
    search = " ".join(
        str(task.get(key) or "")
        for key in ("id", "title", "goal", "state", "priority", "source")
    )
    run_line = ""
    if run:
        thread = run.get("thread_id") or "-"
        run_line = f"""
          <div class="meta-row">
            <dt>Run</dt>
            <dd>{_h(run.get("id"))} / {_h(run.get("status"))}</dd>
          </div>
          <div class="meta-row">
            <dt>Worker</dt>
            <dd>{_h(thread)}</dd>
          </div>
        """

    proof_line = ""
    if artifact:
        proof_line = f"""
          <div class="proof">
            <div class="proof-title">{_h(artifact.get("title") or artifact.get("artifact_type"))}</div>
            <p>{_h(_compact(artifact.get("body"), 360))}</p>
          </div>
        """

    return f"""
      <article class="card" data-search="{_h(search).lower()}">
        <div class="card-top">
          <span>{_h(task_id)}</span>
          <span>{_h(source)}</span>
        </div>
        <h3>{_h(task["title"])}</h3>
        <p class="goal">{_h(_compact(task["goal"], 280))}</p>
        <dl class="meta">
          <div class="meta-row">
            <dt>Updated</dt>
            <dd>{_h(_time(task["updated_at"]))}</dd>
          </div>
          <div class="meta-row">
            <dt>Priority</dt>
            <dd>{_h(task["priority"])}</dd>
          </div>
          {run_line}
        </dl>
        <details>
          <summary>Details</summary>
          <div class="detail-block">
            <div class="detail-label">Boundary</div>
            <p>{_h(task["boundary"])}</p>
          </div>
          {proof_line}
        </details>
      </article>
    """


def _column(state: str, label: str, tasks: list[dict[str, Any]], count: int) -> str:
    cards = "\n".join(_card(task) for task in tasks)
    empty = '<div class="empty">Nothing here.</div>' if not tasks else ""
    return f"""
      <section class="column" aria-labelledby="heading-{_h(state)}">
        <div class="column-head">
          <h2 id="heading-{_h(state)}">{_h(label)}</h2>
          <span>{count}</span>
        </div>
        <div class="cards">
          {cards}
          {empty}
        </div>
      </section>
    """


def render_kanban_html(
    conn: sqlite3.Connection,
    *,
    workspace: str = "default",
    limit: int = 20,
    done_limit: int = 8,
) -> str:
    grouped, counts = _load_columns(conn, workspace=workspace, limit=limit, done_limit=done_limit)
    total = sum(counts.values())
    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    columns = "\n".join(
        _column(state, label, grouped[state], counts.get(state, 0))
        for state, label in STATE_COLUMNS
    )
    count_items = "\n".join(
        f"<div><span>{_h(label)}</span><strong>{counts.get(state, 0)}</strong></div>"
        for state, label in STATE_COLUMNS
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent OS Ledger</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #ffffff;
      --text: #0a0a0a;
      --muted: #666666;
      --line: #d8d8d8;
      --soft: #f6f6f6;
      --softer: #fafafa;
      --shadow: rgba(0, 0, 0, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    .shell {{
      min-height: 100vh;
      padding: 28px;
    }}
    header {{
      display: grid;
      grid-template-columns: minmax(260px, 1fr) minmax(320px, 520px);
      gap: 24px;
      align-items: end;
      border-bottom: 1px solid var(--text);
      padding-bottom: 22px;
      margin-bottom: 22px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: clamp(34px, 5vw, 72px);
      line-height: 0.92;
      font-weight: 760;
      letter-spacing: 0;
    }}
    .subline {{
      margin: 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.45;
    }}
    .toolbar {{
      display: grid;
      gap: 12px;
    }}
    .counts {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      border: 1px solid var(--text);
    }}
    .counts div {{
      min-width: 0;
      padding: 10px 12px;
      border-right: 1px solid var(--line);
    }}
    .counts div:nth-child(3n) {{ border-right: 0; }}
    .counts div:nth-child(n+4) {{ border-top: 1px solid var(--line); }}
    .counts span {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.2;
      text-transform: uppercase;
    }}
    .counts strong {{
      display: block;
      margin-top: 3px;
      font-size: 22px;
      line-height: 1;
    }}
    .search {{
      width: 100%;
      min-height: 42px;
      border: 1px solid var(--text);
      border-radius: 0;
      padding: 0 12px;
      font: inherit;
      color: var(--text);
      background: var(--bg);
    }}
    .board {{
      display: grid;
      grid-template-columns: repeat(6, minmax(265px, 1fr));
      align-items: start;
      gap: 12px;
      overflow-x: auto;
      padding-bottom: 16px;
    }}
    .column {{
      min-width: 265px;
      border: 1px solid var(--text);
      background: var(--softer);
    }}
    .column-head {{
      position: sticky;
      top: 0;
      z-index: 1;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 12px;
      background: var(--bg);
      border-bottom: 1px solid var(--text);
    }}
    .column h2 {{
      margin: 0;
      font-size: 13px;
      line-height: 1;
      text-transform: uppercase;
      font-weight: 760;
    }}
    .column-head span {{
      display: inline-flex;
      min-width: 24px;
      height: 24px;
      align-items: center;
      justify-content: center;
      border: 1px solid var(--text);
      font-size: 12px;
      line-height: 1;
    }}
    .cards {{
      display: grid;
      gap: 10px;
      padding: 10px;
    }}
    .card {{
      background: var(--bg);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 12px;
      box-shadow: 0 1px 10px var(--shadow);
    }}
    .card[hidden] {{ display: none; }}
    .card-top {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.25;
      overflow-wrap: anywhere;
    }}
    .card h3 {{
      margin: 10px 0 8px;
      font-size: 15px;
      line-height: 1.25;
      font-weight: 720;
      letter-spacing: 0;
    }}
    .goal {{
      margin: 0 0 12px;
      color: #242424;
      font-size: 13px;
      line-height: 1.4;
    }}
    .meta {{
      margin: 0;
      display: grid;
      border-top: 1px solid var(--line);
    }}
    .meta-row {{
      display: grid;
      grid-template-columns: 72px 1fr;
      gap: 10px;
      padding: 7px 0;
      border-bottom: 1px solid var(--line);
      font-size: 12px;
      line-height: 1.35;
    }}
    dt {{
      color: var(--muted);
    }}
    dd {{
      margin: 0;
      overflow-wrap: anywhere;
    }}
    details {{
      margin-top: 10px;
      border-top: 1px solid var(--line);
      padding-top: 9px;
    }}
    summary {{
      cursor: pointer;
      color: var(--text);
      font-size: 12px;
      font-weight: 650;
    }}
    .detail-block, .proof {{
      margin-top: 10px;
      font-size: 12px;
      line-height: 1.4;
      color: #222222;
    }}
    .detail-label, .proof-title {{
      margin-bottom: 4px;
      color: var(--muted);
      text-transform: uppercase;
      font-size: 10px;
      font-weight: 760;
    }}
    .detail-block p, .proof p {{
      margin: 0;
    }}
    .empty {{
      min-height: 84px;
      display: grid;
      place-items: center;
      border: 1px dashed var(--line);
      color: var(--muted);
      font-size: 13px;
    }}
    footer {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }}
    @media (max-width: 900px) {{
      .shell {{ padding: 18px; }}
      header {{ grid-template-columns: 1fr; align-items: stretch; }}
      .counts {{ grid-template-columns: repeat(2, 1fr); }}
      .counts div:nth-child(3n) {{ border-right: 1px solid var(--line); }}
      .counts div:nth-child(2n) {{ border-right: 0; }}
      .counts div:nth-child(n+3) {{ border-top: 1px solid var(--line); }}
      .board {{ grid-template-columns: repeat(6, minmax(250px, 82vw)); }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div>
        <h1>Agent OS Ledger</h1>
        <p class="subline">{total} tasks. Generated {_h(generated)}. Workspace: {_h(workspace)}.</p>
      </div>
      <div class="toolbar">
        <div class="counts" aria-label="Task counts">
          {count_items}
        </div>
        <input class="search" id="search" type="search" placeholder="Filter ledger..." autocomplete="off">
      </div>
    </header>
    <div class="board" id="board">
      {columns}
    </div>
    <footer>
      Regenerate from the ledger with: <code>python3 -m agent_os.cli --db ./agent_os.sqlite kanban --output agent_os_kanban.html</code>
    </footer>
  </main>
  <script>
    const input = document.getElementById('search');
    const cards = [...document.querySelectorAll('.card')];
    input.addEventListener('input', () => {{
      const query = input.value.trim().toLowerCase();
      for (const card of cards) {{
        card.hidden = query && !card.dataset.search.includes(query);
      }}
    }});
  </script>
</body>
</html>
"""


def write_kanban_html(
    conn: sqlite3.Connection,
    output: str | Path,
    *,
    workspace: str = "default",
    limit: int = 20,
    done_limit: int = 8,
) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_kanban_html(conn, workspace=workspace, limit=limit, done_limit=done_limit),
        encoding="utf-8",
    )
    return path
