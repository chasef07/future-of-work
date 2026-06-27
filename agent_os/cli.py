from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from textwrap import dedent
from typing import Optional

from .db import (
    DEFAULT_BOUNDARY,
    add_task_event,
    connect,
    init_db,
    make_id,
    set_task_state,
    utc_now,
    workspace_id,
)


GMAIL_WRAPPER = os.environ.get("AGENT_OS_GMAIL_WRAPPER", "gog")
GMAIL_ACCOUNT = os.environ.get("AGENT_OS_GMAIL_ACCOUNT", "auto")


def _conn(args: argparse.Namespace) -> sqlite3.Connection:
    conn = connect(args.db)
    init_db(conn)
    return conn


def _truncate(value: str, limit: int = 88) -> str:
    clean = " ".join(value.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def command_init(args: argparse.Namespace) -> int:
    conn = _conn(args)
    conn.close()
    print(f"initialized {Path(args.db).expanduser() if args.db else 'agent_os.sqlite'}")
    return 0


def command_capture(args: argparse.Namespace) -> int:
    conn = _conn(args)
    ws_id = workspace_id(conn, args.workspace)
    event_id = make_id("evt")
    now = utc_now()
    summary = " ".join(args.text).strip()
    if not summary:
        print("capture requires text", file=sys.stderr)
        return 2

    payload = {"input": summary}
    external_id = args.external_id
    conn.execute(
        """
        INSERT INTO events
          (id, workspace_id, source, external_id, event_type, summary,
           payload_json, occurred_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            ws_id,
            args.source,
            external_id,
            f"{args.source}.capture",
            summary,
            json.dumps(payload, sort_keys=True),
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    print(f"captured {event_id}")
    return 0


def _load_json(path: Optional[str]) -> object:
    if path == "-":
        return json.loads(sys.stdin.read())
    if path:
        return json.loads(Path(path).read_text())
    raise ValueError("json path required")


def _first_text(item: dict, keys: list[str], default: str = "") -> str:
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return default


def _items_from_json(value: object) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("threads", "messages", "results", "items", "data"):
            child = value.get(key)
            if isinstance(child, list):
                return [item for item in child if isinstance(item, dict)]
        return [value]
    return []


def _gmail_live_search(args: argparse.Namespace) -> object:
    query = " ".join(args.query).strip()
    if not query:
        query = "newer_than:1d"
    command = [
        args.wrapper,
        "--account",
        args.account,
        "--gmail-no-send",
        "--json",
        "--results-only",
        "--wrap-untrusted",
        "gmail",
        "search",
        "--max",
        str(args.max),
        query,
    ]
    result = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"gmail search failed: {result.returncode}")
    if not result.stdout.strip():
        return []
    return json.loads(result.stdout)


def command_ingest_gmail(args: argparse.Namespace) -> int:
    conn = _conn(args)
    ws_id = workspace_id(conn, args.workspace)
    if args.json_file:
        raw = _load_json(args.json_file)
    else:
        raw = _gmail_live_search(args)

    now = utc_now()
    items = _items_from_json(raw)
    inserted = 0
    skipped = 0
    for item in items:
        thread_id = _first_text(item, ["threadId", "thread_id", "threadID", "id"])
        message_id = _first_text(item, ["messageId", "message_id", "messageID"])
        subject = _first_text(item, ["subject", "Subject"], "(no subject)")
        sender = _first_text(item, ["from", "sender", "From"], "(unknown sender)")
        snippet = _first_text(item, ["snippet", "summary", "body_excerpt", "excerpt"])
        last_seen = _first_text(
            item,
            ["date", "internalDate", "last_seen_at", "lastMessageDate", "received_at"],
            now,
        )
        key_part = message_id or thread_id or f"{subject}:{sender}:{last_seen}"
        external_id = f"{args.account}:{key_part}"
        summary_bits = [f"Email from {sender}", f"subject: {subject}"]
        if snippet:
            summary_bits.append(f"snippet: {_truncate(snippet, 180)}")
        summary = "; ".join(summary_bits)
        payload = {
            "account": args.account,
            "thread_id": thread_id,
            "message_id": message_id,
            "sender": sender,
            "subject": subject,
            "snippet": snippet,
            "last_seen_at": last_seen,
        }
        event_id = make_id("evt")
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO events
              (id, workspace_id, source, external_id, event_type, summary,
               payload_json, occurred_at, created_at)
            VALUES (?, ?, 'gmail', ?, 'gmail.thread_seen', ?, ?, ?, ?)
            """,
            (
                event_id,
                ws_id,
                external_id,
                summary,
                json.dumps(payload, sort_keys=True),
                last_seen,
                now,
            ),
        )
        if cursor.rowcount:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    conn.close()
    print(f"ingested {inserted} Gmail events, skipped {skipped} duplicates")
    return 0


def command_route(args: argparse.Namespace) -> int:
    conn = _conn(args)
    rows = conn.execute(
        """
        SELECT * FROM events
        WHERE processed_at IS NULL
        ORDER BY occurred_at ASC
        LIMIT ?
        """,
        (args.limit,),
    ).fetchall()

    created = 0
    for row in rows:
        event_id = str(row["id"])
        summary = str(row["summary"])
        source = str(row["source"])
        boundary = DEFAULT_BOUNDARY
        state = "ready"
        approval_required = 0
        if source == "gmail":
            boundary = (
                "Inspect and draft only. Do not send, archive, mark read, delete, "
                "unsubscribe, download attachments, or mutate Gmail."
            )
            state = "needs_approval"
            approval_required = 1
        elif source != "manual":
            state = "needs_approval"
            approval_required = 1
        task_id = make_id("task")
        now = utc_now()
        conn.execute(
            """
            INSERT INTO tasks
              (id, workspace_id, source_event_id, title, goal, state, priority,
               owner, boundary, approval_required, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'agent', ?, ?, ?, ?)
            """,
            (
                task_id,
                row["workspace_id"],
                event_id,
                _truncate(summary),
                summary,
                state,
                args.priority,
                boundary,
                approval_required,
                now,
                now,
            ),
        )
        add_task_event(
            conn,
            task_id,
            "task.created",
            None,
            state,
            f"Created from event {event_id}",
        )
        conn.execute(
            "UPDATE events SET processed_at = ? WHERE id = ?",
            (now, event_id),
        )
        created += 1

    conn.commit()
    conn.close()
    print(f"routed {len(rows)} events, created {created} tasks")
    return 0


def command_mark_ready(args: argparse.Namespace) -> int:
    conn = _conn(args)
    row = conn.execute(
        "SELECT id, state FROM tasks WHERE id = ?",
        (args.task_id,),
    ).fetchone()
    if not row:
        print(f"unknown task: {args.task_id}", file=sys.stderr)
        return 2
    conn.execute(
        """
        UPDATE tasks
        SET approval_required = 0, updated_at = ?
        WHERE id = ?
        """,
        (utc_now(), args.task_id),
    )
    set_task_state(conn, args.task_id, "ready", args.note)
    conn.commit()
    conn.close()
    print(f"marked {args.task_id} ready")
    return 0


def command_close_task(args: argparse.Namespace) -> int:
    conn = _conn(args)
    row = conn.execute(
        "SELECT id, state FROM tasks WHERE id = ?",
        (args.task_id,),
    ).fetchone()
    if not row:
        print(f"unknown task: {args.task_id}", file=sys.stderr)
        return 2

    proof = args.proof.strip()
    if not proof:
        print("close requires --proof", file=sys.stderr)
        return 2

    now = utc_now()
    conn.execute(
        """
        UPDATE tasks
        SET approval_required = 0, updated_at = ?
        WHERE id = ?
        """,
        (now, args.task_id),
    )
    set_task_state(conn, args.task_id, args.state, args.note or proof)
    conn.execute(
        """
        INSERT INTO artifacts
          (id, task_id, run_id, artifact_type, title, uri, body, created_at)
        VALUES (?, ?, NULL, 'proof', ?, ?, ?, ?)
        """,
        (
            make_id("art"),
            args.task_id,
            args.title,
            args.uri,
            proof,
            now,
        ),
    )
    conn.commit()
    conn.close()
    print(f"closed {args.task_id} as {args.state}")
    return 0


def command_tasks(args: argparse.Namespace) -> int:
    conn = _conn(args)
    params: list[str] = []
    where = ""
    if args.state:
        where = "WHERE state = ?"
        params.append(args.state)
    rows = conn.execute(
        f"""
        SELECT id, state, priority, title, updated_at
        FROM tasks
        {where}
        ORDER BY
          CASE state
            WHEN 'needs_approval' THEN 1
            WHEN 'blocked' THEN 2
            WHEN 'running' THEN 3
            WHEN 'ready' THEN 4
            WHEN 'waiting' THEN 5
            WHEN 'done' THEN 6
            ELSE 7
          END,
          updated_at DESC
        LIMIT ?
        """,
        (*params, args.limit),
    ).fetchall()
    conn.close()

    if not rows:
        print("No tasks.")
        return 0

    for row in rows:
        print(f"{row['id']}  {row['state']:<14} {row['priority']:<8} {row['title']}")
    return 0


def _worker_prompt(task: sqlite3.Row, run_id: str, source_summary: str = "") -> str:
    context = "Created from Agent OS task ledger."
    if source_summary:
        context = f"Created from Agent OS task ledger. Source event: {source_summary}"
    return dedent(
        f"""
        Task ID: {task['id']}
        Run ID: {run_id}
        Goal: {task['goal']}
        Context: {context}
        Boundary: {task['boundary']}
        Allowed tools: Use local tools and relevant skills needed for this bounded task.
        Approval rules: Do not send, spend, deploy, delete, publish, or make customer-facing changes without explicit approval.
        Expected proof: exact files/links/commands/output/draft/screenshot/conclusion that proves the result.
        Stale time: 4 hours unless a different deadline is provided.
        Return format:
        - status: done | blocked | needs_approval | needs_followup
        - output:
        - proof:
        - files_or_links:
        - blocker:
        - decision_needed:
        - recommended_next_step:
        """
    ).strip()


def command_dispatch(args: argparse.Namespace) -> int:
    conn = _conn(args)
    rows = conn.execute(
        """
        SELECT t.*, e.summary AS source_summary
        FROM tasks t
        LEFT JOIN events e ON e.id = t.source_event_id
        WHERE t.state = 'ready'
          AND t.approval_required = 0
          AND NOT EXISTS (
            SELECT 1 FROM runs r
            WHERE r.task_id = t.id
              AND r.status IN ('prepared', 'running')
          )
        ORDER BY t.updated_at ASC
        LIMIT ?
        """,
        (args.limit,),
    ).fetchall()

    if not rows:
        if args.json:
            print("[]")
        else:
            print("No dispatchable tasks.")
        conn.close()
        return 0

    prepared_runs = []
    for task in rows:
        run_id = make_id("run")
        prompt = _worker_prompt(task, run_id, str(task["source_summary"] or ""))
        if args.dry_run:
            print(f"\n--- dispatch candidate {task['id']} / {run_id} ---\n{prompt}")
            continue

        now = utc_now()
        lease_until = (
            datetime.now(timezone.utc) + timedelta(hours=args.lease_hours)
        ).replace(microsecond=0).isoformat()
        conn.execute(
            """
            INSERT INTO runs
              (id, task_id, status, actor, boundary, expected_proof, prompt,
               started_at, lease_until)
            VALUES (?, ?, 'prepared', 'codex', ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                task["id"],
                task["boundary"],
                "Proof matching the worker contract.",
                prompt,
                now,
                lease_until,
            ),
        )
        set_task_state(conn, task["id"], "running", f"Prepared run {run_id}")
        prepared = {
            "run_id": run_id,
            "task_id": task["id"],
            "title": task["title"],
            "prompt": prompt,
        }
        prepared_runs.append(prepared)
        if not args.json:
            print(f"prepared {run_id} for {task['id']}")

    conn.commit()
    conn.close()
    if args.json:
        print(json.dumps(prepared_runs, indent=2, sort_keys=True))
    return 0


def command_attach_thread(args: argparse.Namespace) -> int:
    conn = _conn(args)
    run = conn.execute(
        "SELECT * FROM runs WHERE id = ?",
        (args.run_id,),
    ).fetchone()
    if not run:
        print(f"unknown run: {args.run_id}", file=sys.stderr)
        return 2
    now = utc_now()
    conn.execute(
        """
        UPDATE runs
        SET status = 'running', thread_id = ?, thread_url = ?, dispatched_at = ?
        WHERE id = ?
        """,
        (args.thread_id, args.thread_url, now, args.run_id),
    )
    add_task_event(
        conn,
        str(run["task_id"]),
        "run.thread_attached",
        "running",
        "running",
        f"Attached Codex thread {args.thread_id}",
    )
    conn.commit()
    conn.close()
    print(f"attached {args.thread_id} to {args.run_id}")
    return 0


def command_runs(args: argparse.Namespace) -> int:
    conn = _conn(args)
    rows = conn.execute(
        """
        SELECT r.id, r.task_id, r.status, r.thread_id, t.title
        FROM runs r
        JOIN tasks t ON t.id = r.task_id
        WHERE (? IS NULL OR r.status = ?)
        ORDER BY r.started_at DESC
        LIMIT ?
        """,
        (args.status, args.status, args.limit),
    ).fetchall()
    conn.close()
    if args.json:
        print(json.dumps([dict(row) for row in rows], indent=2, sort_keys=True))
        return 0
    if not rows:
        print("No runs.")
        return 0
    for row in rows:
        thread = row["thread_id"] or "-"
        print(f"{row['id']}  {row['status']:<14} {thread:<24} {row['title']}")
    return 0


def command_complete_run(args: argparse.Namespace) -> int:
    conn = _conn(args)
    run = conn.execute(
        "SELECT * FROM runs WHERE id = ?",
        (args.run_id,),
    ).fetchone()
    if not run:
        print(f"unknown run: {args.run_id}", file=sys.stderr)
        return 2

    task_id = str(run["task_id"])
    now = utc_now()
    result = {
        "status": args.status,
        "proof": args.proof,
        "note": args.note,
    }
    conn.execute(
        """
        UPDATE runs
        SET status = ?, result_json = ?, completed_at = ?
        WHERE id = ?
        """,
        (args.status, json.dumps(result, sort_keys=True), now, args.run_id),
    )
    if args.proof:
        conn.execute(
            """
            INSERT INTO artifacts
              (id, task_id, run_id, artifact_type, title, uri, body, created_at)
            VALUES (?, ?, ?, 'proof', ?, ?, ?, ?)
            """,
            (
                make_id("art"),
                task_id,
                args.run_id,
                "Run proof",
                args.uri,
                args.proof,
                now,
            ),
        )

    target_state = {
        "done": "done",
        "blocked": "blocked",
        "needs_approval": "needs_approval",
        "needs_followup": "waiting",
    }[args.status]
    set_task_state(conn, task_id, target_state, args.note or f"Run {args.run_id} completed")
    conn.commit()
    conn.close()
    print(f"recorded {args.status} for {args.run_id}")
    return 0


def command_reconcile(args: argparse.Namespace) -> int:
    conn = _conn(args)
    now_dt = datetime.now(timezone.utc)
    rows = conn.execute(
        """
        SELECT r.id, r.task_id, r.lease_until
        FROM runs r
        WHERE r.status IN ('prepared', 'running')
          AND r.lease_until IS NOT NULL
        """
    ).fetchall()

    stale = 0
    for row in rows:
        lease_until = _parse_iso(str(row["lease_until"]))
        if lease_until > now_dt:
            continue
        conn.execute(
            """
            UPDATE runs
            SET status = 'stale', completed_at = ?, result_json = ?
            WHERE id = ?
            """,
            (
                utc_now(),
                json.dumps({"status": "stale", "reason": "lease expired"}, sort_keys=True),
                row["id"],
            ),
        )
        set_task_state(conn, row["task_id"], "blocked", f"Run {row['id']} went stale")
        stale += 1

    conn.commit()
    conn.close()
    print(f"reconciled runs, marked {stale} stale")
    return 0


def _section(title: str, rows: list[sqlite3.Row]) -> list[str]:
    lines = [f"{title}:"]
    if not rows:
        lines.append("- None.")
        return lines
    for row in rows:
        lines.append(f"- {row['title']} ({row['id']})")
    return lines


def command_brief(args: argparse.Namespace) -> int:
    conn = _conn(args)
    ws_id = workspace_id(conn, args.workspace)
    groups = {
        "Needs Human": "needs_approval",
        "Running": "running",
        "Blocked": "blocked",
        "Waiting": "waiting",
        "Ready": "ready",
    }
    body_lines: list[str] = []
    for title, state in groups.items():
        rows = conn.execute(
            """
            SELECT id, title
            FROM tasks
            WHERE workspace_id = ? AND state = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (ws_id, state, args.limit),
        ).fetchall()
        body_lines.extend(_section(title, rows))
        body_lines.append("")

    body = "\n".join(body_lines).strip()
    conn.execute(
        "INSERT INTO briefs (id, workspace_id, body, created_at) VALUES (?, ?, ?, ?)",
        (make_id("brief"), ws_id, body, utc_now()),
    )
    conn.commit()
    conn.close()
    print(body)
    return 0


def command_remember(args: argparse.Namespace) -> int:
    conn = _conn(args)
    ws_id = workspace_id(conn, args.workspace)
    now = utc_now()
    knowledge_id = make_id("know")
    conn.execute(
        """
        INSERT INTO knowledge
          (id, workspace_id, knowledge_type, subject, body, confidence,
           status, source_event_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            knowledge_id,
            ws_id,
            args.kind,
            args.subject,
            args.body,
            args.confidence,
            args.status,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    print(f"remembered {knowledge_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-os")
    parser.add_argument("--db", help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Initialize the ledger")
    init.set_defaults(func=command_init)

    capture = sub.add_parser("capture", help="Capture a manual/voice input event")
    capture.add_argument("text", nargs="+")
    capture.add_argument("--source", default="manual")
    capture.add_argument("--workspace", default="default")
    capture.add_argument("--external-id")
    capture.set_defaults(func=command_capture)

    ingest = sub.add_parser("ingest", help="Ingest source events")
    ingest_sub = ingest.add_subparsers(dest="source", required=True)
    gmail = ingest_sub.add_parser("gmail", help="Ingest Gmail search results")
    gmail.add_argument("query", nargs="*", help="Gmail query; defaults to newer_than:1d")
    gmail.add_argument("--json-file", help="Read gog-compatible JSON from a file or '-'")
    gmail.add_argument("--account", default=GMAIL_ACCOUNT)
    gmail.add_argument("--workspace", default="default")
    gmail.add_argument("--wrapper", default=GMAIL_WRAPPER)
    gmail.add_argument("--max", type=int, default=10)
    gmail.set_defaults(func=command_ingest_gmail)

    route = sub.add_parser("route", help="Route unprocessed events into tasks")
    route.add_argument("--limit", type=int, default=25)
    route.add_argument("--priority", default="normal")
    route.set_defaults(func=command_route)

    tasks = sub.add_parser("tasks", help="List tasks")
    tasks.add_argument("--state")
    tasks.add_argument("--limit", type=int, default=50)
    tasks.set_defaults(func=command_tasks)

    ready = sub.add_parser("ready", help="Mark a task ready for dispatch")
    ready.add_argument("task_id")
    ready.add_argument("--note", default="Approved for dispatch")
    ready.set_defaults(func=command_mark_ready)

    close = sub.add_parser("close", help="Close a task with proof and no worker run")
    close.add_argument("task_id")
    close.add_argument("--state", choices=["done", "archived"], default="done")
    close.add_argument("--proof", required=True)
    close.add_argument("--note", default="")
    close.add_argument("--title", default="Task proof")
    close.add_argument("--uri")
    close.set_defaults(func=command_close_task)

    dispatch = sub.add_parser("dispatch", help="Prepare worker handoffs for ready tasks")
    dispatch.add_argument("--limit", type=int, default=5)
    dispatch.add_argument("--dry-run", action="store_true")
    dispatch.add_argument("--lease-hours", type=int, default=4)
    dispatch.add_argument("--json", action="store_true")
    dispatch.set_defaults(func=command_dispatch)

    runs = sub.add_parser("runs", help="List worker runs")
    runs.add_argument("--status")
    runs.add_argument("--limit", type=int, default=50)
    runs.add_argument("--json", action="store_true")
    runs.set_defaults(func=command_runs)

    attach = sub.add_parser("attach-thread", help="Attach a Codex thread to a run")
    attach.add_argument("run_id")
    attach.add_argument("thread_id")
    attach.add_argument("--thread-url")
    attach.set_defaults(func=command_attach_thread)

    complete = sub.add_parser("complete-run", help="Record a worker run result")
    complete.add_argument("run_id")
    complete.add_argument(
        "--status",
        choices=["done", "blocked", "needs_approval", "needs_followup"],
        required=True,
    )
    complete.add_argument("--proof", default="")
    complete.add_argument("--note", default="")
    complete.add_argument("--uri")
    complete.set_defaults(func=command_complete_run)

    reconcile = sub.add_parser("reconcile", help="Detect stale runs")
    reconcile.set_defaults(func=command_reconcile)

    brief = sub.add_parser("brief", help="Generate a brief")
    brief.add_argument("--workspace", default="default")
    brief.add_argument("--limit", type=int, default=10)
    brief.set_defaults(func=command_brief)

    remember = sub.add_parser("remember", help="Record durable Agent OS knowledge")
    remember.add_argument("subject")
    remember.add_argument("body")
    remember.add_argument(
        "--kind",
        choices=["fact", "rule", "preference", "playbook", "opportunity"],
        default="fact",
    )
    remember.add_argument("--workspace", default="default")
    remember.add_argument("--confidence", type=float, default=0.9)
    remember.add_argument("--status", choices=["proposed", "active"], default="active")
    remember.set_defaults(func=command_remember)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
