# PostgreSQL 全量切换 runbook（T9）

> 目标：停服 → 备份旧 SQLite（回滚用）→ bootstrap/schema-only → ETL → finalize →
> 校验 → 切 app URL → smoke → 观察期。**不双写、不反向 ETL、不回写旧库。**

## 0. 前置检查

- 旧库：`backend/data/pmstudio.db`；先确认 `PRAGMA user_version` 是 15 或 17。
- 新库：PostgreSQL 18 + pgvector 0.8.7；`pmstudio_owner` / `pmstudio_app` 已由
  `init_db --bootstrap` 建好。
- ETL 只读旧库，只连 **owner 直连 URL**（`PMSTUDIO_DIRECT_DATABASE_URL`）；
  **不要**把 app URL / PgBouncer URL 传给 ETL。
- 本脚本/测试**不进生产镜像**；ETL 在迁移窗口的宿主机上跑。

## 1. 停服并做旧库回滚备份（保留 30 天）

1. 停后端/任何写旧库的进程；确认没有进程还开着 `pmstudio.db`。
2. 用旧库自己的 `.backup`（**不要 `cp` 正在跑的 SQLite**；`-wal` 里的数据会少）：

```bash
cd backend
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p data/backups/legacy && chmod 700 data/backups/legacy
sqlite3 data/pmstudio.db ".backup 'data/backups/legacy/pmstudio-$stamp.db'"
chmod 600 "data/backups/legacy/pmstudio-$stamp.db"
sqlite3 "data/backups/legacy/pmstudio-$stamp.db" "PRAGMA integrity_check; PRAGMA user_version;"
```

3. 把备份路径写进变更记录；这份文件是**唯一回滚数据源**，保留至少 30 天。

## 2. 初始化空 PG（先不 finalize）

```bash
cd backend
uv run python scripts/init_db.py --bootstrap
uv run python scripts/init_db.py --schema-only
```

`--schema-only` 只跑编号迁移；此时目标 19 张普通表为空。**不要**先跑 `--finalize`，
否则 ETL 会以“目标表非空”拒绝合并。

## 3. ETL

```bash
cd backend
uv run python scripts/migrate_sqlite_to_pg.py --sqlite data/pmstudio.db
```

- 失败时脚本非零退出；数据插入在**一个事务**里，失败不会留下半截。若向量重嵌入
  阶段失败或有人手工改过目标表，确认是重跑后加 `--truncate` 清空 19 张表再跑；
  `--truncate` 不碰 `schema_migrations`。
- 输出会逐表打印行数、running 去重、`vector_index_meta`、重嵌入块数，以及
  `row_counts/values/json/primary_keys/tokens/roundtrip` 校验。任何一项失败都不算完成。
- 预期：`sessions` 显示“跳过（源 N / 目标 0）”；重复 running 除最新外改
  `abandoned` 但保留全部行；重复 `(project_id, sha256)` 会直接中止。

## 4. finalize

```bash
cd backend
uv run python scripts/init_db.py --finalize
```

finalize 会 seed 首管理员/内置模板、回填 owner、断言孤儿、upsert
`vector_index_meta`（维度 ≠ 512 明确失败）、给 app 角色授权。**ETL 后必须 finalize**。

## 5. 校验

- 看 ETL 输出：行数/主键哈希/JSON/roundtrip/tokens 全 OK。
- 看 finalize 输出：owner 回填数、内置模板数、向量 profile 512 维。
- 旧库有材料时，ETL 的 tokens/检索链路必须非空；无材料时用临时库的 ETL 测试
  （`tests/test_etl_cutover.py`）覆盖 recall，不允许拿“0 材料”跳过验收。
- 真备份/恢复演练（PG18 客户端；会建 scratch 库并删掉）：

```bash
cd backend
bash scripts/restore_check.sh
```

## 6. 切 app URL 并 smoke

1. 把 backend 的 `PMSTUDIO_DATABASE_URL` 指向 PgBouncer（6432），ops/init 仍用
   `PMSTUDIO_DIRECT_DATABASE_URL`（5432）；两个 URL 不互相推导。
2. 起后端；`GET /api/ready` 必须 200（`/api/health` 只是 liveness）。
3. 登录（**所有用户重新登录**：`sessions` 不迁，旧 cookie 必然 401）。
4. 打开一个旧项目：文档标题/版本/证据可读；打开最近一轮；材料搜索有结果。
5. 观察期至少一个工作日：错误率、`/api/ready`、慢查询、备份任务。

## 7. 备份与 RPO

- 切换后由 `scripts/backup.sh` 每日 03:00 做 PG `pg_dump -Fc`，保留 7 日 + 4 周；
  **RPO ≤ 24h**；每小时 `scripts/backup.sh --check` 发现“昨天没备”。
- 恢复演练用 `scripts/restore_check.sh`；恢复后 `TRUNCATE sessions`，旧 cookie 失效。
- 旧 SQLite 备份保留 30 天；确认不再回滚后才删。

## 8. 回滚

1. 停新后端，确认没有写入 PG。
2. 用旧镜像 + 旧配置（旧 `PMSTUDIO_DB`）指向第 1 步的 SQLite 备份；不要改旧库、
   不反向 ETL。
3. 为避免旧备份里的会话复活，启动前在恢复出的旧库执行 `DELETE FROM sessions;`
   （或等价地让所有用户重新登录）。
4. 明确损失：**切流之后写入 PG 的数据不会回到 SQLite**；回滚的 RPO 是“切流那一刻”，
   不是 24h。PG 数据保留现场，不并入旧库、不双运行时。

## 9. 明确不做

- 不双写、不回写 SQLite、不做影子读。
- 不把 ETL 脚本进镜像；T10 的 no-sqlite 守卫会把它和对应测试加白名单。
- 不在线 `ALTER COLUMN TYPE vector(N)`；换 profile/维度走显式迁移 + 重建 HNSW。
