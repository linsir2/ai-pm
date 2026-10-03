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

-- 材料（工具层 C-2）：一份材料一行，**原始字节与抽出的文字都在这儿**。
-- 为什么留 content：换解析器（docling）时要能重解析，不能让用户重传。
-- 注意：`content` **永不进任何 API 投影**（`as_json` 没有 bytes 分支，进去就是 500）。
CREATE TABLE IF NOT EXISTS materials (
  material_id     TEXT PRIMARY KEY,
  project_id      TEXT NOT NULL,
  origin          TEXT NOT NULL,
  title           TEXT NOT NULL,
  source_ref      TEXT NOT NULL,
  media_type      TEXT NOT NULL,
  bytes_len       INTEGER NOT NULL,
  sha256          TEXT NOT NULL,
  parser_id       TEXT NOT NULL,
  parser_version  TEXT NOT NULL,
  schema_version  INTEGER NOT NULL,
  truncated       INTEGER NOT NULL,
  warnings        TEXT NOT NULL,
  content         BLOB NOT NULL,
  created_at      TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_materials_dedupe ON materials(project_id, sha256);
CREATE INDEX IF NOT EXISTS idx_materials_project ON materials(project_id, created_at);

-- 材料块：检索、引用、渲染的单位。块正文只在这里（列表接口不吐它）。
CREATE TABLE IF NOT EXISTS material_chunks (
  chunk_id     TEXT PRIMARY KEY,
  material_id  TEXT NOT NULL REFERENCES materials(material_id) ON DELETE CASCADE,
  ordinal      INTEGER NOT NULL,
  text         TEXT NOT NULL,
  heading_path TEXT NOT NULL,
  anchor       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_material_chunks_material ON material_chunks(material_id, ordinal);

-- 跨轮记忆（M6，原设计里的 PROJECT.md）：一个项目一行。
-- payload 里两段：`entries`（事实）与 `revisions`（它怎么变成现在这样的）。
--
-- 为什么只有这一块落库：章节状态表 / 阻塞项 / 铁律**机械可算**（就绪度、s11、提示词硬规矩），
-- 存一份就是第二真相源（`02` §11.0.10）。这张表只装"模型判断 + 用户逐条确认"过的东西。
-- 条目与版本只增不改：一次确认 = 一条 revision，删掉的条目**不出现在 entries 里但留在 revisions 里**。
CREATE TABLE IF NOT EXISTS project_memory (
  project_id TEXT PRIMARY KEY,
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
