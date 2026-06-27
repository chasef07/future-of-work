---
name: agent-os
description: "Operate the Agent OS ledger, worker contract, approvals, proof, briefings, and approved knowledge."
---

# Future of Work / Agent OS

Use this skill when capturing work, routing events, dispatching workers,
recording proof, generating briefs, or proposing durable knowledge.

## Operating Model

The Agent OS has four loops:

1. Input: notice what happened.
2. Execute: do safe bounded work.
3. Approve: ask the human only for judgment or risk.
4. Learn: propose durable rules and playbooks from repeated patterns.

## Contract

- Use the Agent OS CLI/API for state. Do not treat chat, Codex threads, or
  Markdown boards as the source of truth.
- Every task has one current state.
- Every worker needs a `run_id`.
- Every run needs a boundary.
- Every done task needs proof.
- External send, spend, deploy, delete, publish, or customer-facing action
  requires policy clearance or explicit approval.
- External source tasks such as Gmail, growth-loop signals, and transcript-review
  signals default to `needs_approval`; mark ready only after a human approves or
  policy clearly allows dispatch.

## Local Commands

Run commands from the Agent OS checkout.

```bash
python3 -m agent_os.cli --db ./agent_os.sqlite ready TASK_ID
python3 -m agent_os.cli --db ./agent_os.sqlite defer TASK_ID --until 2026-06-29 --note "Start next week"
python3 -m agent_os.cli --db ./agent_os.sqlite close TASK_ID --proof "Human said no action is needed."
python3 -m agent_os.cli --db ./agent_os.sqlite remember "Subject" "Durable fact, rule, preference, or playbook." --kind rule
python3 -m agent_os.cli --db ./agent_os.sqlite dispatch --limit 1 --json
python3 -m agent_os.cli --db ./agent_os.sqlite brief
```

Use `close` when the human gives a no-action, done, or archive decision directly.
Do not create a fake run for human triage.

Use `defer` when work belongs in the queue but should not dispatch yet.

Use `remember` when the human gives durable business or personal operating context.

## Worker Handoff

Every worker prompt should include:

```text
Task ID:
Run ID:
Goal:
Context:
Boundary:
Allowed tools:
Approval rules:
Expected proof:
Stale time:
Return format:
```

Workers must return one of:

```text
done
blocked
needs_approval
needs_followup
```

They must also provide proof or the exact blocker.

If the worker has access to the Agent OS checkout, it should close the run with:

```bash
python3 -m agent_os.cli --db ./agent_os.sqlite complete-run RUN_ID --status done --proof "..."
```

Use `blocked`, `needs_approval`, or `needs_followup` instead of `done` when
proof is missing or the boundary prevents completion.

## Human Surface

Keep the human surface to:

- Brief
- Approval
- Proof

Do not make the human reconcile Gmail, Codex threads, dashboards, logs, and
task boards.
