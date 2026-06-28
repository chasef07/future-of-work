# Future of Work

Future of Work is a small agent operating kernel for personal and business work.

It is not a dashboard, task app, or chat transcript. It gives agents a durable
place to record what happened, what needs doing, what is running, what needs
approval, and what proof exists.

The public repo is the reusable kernel. Your live database, account names,
thread IDs, raw emails, customer context, and personal/business knowledge should
stay in a private overlay.

## Four Loop Primitives

The system is built from four loops:

1. Input: notice what happened.
2. Execute: do safe bounded work.
3. Approve: ask the human only for judgment or risk.
4. Learn: turn repeated steering into durable rules and playbooks.

Everything else is implementation detail.

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
python -m agent_os.cli defer TASK_ID --until 2026-06-29 --note "Start next week"
python -m agent_os.cli close TASK_ID --proof "Human said no action is needed."
python -m agent_os.cli remember "Vendor digests" "Monitor only unless there is production impact." --kind rule
python -m agent_os.cli dispatch --dry-run
python -m agent_os.cli dispatch --json
python -m agent_os.cli brief
python -m agent_os.cli email-brief --limit 20
python -m agent_os.cli kanban --output agent_os_kanban.html
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
- Email visibility: what came in, including noise that was closed.
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

External sources route by policy:

- Gmail defaults to `needs_approval`; workers may inspect/draft only until
  Chase approves a response.
- Gmail from Chase's own accounts is not automatically user input. Self-sent
  cold outbound campaign emails such as `question about calls at ...` close as
  campaign artifacts; explicit `Agent OS:` self-emails may route to `ready`.
- Cold outbound may route to `ready` when the outbound policy gate owns sends.
  The worker must still run policy-check immediately before sending, respect
  suppression/reply/bounce stops, record provider receipts, and write proof.
- LinkedIn stays approval-gated; approval packets are safe, publishing is not.
- Abita transcript findings route to `ready` repo workers. They should use
  sanitized evidence, ignore `reviewStatus`, `reviewResult`, and `needsReview`
  as routing blockers, make the smallest deterministic repo fix or loop
  improvement, run checks, and open a PR targeting `main` when reasonable.

Mark a task ready manually only after a human approves or policy clearly allows
the work:

```bash
python -m agent_os.cli ready TASK_ID --note "Approved by human"
```

Human triage can close a task without creating a worker run:

```bash
python -m agent_os.cli close TASK_ID --proof "Human said no action is needed."
```

Future work can be deferred without dispatching a worker immediately:

```bash
python -m agent_os.cli defer TASK_ID --until 2026-06-29 --note "Start next week"
```

Record durable facts, rules, preferences, and playbooks with:

```bash
python -m agent_os.cli remember "Subject" "What should be remembered." --kind rule
```

Show recent Gmail arrivals and what happened to each one:

```bash
python -m agent_os.cli email-brief --limit 20
```

Render a local black-and-white kanban snapshot of the ledger:

```bash
python -m agent_os.cli kanban --output agent_os_kanban.html
```

## Automations

Codex automation templates live in `automations/templates`.

Commit templates, not live automation state. Live automation files usually
contain local paths, thread IDs, account names, and private workflow details.
