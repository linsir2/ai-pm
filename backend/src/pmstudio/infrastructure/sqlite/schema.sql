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

-- 模型留样（`02` §5.3）：默认不写，`PMSTUDIO_TRACE_MODEL=1` 时才记。
-- 它不参与业务：写不进去也不影响这一轮；要清就直接 DELETE。
-- 每次**尝试**一行（重试后成功也留：不然"刚才为什么慢"永远查不到）：
-- `attempt` 第几次尝试、`ok` 成没成、`error` 失败原因（成功为 NULL）。
CREATE TABLE IF NOT EXISTS model_calls (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  round_id    TEXT,
  purpose     TEXT NOT NULL,
  model       TEXT NOT NULL,
  prompt_sha  TEXT NOT NULL,
  prompt_text TEXT NOT NULL,
  reply_text  TEXT NOT NULL,
  usage_json  TEXT NOT NULL,
  duration_ms INTEGER NOT NULL,
  created_at  TEXT NOT NULL,
  attempt     INTEGER NOT NULL DEFAULT 1,
  ok          INTEGER NOT NULL DEFAULT 1,
  error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_model_calls_round ON model_calls(round_id, id);
