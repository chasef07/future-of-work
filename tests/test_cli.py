from __future__ import annotations

import sqlite3
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_cli(db: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agent_os.cli", "--db", str(db), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


class CliTest(unittest.TestCase):
    def test_capture_route_brief(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agent_os.sqlite"

            run_cli(db, "init")
            captured = run_cli(db, "capture", "Check Abita reminder texts")
            self.assertIn("captured evt_", captured.stdout)

            routed = run_cli(db, "route")
            self.assertIn("created 1 tasks", routed.stdout)

            tasks = run_cli(db, "tasks")
            self.assertIn("ready", tasks.stdout)
            self.assertIn("Check Abita reminder texts", tasks.stdout)

            brief = run_cli(db, "brief")
            self.assertIn("Ready:", brief.stdout)
            self.assertIn("Check Abita reminder texts", brief.stdout)

    def test_dispatch_prepare_and_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agent_os.sqlite"

            run_cli(db, "capture", "Draft a safe vendor reply")
            run_cli(db, "route")

            dry_run = run_cli(db, "dispatch", "--dry-run")
            self.assertIn("dispatch candidate", dry_run.stdout)
            self.assertIn("Expected proof", dry_run.stdout)

            prepared = run_cli(db, "dispatch")
            self.assertIn("prepared run_", prepared.stdout)
            run_id = prepared.stdout.split()[1]

            run_cli(db, "complete-run", run_id, "--status", "done", "--proof", "Draft prepared locally.")

            conn = sqlite3.connect(db)
            row = conn.execute("SELECT state FROM tasks LIMIT 1").fetchone()
            self.assertEqual(row, ("done",))
            artifact = conn.execute("SELECT body FROM artifacts LIMIT 1").fetchone()
            self.assertEqual(artifact, ("Draft prepared locally.",))
            conn.close()

    def test_close_task_and_remember(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agent_os.sqlite"

            run_cli(db, "capture", "Triage a noisy notification")
            run_cli(db, "route")
            tasks = run_cli(db, "tasks")
            task_id = tasks.stdout.split()[0]

            closed = run_cli(
                db,
                "close",
                task_id,
                "--proof",
                "Human said no action is needed.",
            )
            self.assertIn(f"closed {task_id} as done", closed.stdout)

            remembered = run_cli(
                db,
                "remember",
                "Noisy notifications",
                "Routine vendor digests are monitor-only unless they show production impact.",
                "--kind",
                "rule",
            )
            self.assertIn("remembered know_", remembered.stdout)

            conn = sqlite3.connect(db)
            task = conn.execute("SELECT state, approval_required FROM tasks").fetchone()
            self.assertEqual(task, ("done", 0))
            artifact = conn.execute("SELECT body FROM artifacts").fetchone()
            self.assertEqual(artifact, ("Human said no action is needed.",))
            knowledge = conn.execute(
                "SELECT knowledge_type, subject, status FROM knowledge"
            ).fetchone()
            self.assertEqual(knowledge, ("rule", "Noisy notifications", "active"))
            conn.close()

    def test_defer_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agent_os.sqlite"

            run_cli(db, "capture", "Start product brainstorming next week")
            run_cli(db, "route")
            tasks = run_cli(db, "tasks")
            task_id = tasks.stdout.split()[0]

            deferred = run_cli(
                db,
                "defer",
                task_id,
                "--until",
                "2026-06-29",
                "--note",
                "Start next week",
            )
            self.assertIn(f"deferred {task_id} until 2026-06-29", deferred.stdout)

            brief = run_cli(db, "brief")
            self.assertIn("Waiting:", brief.stdout)
            self.assertIn("Start product brainstorming next week", brief.stdout)

            conn = sqlite3.connect(db)
            row = conn.execute("SELECT state, due_at FROM tasks LIMIT 1").fetchone()
            self.assertEqual(row, ("waiting", "2026-06-29"))
            event = conn.execute(
                "SELECT event_type, note FROM task_events WHERE event_type = 'task.deferred'"
            ).fetchone()
            self.assertEqual(event, ("task.deferred", "Start next week"))
            conn.close()

    def test_ingest_gmail_and_attach_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agent_os.sqlite"
            gmail_json = Path(tmp) / "gmail.json"
            gmail_json.write_text(
                json.dumps(
                    [
                        {
                            "threadId": "thread_123",
                            "messageId": "msg_123",
                            "from": "Sender <sender@example.com>",
                            "subject": "Crystal River scheduling test",
                            "snippet": "Can we temporarily remove the restriction?",
                            "date": "2026-06-27T10:00:00Z",
                        }
                    ]
                )
            )

            ingest = run_cli(db, "ingest", "gmail", "--json-file", str(gmail_json))
            self.assertIn("ingested 1 Gmail events", ingest.stdout)
            duplicate = run_cli(db, "ingest", "gmail", "--json-file", str(gmail_json))
            self.assertIn("skipped 1 duplicates", duplicate.stdout)

            run_cli(db, "route")
            no_dispatch = run_cli(db, "dispatch", "--json")
            self.assertEqual(json.loads(no_dispatch.stdout), [])

            tasks = run_cli(db, "tasks")
            self.assertIn("needs_approval", tasks.stdout)
            task_id = tasks.stdout.split()[0]

            run_cli(db, "ready", task_id)
            prepared = run_cli(db, "dispatch", "--json")
            runs = json.loads(prepared.stdout)
            self.assertEqual(len(runs), 1)
            self.assertIn("Inspect and draft only", runs[0]["prompt"])
            run_id = runs[0]["run_id"]

            attached = run_cli(db, "attach-thread", run_id, "thread_codex_123")
            self.assertIn("attached thread_codex_123", attached.stdout)

            conn = sqlite3.connect(db)
            row = conn.execute("SELECT thread_id, status FROM runs LIMIT 1").fetchone()
            self.assertEqual(row, ("thread_codex_123", "running"))
            conn.close()

    def test_reconcile_releases_orphan_prepared_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agent_os.sqlite"

            run_cli(db, "capture", "Investigate a bounded issue")
            run_cli(db, "route")
            prepared = run_cli(
                db,
                "dispatch",
                "--json",
                "--lease-hours",
                "-1",
            )
            run_id = json.loads(prepared.stdout)[0]["run_id"]

            reconciled = run_cli(db, "reconcile")
            self.assertIn("released 1 orphan prepared", reconciled.stdout)

            conn = sqlite3.connect(db)
            run = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
            task = conn.execute("SELECT state FROM tasks LIMIT 1").fetchone()
            self.assertEqual(run[0], "cancelled")
            self.assertEqual(task[0], "ready")
            conn.close()

    def test_growth_outbound_routes_ready_with_policy_send_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agent_os.sqlite"

            run_cli(
                db,
                "capture",
                "--source",
                "growth",
                "Outbound ledger shows 35 companies due and policy-check is send-ready.",
            )
            run_cli(db, "route")

            conn = sqlite3.connect(db)
            task = conn.execute(
                "SELECT state, approval_required, boundary FROM tasks"
            ).fetchone()
            self.assertEqual(task[0:2], ("ready", 0))
            self.assertIn("Send-approved cold outbound lane", task[2])
            conn.close()

            prepared = run_cli(db, "dispatch", "--json")
            runs = json.loads(prepared.stdout)
            self.assertEqual(len(runs), 1)
            self.assertIn("Cold outbound sends are policy-approved", runs[0]["prompt"])
            self.assertIn("policy-check", runs[0]["prompt"])

    def test_growth_linkedin_routes_done_under_standing_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agent_os.sqlite"

            run_cli(
                db,
                "capture",
                "--source",
                "growth",
                "LinkedIn auth is healthy with 4 drafts and 0 approved posts.",
            )
            run_cli(db, "route")

            conn = sqlite3.connect(db)
            task = conn.execute(
                "SELECT state, approval_required, boundary FROM tasks"
            ).fetchone()
            self.assertEqual(task[0:2], ("done", 0))
            self.assertIn("Autonomous Acuity Health company-page", task[2])
            conn.close()

            prepared = run_cli(db, "dispatch", "--json")
            self.assertEqual(json.loads(prepared.stdout), [])

    def test_user_email_command_routes_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agent_os.sqlite"

            run_cli(
                db,
                "capture",
                "--source",
                "gmail",
                "Email from chase@acuityhealth.io; subject: Agent OS: check tomorrow's calendar",
            )
            run_cli(db, "route")

            conn = sqlite3.connect(db)
            task = conn.execute(
                "SELECT state, approval_required, boundary FROM tasks"
            ).fetchone()
            self.assertEqual(task[0:2], ("ready", 0))
            self.assertIn("Explicit user-originated email command", task[2])
            conn.close()

    def test_outbound_sent_email_artifact_routes_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agent_os.sqlite"

            run_cli(
                db,
                "capture",
                "--source",
                "gmail",
                "Email from chase@acuityhealth.io; subject: question about calls at Southwest Eye Institute",
            )
            run_cli(db, "route")

            conn = sqlite3.connect(db)
            task = conn.execute(
                "SELECT id, state, approval_required, boundary FROM tasks"
            ).fetchone()
            self.assertEqual(task[1:3], ("done", 0))
            self.assertIn("Cold outbound sent-email artifact", task[3])
            artifact = conn.execute(
                "SELECT title, body FROM artifacts WHERE task_id = ?",
                (task[0],),
            ).fetchone()
            self.assertEqual(artifact[0], "Auto-routed proof")
            self.assertIn("Cold outbound sent-email artifact", artifact[1])
            conn.close()

            prepared = run_cli(db, "dispatch", "--json")
            self.assertEqual(json.loads(prepared.stdout), [])

    def test_abita_autonomous_routes_ready_with_pr_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agent_os.sqlite"

            run_cli(
                db,
                "capture",
                "--source",
                "abita-transcripts",
                "Autonomous: reschedule cluster shows hard failures with middleware_error.",
            )
            run_cli(db, "route")

            conn = sqlite3.connect(db)
            task = conn.execute(
                "SELECT state, approval_required, boundary FROM tasks"
            ).fetchone()
            self.assertEqual(task[0:2], ("ready", 0))
            self.assertIn("Autonomous Abita transcript lane", task[2])
            conn.close()

            prepared = run_cli(db, "dispatch", "--json")
            runs = json.loads(prepared.stdout)
            self.assertEqual(len(runs), 1)
            self.assertIn("PR creation", runs[0]["prompt"])
            self.assertIn("smallest deterministic repo fix", runs[0]["prompt"])

    def test_abita_data_gap_routes_ready_not_human(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agent_os.sqlite"

            run_cli(
                db,
                "capture",
                "--source",
                "abita-transcripts",
                "Data gap: recovered reschedule calls need loop verification; do not rely on reviewStatus/reviewResult annotations.",
            )
            run_cli(db, "route")

            conn = sqlite3.connect(db)
            task = conn.execute(
                "SELECT state, approval_required, boundary FROM tasks"
            ).fetchone()
            self.assertEqual(task[0:2], ("ready", 0))
            self.assertIn("do not treat reviewStatus", task[2])
            conn.close()

    def test_email_brief_shows_closed_noise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agent_os.sqlite"
            gmail_json = Path(tmp) / "gmail.json"
            gmail_json.write_text(
                json.dumps(
                    [
                        {
                            "threadId": "thread_noise",
                            "messageId": "msg_noise",
                            "from": "Vendor <vendor@example.com>",
                            "subject": "Product update",
                            "snippet": "Here is a routine product announcement.",
                            "date": "2026-06-28T09:00:00Z",
                        }
                    ]
                )
            )

            run_cli(db, "ingest", "gmail", "--json-file", str(gmail_json))
            run_cli(db, "route")
            task_id = run_cli(db, "tasks").stdout.split()[0]
            run_cli(
                db,
                "close",
                task_id,
                "--proof",
                "Routine vendor update. No action needed.",
            )

            email_brief = run_cli(db, "email-brief")
            self.assertIn("Email Visibility:", email_brief.stdout)
            self.assertIn("Vendor <vendor@example.com>: Product update", email_brief.stdout)
            self.assertIn("closed/no action or done", email_brief.stdout)
            self.assertIn(task_id, email_brief.stdout)

    def test_kanban_renders_static_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "agent_os.sqlite"
            output = Path(tmp) / "ledger.html"

            run_cli(db, "capture", "Ship a tiny dashboard")
            run_cli(
                db,
                "capture",
                "--source",
                "growth",
                "LinkedIn auth is healthy with 4 drafts and 0 approved posts.",
            )
            run_cli(db, "route")

            rendered = run_cli(db, "kanban", "--output", str(output))
            self.assertIn(f"wrote {output}", rendered.stdout)

            html = output.read_text()
            self.assertIn("<title>Agent OS Ledger</title>", html)
            self.assertIn("Done", html)
            self.assertIn("Ready", html)
            self.assertIn("Ship a tiny dashboard", html)
            self.assertIn("LinkedIn auth is healthy", html)
            self.assertIn("Filter ledger", html)


if __name__ == "__main__":
    unittest.main()
