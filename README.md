# Agent OS

Agent OS is a small operating kernel for personal and business work.

It is not a dashboard, task app, or chat transcript. It gives agents a durable
place to record what happened, what needs doing, what is running, what needs
approval, and what proof exists.

The public repo is the reusable kernel. Your live database, account names,
thread IDs, raw emails, customer context, and personal/business knowledge should
stay in a private overlay.

## Mental Model

```text
Input -> Task -> Worker -> Proof -> Brief
                 |
              Approval
                 |
              Learning
```

## First Principles

- One task ledger.
- No worker without a run.
- No run without a boundary.
- No done state without proof.
- No external send, spend, deploy, delete, or customer-facing action without
  policy clearance or explicit approval.
- Humans steer; agents operate.

## Local Start

```bash
python -m agent_os.cli init
python -m agent_os.cli capture "Check whether reminder messages failed"
python -m agent_os.cli ingest gmail newer_than:1d --max 10
python -m agent_os.cli route
python -m agent_os.cli tasks
python -m agent_os.cli ready TASK_ID
python -m agent_os.cli close TASK_ID --proof "Human said no action is needed."
python -m agent_os.cli remember "Vendor digests" "Monitor only unless there is production impact." --kind rule
python -m agent_os.cli dispatch --dry-run
python -m agent_os.cli dispatch --json
python -m agent_os.cli brief
```

The default database path is `./agent_os.sqlite`. Set `AGENT_OS_DB` or pass
`--db` to use another path.

For Gmail ingestion, set:

```bash
export AGENT_OS_GMAIL_WRAPPER=/path/to/read-only-gmail-wrapper
export AGENT_OS_GMAIL_ACCOUNT=you@example.com
```

The default wrapper command is `gog`; Agent OS passes `--gmail-no-send` for the
live Gmail search path.

## Human Surfaces

- Brief: what changed, what needs judgment, what is blocked, what is done.
- Approval: one decision at a time with proposed action and risk.
- Proof: receipts for completed work.

## Agent Surfaces

Agents should use the CLI/API, not edit the database directly.

## Codex Worker Dispatch

`dispatch --json` prepares worker runs and returns JSON containing:

- `task_id`
- `run_id`
- `title`
- `prompt`

A Codex automation should create one bounded worker thread per returned run,
then attach the thread:

```bash
python -m agent_os.cli attach-thread RUN_ID THREAD_ID
```

Workers close the loop with:

```bash
python -m agent_os.cli complete-run RUN_ID --status done --proof "..."
```

Allowed statuses are `done`, `blocked`, `needs_approval`, and
`needs_followup`.

External sources such as Gmail, growth-loop signals, and transcript-review
signals default to `needs_approval`. Mark a task ready only after a human
approves or policy clearly allows the work:

```bash
python -m agent_os.cli ready TASK_ID --note "Approved by human"
```

Human triage can close a task without creating a worker run:

```bash
python -m agent_os.cli close TASK_ID --proof "Human said no action is needed."
```

Record durable facts, rules, preferences, and playbooks with:

```bash
python -m agent_os.cli remember "Subject" "What should be remembered." --kind rule
```

## Automations

Codex automation templates live in `automations/templates`.

Commit templates, not live automation state. Live automation files usually
contain local paths, thread IDs, account names, and private workflow details.
