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
from .kanban import write_kanban_html


GMAIL_WRAPPER = os.environ.get("AGENT_OS_GMAIL_WRAPPER", "gog")
GMAIL_ACCOUNT = os.environ.get("AGENT_OS_GMAIL_ACCOUNT", "auto")

GMAIL_BOUNDARY = (
    "Inspect and draft only. Do not send, archive, mark read, delete, "
    "unsubscribe, download attachments, or mutate Gmail."
)

USER_EMAIL_COMMAND_BOUNDARY = (
    "Explicit user-originated email command. Treat as Chase input, inspect only "
    "the needed context, create bounded worker work when useful, and do not "
    "send, spend, deploy, delete, publish, or mutate external systems unless "
    "the task boundary explicitly allows it."
)

OUTBOUND_SENT_ARTIFACT_BOUNDARY = (
    "Cold outbound sent-email artifact. This is visibility/proof from the "
    "campaign, not a new task for Chase or a worker. Track replies, bounces, "
    "suppressions, and follow-ups through the outbound loop."
)

OUTBOUND_BOUNDARY = (
    "Send-approved cold outbound lane. Work in /Users/chasefagen/Projects/outbound_ops. "
    "Run python3 scripts/outbound.py policy-check immediately before any send. "
    "Send only if send_ready is true and require_human_approval is false. Respect "
    "suppression, replies, bounces, unsubscribe, not-interested, angry/legal stops, "
    "and record Gmail messageId/threadId plus queue advancement proof."
)

LINKEDIN_BOUNDARY = (
    "Autonomous Acuity Health company-page publishing lane. One safe, "
    "website/campaign-grounded post may publish every three days through the "
    "LinkedIn automation. Comments, replies, DMs, paid promotion, customer naming, "
    "media uploads, unusual claims, or non-Acuity pages still require Chase approval."
)

ABITA_AUTONOMOUS_BOUNDARY = (
    "Autonomous Abita transcript lane. Use sanitized evidence only; do not treat "
    "reviewStatus, reviewResult, or needsReview annotations as required routing "
    "evidence or blockers. Inspect the selected calls needed to prove the owner "
    "boundary, implement the smallest deterministic repo fix or loop improvement "
    "when justified, run focused checks, and open a PR targeting main if reasonable. "
    "No raw transcripts, PHI, direct main push, merge, release, destructive git, or "
    "production mutation."
)

SOURCE_REVIEW_BOUNDARY = (
    "Review and classify only. Do not send, spend, deploy, delete, publish, mutate "
    "external apps, or make customer-facing changes until policy clearance or Chase "
    "approval makes the task ready."
)


def _conn(args: argparse.Namespace) -> sqlite3.Connection:
    conn = connect(args.db)
    init_db(conn)
    return conn


def _truncate(value: str, limit: int = 88) -> str:
    clean = " ".join(value.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."


def _clean_untrusted(value: str) -> str:
    lines = []
    for line in value.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("<<<EXTERNAL_UNTRUSTED_CONTENT"):
            continue
        if stripped.startswith("<<<END_EXTERNAL_UNTRUSTED_CONTENT"):
            continue
        if stripped == "Source: google_api":
            continue
        if stripped == "---":
            continue
        lines.append(stripped)
    return " ".join(lines).strip()


def _is_chase_email(source: str, summary: str) -> bool:
    text = summary.lower()
    return source == "gmail" and (
        text.startswith("email from chase@acuityhealth.io")
        or text.startswith("email from chasefagen@gmail.com")
    )


def _is_user_email_command(source: str, summary: str) -> bool:
    text = summary.lower()
    return _is_chase_email(source, summary) and (
        "agent os:" in text or "agent-os:" in text
    )


def _is_outbound_sent_artifact(source: str, summary: str) -> bool:
    text = summary.lower()
    return _is_chase_email(source, summary) and "question about calls at" in text


def _route_policy(source: str, summary: str) -> tuple[str, int, str]:
    """Return task state, approval flag, and boundary for a source event."""
    text = summary.lower()
    if source == "gmail":
        if _is_outbound_sent_artifact(source, summary):
            return "done", 0, OUTBOUND_SENT_ARTIFACT_BOUNDARY
        if _is_user_email_command(source, summary):
            return "ready", 0, USER_EMAIL_COMMAND_BOUNDARY
        return "needs_approval", 1, GMAIL_BOUNDARY

    if source == "growth":
        if "linkedin" in text:
            return "done", 0, LINKEDIN_BOUNDARY
        if "outbound" in text or "cold outbound" in text:
            return "ready", 0, OUTBOUND_BOUNDARY
        return "needs_approval", 1, SOURCE_REVIEW_BOUNDARY

    if source == "linkedin":
        return "done", 0, LINKEDIN_BOUNDARY

    if source == "abita-transcripts":
        return "ready", 0, ABITA_AUTONOMOUS_BOUNDARY

    if source == "manual":
        return "ready", 0, DEFAULT_BOUNDARY

    return "needs_approval", 1, SOURCE_REVIEW_BOUNDARY


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _validate_isoish(value: str) -> str:
    try:
        _parse_iso(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "expected an ISO date or datetime, such as 2026-06-29"
        ) from exc
    return value


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
        state, approval_required, boundary = _route_policy(source, summary)
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
        if state == "done":
            conn.execute(
                """
                INSERT INTO artifacts
                  (id, task_id, run_id, artifact_type, title, uri, body, created_at)
                VALUES (?, ?, NULL, 'proof', ?, NULL, ?, ?)
                """,
                (
                    make_id("art"),
                    task_id,
                    "Auto-routed proof",
                    f"Closed automatically from event {event_id}: {boundary}",
                    now,
                ),
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
        """
        SELECT t.id, t.state, e.source, e.summary
        FROM tasks t
        LEFT JOIN events e ON e.id = t.source_event_id
        WHERE t.id = ?
        """,
        (args.task_id,),
    ).fetchone()
    if not row:
        print(f"unknown task: {args.task_id}", file=sys.stderr)
        return 2
    boundary = None
    if row["source"] == "abita-transcripts":
        boundary = ABITA_AUTONOMOUS_BOUNDARY
    elif _is_user_email_command(row["source"] or "", row["summary"] or ""):
        boundary = USER_EMAIL_COMMAND_BOUNDARY
    boundary_sql = ", boundary = ?" if boundary else ""
    params = [utc_now()]
    if boundary:
        params.append(boundary)
    params.append(args.task_id)
    conn.execute(
        f"""
        UPDATE tasks
        SET approval_required = 0, updated_at = ?{boundary_sql}
        WHERE id = ?
        """,
        tuple(params),
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


def command_defer_task(args: argparse.Namespace) -> int:
    conn = _conn(args)
    row = conn.execute(
        "SELECT id, state FROM tasks WHERE id = ?",
        (args.task_id,),
    ).fetchone()
    if not row:
        print(f"unknown task: {args.task_id}", file=sys.stderr)
        return 2

    now = utc_now()
    note = args.note or f"Deferred until {args.until}"
    conn.execute(
        """
        UPDATE tasks
        SET due_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (args.until, now, args.task_id),
    )
    set_task_state(conn, args.task_id, "waiting", note)
    add_task_event(
        conn,
        args.task_id,
        "task.deferred",
        str(row["state"]),
        "waiting",
        note,
    )
    conn.commit()
    conn.close()
    print(f"deferred {args.task_id} until {args.until}")
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


def _approval_rules(task: sqlite3.Row) -> str:
    if int(task["approval_required"] or 0):
        return (
            "This task still needs approval. Do not send, spend, deploy, delete, "
            "publish, mutate external apps, or make customer-facing changes."
        )

    boundary = str(task["boundary"]).lower()
    if "cold outbound" in boundary:
        return (
            "Cold outbound sends are policy-approved only after policy-check returns "
            "send_ready true and require_human_approval false. Stop on suppression, "
            "reply, bounce, unsubscribe, not-interested, angry/legal, weak evidence, "
            "or policy failure."
        )
    if "abita" in boundary:
        return (
            "Repo investigation and PR creation are approved for Abita transcript "
            "findings. Do not block on reviewStatus, reviewResult, or needsReview "
            "annotations. Do not expose raw transcripts/PHI, push main, merge, "
            "release, or mutate production."
        )
    return (
        "This task is ready. Execute only within the boundary. Do not spend money, "
        "merge, release, delete, or take irreversible action unless explicitly allowed."
    )


def _worker_prompt(task: sqlite3.Row, run_id: str, source_summary: str = "") -> str:
    context = "Created from Agent OS task ledger."
    if source_summary:
        context = f"Created from Agent OS task ledger. Source event: {source_summary}"
    approval_rules = _approval_rules(task)
    return dedent(
        f"""
        Task ID: {task['id']}
        Run ID: {run_id}
        Goal: {task['goal']}
        Context: {context}
        Boundary: {task['boundary']}
        Allowed tools: Use local tools and relevant skills needed for this bounded task.
        Approval rules: {approval_rules}
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
        SELECT r.id, r.task_id, r.status, r.thread_id, r.started_at, r.lease_until
        FROM runs r
        WHERE r.status IN ('prepared', 'running')
          AND r.lease_until IS NOT NULL
        """
    ).fetchall()

    stale = 0
    released = 0
    for row in rows:
        lease_until = _parse_iso(str(row["lease_until"]))
        started_at = _parse_iso(str(row["started_at"]))
        prepared_without_thread = row["status"] == "prepared" and not row["thread_id"]
        orphan_prepared = prepared_without_thread and (
            started_at <= now_dt - timedelta(minutes=15) or lease_until <= now_dt
        )
        if orphan_prepared:
            now = utc_now()
            conn.execute(
                """
                UPDATE runs
                SET status = 'cancelled', completed_at = ?, result_json = ?
                WHERE id = ?
                """,
                (
                    now,
                    json.dumps(
                        {
                            "status": "cancelled",
                            "reason": "prepared run expired before worker thread attached",
                        },
                        sort_keys=True,
                    ),
                    row["id"],
                ),
            )
            set_task_state(
                conn,
                row["task_id"],
                "ready",
                f"Released orphan prepared run {row['id']} with no worker thread",
            )
            released += 1
            continue
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
    print(f"reconciled runs, marked {stale} stale, released {released} orphan prepared")
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


def _email_action(state: Optional[str]) -> str:
    if not state:
        return "not routed yet"
    return {
        "needs_approval": "needs review",
        "ready": "approved for worker",
        "running": "worker running",
        "blocked": "blocked",
        "waiting": "waiting",
        "done": "closed/no action or done",
        "archived": "archived/no action",
    }.get(state, state)


def command_email_brief(args: argparse.Namespace) -> int:
    conn = _conn(args)
    ws_id = workspace_id(conn, args.workspace)
    rows = conn.execute(
        """
        SELECT
          e.id AS event_id,
          e.summary,
          e.payload_json,
          e.occurred_at,
          t.id AS task_id,
          t.state AS task_state,
          t.updated_at AS task_updated_at
        FROM events e
        LEFT JOIN tasks t ON t.source_event_id = e.id
        WHERE e.workspace_id = ?
          AND e.source = 'gmail'
          AND (? IS NULL OR e.occurred_at >= ?)
        ORDER BY e.occurred_at DESC, e.created_at DESC
        LIMIT ?
        """,
        (ws_id, args.since, args.since, args.limit),
    ).fetchall()
    conn.close()

    lines = ["Email Visibility:"]
    if not rows:
        lines.append("- None.")
        print("\n".join(lines))
        return 0

    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError:
            payload = {}
        sender = _clean_untrusted(str(payload.get("sender") or "unknown sender"))
        subject = _clean_untrusted(str(payload.get("subject") or row["summary"]))
        snippet = _clean_untrusted(str(payload.get("snippet") or ""))
        action = _email_action(row["task_state"])
        task = f" ({row['task_id']})" if row["task_id"] else ""
        detail = f"{sender}: {subject}"
        if snippet:
            detail = f"{detail} - {_truncate(snippet, 120)}"
        lines.append(f"- {detail} -> {action}{task}")

    print("\n".join(lines))
    return 0


def command_kanban(args: argparse.Namespace) -> int:
    conn = _conn(args)
    output = write_kanban_html(
        conn,
        args.output,
        workspace=args.workspace,
        limit=args.limit,
        done_limit=args.done_limit,
    )
    conn.close()
    print(f"wrote {output}")
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

    defer = sub.add_parser("defer", help="Move a task to waiting until a date")
    defer.add_argument("task_id")
    defer.add_argument("--until", required=True, type=_validate_isoish)
    defer.add_argument("--note", default="")
    defer.set_defaults(func=command_defer_task)

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

    email_brief = sub.add_parser("email-brief", help="Show recent Gmail visibility and task outcomes")
    email_brief.add_argument("--workspace", default="default")
    email_brief.add_argument("--limit", type=int, default=20)
    email_brief.add_argument("--since")
    email_brief.set_defaults(func=command_email_brief)

    kanban = sub.add_parser("kanban", help="Render a static HTML kanban from the ledger")
    kanban.add_argument("--workspace", default="default")
    kanban.add_argument("--output", default="agent_os_kanban.html")
    kanban.add_argument("--limit", type=int, default=20)
    kanban.add_argument("--done-limit", type=int, default=8)
    kanban.set_defaults(func=command_kanban)

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
