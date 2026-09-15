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

