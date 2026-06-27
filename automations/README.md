# Agent OS Automations

Codex automations are the heartbeat. They should call the Agent OS CLI and use
Codex thread tools only at the dispatch boundary.

## Gmail Ingest

Purpose:

- Read work Gmail through the safe read-only `gog` wrapper.
- Insert thread-level events into the ledger.
- Route events into conservative tasks.
- Generate a terse brief.

Boundaries:

- Do not send email.
- Do not archive, mark read, delete, unsubscribe, or mutate Gmail.
- Do not download attachments.
- Do not spawn workers.

Command shape:

```bash
python3 -m agent_os.cli --db ./agent_os.sqlite ingest gmail newer_than:1d --max 10
python3 -m agent_os.cli --db ./agent_os.sqlite route --limit 25
python3 -m agent_os.cli --db ./agent_os.sqlite brief
```

## Dispatcher

Purpose:

- Lease ready tasks.
- Prepare worker prompts.
- Create bounded Codex worker threads.
- Attach the created Codex thread ID back to the run.

Boundaries:

- Limit spawned workers per run.
- Do not create workers for tasks that need approval.
- Workers may inspect, draft, edit locally, and run checks inside their boundary.
- Workers may not send, spend, deploy, delete, publish, or make customer-facing
  changes without approval.

Command shape:

```bash
python3 -m agent_os.cli --db ./agent_os.sqlite dispatch --limit 1 --json
python3 -m agent_os.cli --db ./agent_os.sqlite attach-thread RUN_ID THREAD_ID
```

## Reconciler

Purpose:

- Detect stale runs.
- Move expired work to blocked.
- Keep the human from becoming the reconciler.

Command shape:

```bash
python3 -m agent_os.cli --db ./agent_os.sqlite reconcile
```

## Briefing

Purpose:

- Tell the human what needs judgment, what is running, what is blocked, what is
  waiting, and what is ready.

Command shape:

```bash
python3 -m agent_os.cli --db ./agent_os.sqlite brief
```
