-- L7 存储底座的表结构。改这里要同时加一条迁移并升 SCHEMA_VERSION。

CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL
);

-- ── 账本（长期） ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS projects (
    project_id     TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    template_id    TEXT NOT NULL,
    config_json    TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    doc_id         TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL,
    template_id    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blocks (
    doc_id         TEXT NOT NULL,
    block_id       TEXT NOT NULL,
    parent_id      TEXT,
    schema_label   TEXT NOT NULL,
    content        TEXT NOT NULL,
    version        INTEGER NOT NULL,
    source_card_id TEXT,
    position       INTEGER NOT NULL,
    PRIMARY KEY (doc_id, block_id)
);

CREATE TABLE IF NOT EXISTS document_versions (
    version_id        TEXT PRIMARY KEY,
    doc_id            TEXT NOT NULL,
    seq               INTEGER NOT NULL,
    snapshot_json     TEXT NOT NULL,
    trigger           TEXT NOT NULL,
    group_id          TEXT,
    target_version_id TEXT,
    UNIQUE (doc_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_document_versions_doc ON document_versions (doc_id, seq);

-- ── 流水（只增不改） ───────────────────────────────────────────
-- 它回答一个问题："这件事到底发了没有"。列与 CONTRACTS C13 的不变量一一对应。

CREATE TABLE IF NOT EXISTS events (
    event_id     TEXT PRIMARY KEY,
    at           TEXT NOT NULL,
    type         TEXT NOT NULL,
    round_id     TEXT,
    project_id   TEXT,
    producer     TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_round ON events (round_id);

-- ── 工作台（黑板） ─────────────────────────────────────────────
-- `round` 区块的家就是 rounds 表：轮末删掉区块，这一行长期保留。

CREATE TABLE IF NOT EXISTS rounds (
    round_id    TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    entry       TEXT NOT NULL,
    user_input  TEXT NOT NULL,
    scope_json  TEXT NOT NULL,
    phase       TEXT NOT NULL,
    ended_at    TEXT,
    end_reason  TEXT
);

CREATE INDEX IF NOT EXISTS idx_rounds_project ON rounds (project_id);
CREATE INDEX IF NOT EXISTS idx_rounds_phase ON rounds (phase);

-- 其余四个区块（context / claims / card_group / confirmed）只装当前未结束的轮。

CREATE TABLE IF NOT EXISTS board_regions (
    round_id     TEXT NOT NULL,
    region       TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (round_id, region)
);
