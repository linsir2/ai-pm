CREATE TABLE IF NOT EXISTS documents (
  project_id         TEXT PRIMARY KEY,
  owner_id           TEXT,
  document_id        TEXT NOT NULL,
  title              TEXT NOT NULL,
  template           TEXT NOT NULL DEFAULT '{}',
  blocks             TEXT NOT NULL,
  preference_entries TEXT NOT NULL,
  tombstones         TEXT NOT NULL,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_versions (
  project_id        TEXT NOT NULL REFERENCES documents(project_id) ON DELETE CASCADE,
  owner_id          TEXT,
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
  owner_id   TEXT,
  step       TEXT NOT NULL,
  waiting_on TEXT NOT NULL,
  payload    TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rounds_project ON rounds(project_id, created_at);

-- 模板库（`02` §11.0.68）：定义时来源。行内快照（`documents.template`）才是运行真相，
-- 所以这里允许 seed 反复覆盖 builtin 行，老项目不受影响。
CREATE TABLE IF NOT EXISTS templates (
  template_id TEXT PRIMARY KEY,
  owner_id    TEXT,
  title       TEXT NOT NULL,
  spec        TEXT NOT NULL,
  builtin     INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_ledgers (
  project_id TEXT PRIMARY KEY REFERENCES documents(project_id) ON DELETE CASCADE,
  owner_id   TEXT,
  payload    TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- 材料（工具层 C-2）：一份材料一行，**原始字节与抽出的文字都在这儿**。
-- 为什么留 content：换解析器（docling）时要能重解析，不能让用户重传。
-- 注意：`content` **永不进任何 API 投影**（`as_json` 没有 bytes 分支，进去就是 500）。
CREATE TABLE IF NOT EXISTS materials (
  material_id     TEXT PRIMARY KEY,
  project_id      TEXT NOT NULL,
  owner_id        TEXT,
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
  owner_id     TEXT,
  ordinal      INTEGER NOT NULL,
  text         TEXT NOT NULL,
  heading_path TEXT NOT NULL,
  anchor       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_material_chunks_material ON material_chunks(material_id, ordinal);

-- 材料块的全文索引（检索 P1）：FTS5 **影子表**，存 jieba 预分词后的 token 流。
-- 为什么影子表不是 external-content：external-content 会**原样读源列**，读不到应用层
-- 分词结果（触发器也调不了 Python）；所以由 `SqliteMaterialStore` 在**同一事务**里维护。
-- `chunk_id`/`material_id` 是 UNINDEXED（存值不建索引）：前者用于回连 `material_chunks`，
-- 后者用于按材料整批删（材料删除/重解析时）。
CREATE VIRTUAL TABLE IF NOT EXISTS material_chunks_fts USING fts5(
  chunk_id UNINDEXED,
  material_id UNINDEXED,
  tokens,
  tokenize = 'unicode61'
);

-- 材料块的**向量索引**（检索阶段 2）的 vec0 建表**不在本文件里**：
-- 它要 `sqlite-vec` 扩展先载入才建得出来，而 `schema.sql` 是无条件执行的
-- （`executescript` 不带条件）。扩展载不了时整份 schema 会撞「no such module: vec0」，
-- 后端直接起不来 —— 与「降级到纯 FTS5」矛盾。故由 `database._create_vector_table()`
-- 在载入成功后显式建（见 `docs/plans/2026-10-18-B组-批5-检索向量.md`）。

-- 探索运行（M8）：一趟调研（将来还有讨论/体检）一行，产物与候选状态全在 payload。
-- 与 rounds 同一个范式：列只放"要按它查"的（项目/状态），其余进 JSON。
CREATE TABLE IF NOT EXISTS explorations (
  exploration_id TEXT PRIMARY KEY,
  project_id     TEXT NOT NULL,
  owner_id       TEXT,
  kind           TEXT NOT NULL,
  status         TEXT NOT NULL,
  payload        TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_explorations_project ON explorations(project_id, created_at);

-- 讨论线程（M9）：一条自由主题的多轮讨论一行，轮次与卡片全在 payload。
-- 与 explorations 同一个范式：列只放"要按它查"的（项目/状态），其余进 JSON。
CREATE TABLE IF NOT EXISTS discussions (
  discussion_id TEXT PRIMARY KEY,
  project_id     TEXT NOT NULL,
  owner_id       TEXT,
  status         TEXT NOT NULL,
  payload        TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_discussions_project ON discussions(project_id, created_at);

-- 跨轮记忆（M6，原设计里的 PROJECT.md）：一个项目一行。
-- payload 里两段：`entries`（事实）与 `revisions`（它怎么变成现在这样的）。
--
-- 为什么只有这一块落库：章节状态表 / 阻塞项 / 铁律**机械可算**（就绪度、s11、提示词硬规矩），
-- 存一份就是第二真相源（`02` §11.0.10）。这张表只装"模型判断 + 用户逐条确认"过的东西。
-- 条目与版本只增不改：一次确认 = 一条 revision，删掉的条目**不出现在 entries 里但留在 revisions 里**。
CREATE TABLE IF NOT EXISTS project_memory (
  project_id TEXT PRIMARY KEY,
  owner_id   TEXT,
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

-- L2 轮次观察（记忆层 M-boat）：席位意见提炼的结构化记录。
-- 席位意见每轮结束就丢了，L2 把它们存下来供跨轮渐进式披露。
CREATE TABLE IF NOT EXISTS observations (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  owner_id   TEXT,
  round_id   TEXT NOT NULL,
  obs_type   TEXT NOT NULL,
  title      TEXT NOT NULL,
  detail     TEXT NOT NULL DEFAULT '',
  source_ref TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_observations_project ON observations(project_id, id);
CREATE INDEX IF NOT EXISTS idx_observations_round ON observations(round_id);

-- L4 轮次摘要（记忆层）：每轮确认后生成的结构化摘要，供跨轮注入。
CREATE TABLE IF NOT EXISTS round_digests (
  id          TEXT PRIMARY KEY,
  project_id  TEXT NOT NULL,
  owner_id    TEXT,
  round_id    TEXT NOT NULL,
  summary     TEXT NOT NULL,
  sections    TEXT NOT NULL DEFAULT '[]',
  features    TEXT NOT NULL DEFAULT '[]',
  outcomes    TEXT NOT NULL DEFAULT '[]',
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_round_digests_project ON round_digests(project_id, id);

-- 用户（多用户改造）：待审批 → active / banned。`password_hash` 由 Task 1 的
-- argon2 模块产出，仓储只存字符串、不碰密码学。
CREATE TABLE IF NOT EXISTS users (
  user_id     TEXT PRIMARY KEY,
  username    TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',
  role        TEXT NOT NULL DEFAULT 'user',
  approved_by TEXT,
  approved_at TEXT,
  created_at  TEXT NOT NULL
);

-- 登录会话：**明文 token 永不入库**，只存 `hashlib.sha256` 的十六进制摘要。
-- 过期由调用方判（`expires_at` 是列，方便"这个用户的活会话"这类查询）。
-- `created_at` 是绝对上限（90 天）的锚点：滑动续期只动 `expires_at`，绝不动它。
CREATE TABLE IF NOT EXISTS sessions (
  session_id  TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  token_hash  TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  ip          TEXT NOT NULL DEFAULT '',
  user_agent  TEXT NOT NULL DEFAULT '',
  revoked_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, expires_at);

-- L3 灵感档案（记忆层）：讨论卡/探索假设/模拟发现的可搜索存档。
CREATE TABLE IF NOT EXISTS inspirations (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  owner_id   TEXT,
  origin     TEXT NOT NULL,
  statement  TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inspirations_project ON inspirations(project_id, id);

-- 多用户改造（Task 5）：owner 列的索引。读过滤一律 `AND owner_id = ?`，写盖章 `owner_id`。
-- 复合索引把 owner 放前导列（查询总是先按 owner 收窄），后跟该表的主查询维度。
-- 主键 / 唯一约束**一字不动**（`materials` 去重键仍是 `(project_id, sha256)`）。
CREATE INDEX IF NOT EXISTS idx_documents_owner ON documents(owner_id);
CREATE INDEX IF NOT EXISTS idx_document_versions_owner ON document_versions(owner_id, project_id);
CREATE INDEX IF NOT EXISTS idx_rounds_owner ON rounds(owner_id, project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_evidence_ledgers_owner ON evidence_ledgers(owner_id);
CREATE INDEX IF NOT EXISTS idx_materials_owner ON materials(owner_id, project_id);
CREATE INDEX IF NOT EXISTS idx_material_chunks_owner ON material_chunks(owner_id, material_id);
CREATE INDEX IF NOT EXISTS idx_explorations_owner ON explorations(owner_id, project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_discussions_owner ON discussions(owner_id, project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_project_memory_owner ON project_memory(owner_id);
CREATE INDEX IF NOT EXISTS idx_observations_owner ON observations(owner_id, project_id);
CREATE INDEX IF NOT EXISTS idx_round_digests_owner ON round_digests(owner_id, project_id);
CREATE INDEX IF NOT EXISTS idx_inspirations_owner ON inspirations(owner_id, project_id);
CREATE INDEX IF NOT EXISTS idx_templates_owner ON templates(owner_id);
