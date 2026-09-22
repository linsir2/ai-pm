CREATE TABLE IF NOT EXISTS documents (
  project_id         TEXT PRIMARY KEY,
  document_id        TEXT NOT NULL,
  title              TEXT NOT NULL,
  blocks             TEXT NOT NULL,
  preference_entries TEXT NOT NULL,
  tombstones         TEXT NOT NULL,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_versions (
  project_id        TEXT NOT NULL REFERENCES documents(project_id) ON DELETE CASCADE,
  version_no        INTEGER NOT NULL,
  trigger           TEXT NOT NULL,
  summary           TEXT NOT NULL,
  changed_block_ids TEXT NOT NULL,
  snapshot          TEXT NOT NULL,
  created_at        TEXT NOT NULL,
  PRIMARY KEY (project_id, version_no)
);

CREATE TABLE IF NOT EXISTS rounds (
  round_id   TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  step       TEXT NOT NULL,
  waiting_on TEXT NOT NULL,
  payload    TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rounds_project ON rounds(project_id, created_at);

CREATE TABLE IF NOT EXISTS evidence_ledgers (
  project_id TEXT PRIMARY KEY REFERENCES documents(project_id) ON DELETE CASCADE,
  payload    TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
