PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS workspaces (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actors (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id),
  source TEXT NOT NULL,
  external_id TEXT,
  event_type TEXT NOT NULL,
  summary TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  occurred_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  processed_at TEXT,
  UNIQUE(source, external_id)
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id),
  source_event_id TEXT REFERENCES events(id),
  title TEXT NOT NULL,
  goal TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN (
    'new',
    'triaged',
    'ready',
    'running',
    'needs_approval',
    'blocked',
    'waiting',
    'done',
    'archived'
  )),
  priority TEXT NOT NULL DEFAULT 'normal',
  owner TEXT NOT NULL DEFAULT 'agent',
  boundary TEXT NOT NULL,
  approval_required INTEGER NOT NULL DEFAULT 0,
  due_at TEXT,
  lease_until TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
CREATE INDEX IF NOT EXISTS idx_tasks_workspace_state ON tasks(workspace_id, state);

CREATE TABLE IF NOT EXISTS task_events (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN (
    'prepared',
    'running',
    'done',
    'blocked',
    'needs_approval',
    'needs_followup',
    'stale',
    'cancelled'
  )),
  actor TEXT NOT NULL DEFAULT 'codex',
  boundary TEXT NOT NULL,
  expected_proof TEXT NOT NULL,
  prompt TEXT NOT NULL,
  result_json TEXT NOT NULL DEFAULT '{}',
  thread_id TEXT,
  thread_url TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  dispatched_at TEXT,
  lease_until TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_task_status ON runs(task_id, status);

CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('requested', 'approved', 'rejected', 'expired')),
  question TEXT NOT NULL,
  risk TEXT NOT NULL DEFAULT 'medium',
  proposed_action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  decided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);

CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
  artifact_type TEXT NOT NULL,
  title TEXT NOT NULL,
  uri TEXT,
  body TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id),
  knowledge_type TEXT NOT NULL CHECK (knowledge_type IN (
    'fact',
    'rule',
    'preference',
    'playbook',
    'opportunity'
  )),
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.5,
  status TEXT NOT NULL CHECK (status IN ('proposed', 'active', 'rejected', 'stale')),
  source_event_id TEXT REFERENCES events(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_knowledge_status ON knowledge(status);

CREATE TABLE IF NOT EXISTS briefs (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id),
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);
