# PostgreSQL 全量切换 · 逐文件影响清单

基线 `new-branch@d53c53d`。本清单由实际 `rg`/grep 结果生成，是 T7/T8/T9/T10 的执行核对表；不要用“约 45 个”代替。

## A. 测试（50 个命中）

### A1. 改为 PG 后重写/迁移（真 DB）
```text
tests/api/test_database_busy.py
tests/application/test_eval_script.py
tests/application/test_telemetry_memory_hits.py
tests/application/test_telemetry.py
tests/application/test_telemetry_watermarks.py
tests/application/test_tool_call_recording.py
tests/bootstrap/test_maintenance.py
tests/bootstrap/test_settings.py
tests/e2e/test_concurrent_smoke.py
tests/e2e/test_disk_full.py
tests/e2e/test_isolation_matrix.py
tests/e2e/test_round_claim_e2e.py
tests/e2e/test_uvicorn_multiworker.py
tests/infrastructure/test_call_log.py
tests/infrastructure/test_embedding.py
tests/infrastructure/test_exploration_lease.py
tests/infrastructure/test_keep_sim_lease.py
tests/infrastructure/test_material_search_hybrid.py
tests/infrastructure/test_material_vec_store.py
tests/infrastructure/test_material_vector_writeback.py
tests/infrastructure/test_model_calls.py
tests/infrastructure/test_round_lease.py
tests/infrastructure/test_scoped_reads.py
tests/infrastructure/test_sqlite_digest.py
tests/infrastructure/test_sqlite_discussions.py
tests/infrastructure/test_sqlite_explorations.py
tests/infrastructure/test_sqlite_inspiration.py
tests/infrastructure/test_sqlite_leases.py
tests/infrastructure/test_sqlite_material_search_fts.py
tests/infrastructure/test_sqlite_materials.py
tests/infrastructure/test_sqlite_memory.py
tests/infrastructure/test_sqlite_observations.py
tests/infrastructure/test_sqlite_repositories.py
tests/infrastructure/test_sqlite_templates.py
tests/infrastructure/test_template_loader.py
tests/infrastructure/test_tool_calls.py
tests/infrastructure/test_user_store.py
tests/infrastructure/test_vector_index.py
tests/support/leases.py
tests/test_dev_script.py
```
### A2. 删除/替换（SQLite 语义不再存在）
```text
tests/infrastructure/test_wal_reset.py        # 删
tests/infrastructure/test_migration_v13.py    # 改 PG/ETL 迁移
tests/infrastructure/test_migration_v17.py    # 改 PG/ETL 迁移
tests/e2e/test_disk_full.py                   # 改 PG 53100 或明确删
tests/e2e/test_concurrent_smoke.py            # 改 PG 并发
```
### A3. 架构守卫（改判据，不删）
```text
tests/architecture/guards.py
tests/architecture/test_container_assets.py
tests/architecture/test_domain_imports.py
tests/architecture/test_harness_imports.py
tests/architecture/test_layers.py
tests/architecture/test_owner_columns.py
tests/architecture/test_parse_entry_points.py
```
> 旧 `test_owner_columns` 从 `schema.sql`/PRAGMA 改 `information_schema`；`test_container_assets` 改 app/direct URL、db/pgbouncer/init 服务；`guards.py` 去 SQLite 资产。

## B. 脚本（50 个命中）

### B1. 运行时/验收：必须迁 PG
```text
scripts/backup.sh
scripts/check_api_parity.py
scripts/dev.py
scripts/eval.py
scripts/mcp_probe.py
scripts/multiworker_smoke.py
scripts/p3a_web_smoke.py
scripts/p3b_concurrency_smoke.py
scripts/restore_check.sh
scripts/smoke.py
scripts/telemetry.py
scripts/chaos_matrix.py          # 若未命中 sqlite 文本，也依赖 SQLite 测试
scripts/load_probe.py            # 当前只打 health，T7 重写
```
### B2. 运行/验收 smoke，按消费者决定迁移或归档
```text
scripts/discussion_audit_db.py
scripts/discussion_findings_smoke.py
scripts/discussion_round_smoke.py
scripts/discussion_seed_smoke.py
scripts/discussion_smoke.py
scripts/exploration_smoke.py
scripts/sim_inquiry_smoke.py
scripts/sim_review_smoke.py
```
### B3. 一次性 bench/probe/证据：归档或删除（不逐个重写）
```text
scripts/char_fallback_probe.py
scripts/chunking_experiment.py
scripts/context_layer_bench.py
scripts/disk_full_probe.py
scripts/e2e_web_material.py
scripts/measure_integration_output.py
scripts/memory_dedup_evidence.py
scripts/paraphrase_bench.py
scripts/pdf_parent_chain.py
scripts/rerank_experiment.py
scripts/retrieval_ablation_v4.py
scripts/retrieval_ablation_v5.py
scripts/retrieval_acceptance.py
scripts/retrieval_bench.py
scripts/retrieval_experiment.py
scripts/semantic_floor_probe.py
scripts/tavily_raw_bench.py
scripts/vector_acceptance.py
scripts/vector_budget_bench.py
scripts/vector_coverage_bench.py
scripts/vector_e2e_acceptance.py
scripts/vector_fusion_lab.py
scripts/vector_http_acceptance.py
scripts/vector_recall_bench.py
scripts/vector_web_bench.py
scripts/vector_web_e2e.py
scripts/verify_consistency_probes.py
scripts/verify_html_paths.py
scripts/verify_material_loop.py
scripts/verify_web_search_fullpage.py
scripts/watermark_memory_acceptance.py
scripts/web_retrieval_bench.py
```
> 归档到 `scripts/legacy/` 或删除前先查 `docs/plans`/CI 引用；这些不是生产 runtime。

## C. 生产源码（17 个 sqlite3 import）
```text
api/app.py
bootstrap/container.py
infrastructure/sqlite/call_log.py
infrastructure/sqlite/database.py
infrastructure/sqlite/digest_repo.py
infrastructure/sqlite/inspiration_repo.py
infrastructure/sqlite/leases.py
infrastructure/sqlite/materials.py
infrastructure/sqlite/model_calls.py
infrastructure/sqlite/observations_repo.py
infrastructure/sqlite/project_factory.py
infrastructure/sqlite/repositories.py
infrastructure/sqlite/schema.sql
infrastructure/sqlite/tool_calls.py
infrastructure/sqlite/unit_of_work.py
infrastructure/sqlite/usage.py
infrastructure/sqlite/users.py
infrastructure/sqlite/vector_index.py
```
> `serialization.py`/`embedding.py`/`tokenize.py`/`owner_scope.py` 不 import `sqlite3`，但要迁路径；T10 删除 `infrastructure/sqlite/`。

## D. 文件类型/数据边界

- 全量原生列：`timestamptz`、`jsonb`、`text[]`、`boolean`、`bytea`、`bigint GENERATED BY DEFAULT AS IDENTITY`、`vector(512)`、`tsvector`。
- 例外：JSON 内部 ISO；`observations/round_digests/inspirations.created_at` 的 `''`；`lease_expires_at`/`round_skips.requested_at` epoch BIGINT。
- `vector_index_meta`：19 张普通表之一；`id smallint PK check(id=1)`、profile/model/dimensions；旧 key/value ETL 转换；维度≠512 启动失败或 `ALTER+rebuild`。
- 目标：19 普通表 + `schema_migrations` = 20；`material_chunks.tokens TEXT NOT NULL`；无 FTS5/vec0 虚拟表。

## E. 使用方式

- T7 按 B1/B2 迁移 runtime/验收脚本，B3 归档/删除；T8 按 A1/A2/A3 改测试；T9 用 v15 旧库 + 临时库 ETL；T10 用本表核对删除。
- 任何“删除”都要先查 CI/docs 消费者；没有消费者的才删，有消费者的先迁移。
