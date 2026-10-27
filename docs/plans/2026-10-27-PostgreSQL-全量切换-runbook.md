# PostgreSQL 全量切换 runbook（T9）

> 目标：停服 → 备份旧 SQLite（回滚用）→ bootstrap/schema-only → ETL → finalize →
> 校验 → 切 app URL → smoke → 观察期。**不双写、不反向 ETL、不回写旧库。**

## 0. 前置检查与配置来源

- 旧库：`backend/data/pmstudio.db`；先确认 `PRAGMA user_version` 是 15 或 17
  （ETL 只支持这两个版本，其它版本直接失败）。
- 新库：PostgreSQL 18 + pgvector 0.8.7。
- 运维侧配置只在 `backend/.env.ops`：`PMSTUDIO_DIRECT_DATABASE_URL`（owner 直连）、
  `PMSTUDIO_BOOTSTRAP_DATABASE_URL`（超级用户，只给 `--bootstrap`）、
  `POSTGRES_*`、`PMSTUDIO_OWNER_PASSWORD` / `PMSTUDIO_APP_PASSWORD`、首管理员 seed。
  **backend 的 `backend/.env` 不包含这些 URL。**
- compose 默认**不发布 db 5432**，且 `.env.ops` 里的 host 是 compose 内网 `db`：
  宿主机进程既解析不了 `db`，也连不上 5432。默认按下面“容器内一次性命令”跑；
  若坚持在宿主机跑，必须自己把 direct URL 变成宿主可达（临时 `127.0.0.1:5432`
  发布或 SSH 隧道），并自行准备下面的 auth_file。
- **PgBouncer auth_file 来源**：compose 的 `init` 服务把
  `PMSTUDIO_PGBOUNCER_AUTH_FILE=/pgbouncer-auth/userlist.txt` 写进共享卷
  `pgbouncer-auth`，`pgbouncer` 只读挂载同一卷。宿主机跑 `--bootstrap` 时必须
  显式给这个环境变量/可写路径，并保证 PgBouncer 容器能读到（uid 通常 70）；
  否则 PgBouncer 没有口令文件，切流后连不上。
- ETL 脚本**不在镜像里**。容器内跑时把宿主 `backend/scripts/` 只读挂到
  `/app/scripts`；ETL 只读旧库，只连 **owner 直连 URL**，不要把 app/PgBouncer
  URL 传给它。

## 1. 停服并做旧库回滚备份（保留 30 天）

1. 停后端/任何写旧库的进程；确认没有进程还开着 `pmstudio.db`。
2. 用旧库自己的 `.backup`（**不要 `cp` 正在跑的 SQLite**；`-wal` 里的数据会少）：

```bash
cd /srv/pmstudio/backend
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p data/backups/legacy && chmod 700 data/backups/legacy
sqlite3 data/pmstudio.db ".backup \"data/backups/legacy/pmstudio-$stamp.db\""
chmod 600 "data/backups/legacy/pmstudio-$stamp.db"
sqlite3 "data/backups/legacy/pmstudio-$stamp.db" "PRAGMA integrity_check; PRAGMA user_version;"
```

3. 把备份路径写进变更记录；这份文件是**唯一回滚数据源**，保留至少 30 天。

## 2. 初始化空 PG（默认：compose 内一次性容器）

```bash
cd /srv/pmstudio
set -a; . backend/.env.ops; set +a   # 只为把 URL/口令交给下面的 compose run

docker compose up -d db
docker compose run --rm --no-deps init \
  uv run --no-sync python scripts/init_db.py --bootstrap
docker compose run --rm --no-deps init \
  uv run --no-sync python scripts/init_db.py --schema-only
```

- `init` 服务继承 `.env.ops`、`PMSTUDIO_PGBOUNCER_AUTH_FILE` 与共享卷，正好满足
  bootstrap 的 auth_file 来源；不要用宿主进程直接跑 `--bootstrap` 来代替它。
- `--schema-only` 只跑编号迁移；此时目标 19 张普通表为空。**不要**先跑
  `--finalize`，否则 ETL 会以“目标表非空”拒绝合并。

### 宿主直连替代方案（可选）

只有在你已经把 direct URL 变成宿主可达时才这样做：

```bash
cd /srv/pmstudio/backend
set -a; . .env.ops; set +a
# 将 @db:5432 改成本机可达地址（例如临时发布的 127.0.0.1:5432 或隧道）
export PMSTUDIO_DIRECT_DATABASE_URL="postgresql://pmstudio_owner:...@127.0.0.1:5432/pmstudio"
export PMSTUDIO_BOOTSTRAP_DATABASE_URL="postgresql://pmstudio_bootstrap:...@127.0.0.1:5432/pmstudio"
export PMSTUDIO_PGBOUNCER_AUTH_FILE="/srv/pmstudio/pgbouncer-auth/userlist.txt"
uv run python scripts/init_db.py --bootstrap
uv run python scripts/init_db.py --schema-only
```

## 3. ETL

默认在 compose 网络内跑（`db:5432` 可达；脚本从宿主挂载）：

```bash
cd /srv/pmstudio
set -a; . backend/.env.ops; set +a
docker compose run --rm --no-deps \
  -e PMSTUDIO_DIRECT_DATABASE_URL="$PMSTUDIO_DIRECT_DATABASE_URL" \
  -v "$PWD/backend/scripts:/app/scripts:ro" \
  -v "$PWD/backend/data:/legacy:ro" \
  backend uv run --no-sync python scripts/migrate_sqlite_to_pg.py \
    --sqlite /legacy/backups/legacy/pmstudio-<stamp>.db
```

宿主直连替代方案：

```bash
cd /srv/pmstudio/backend
set -a; . .env.ops; set +a
export PMSTUDIO_DIRECT_DATABASE_URL="postgresql://pmstudio_owner:...@127.0.0.1:5432/pmstudio"
uv run python scripts/migrate_sqlite_to_pg.py \
  --sqlite data/backups/legacy/pmstudio-<stamp>.db
```

- **插入 + 校验在同一个事务里**：行数/主键/值/JSON/roundtrip/tokens 失败会整体
  回滚。`--truncate` 在事务外先清空目标 19 表；它不碰 `schema_migrations`。
- **embedding 是提交后的独立阶段**：模型调用在事务外；失败时业务行和部分
  `embedding` 可能已落库。ETL 的 `_verify` **不检查 embedding**。失败后确认是
  重跑场景再加 `--truncate` 清空重跑（不要手工合并）。
- 输出逐表行数、running 去重、`vector_index_meta`、重嵌入块数，以及
  `row_counts/values/json/primary_keys/tokens/roundtrip` 校验。任何一项失败都
  不算完成。
- 预期：`sessions` 显示“跳过（源 N / 目标 0）”；重复 running 除最新外改
  `abandoned` 但保留全部行；重复 `(project_id, sha256)` / 未知表 / 未知源列 /
  `user_version` 不是 15/17 都会直接中止。

## 4. finalize

```bash
cd /srv/pmstudio
docker compose run --rm --no-deps init \
  uv run --no-sync python scripts/init_db.py --finalize
```

finalize 会 seed 首管理员/内置模板、回填 owner、断言孤儿、upsert
`vector_index_meta`（维度 ≠ 512 明确失败）、给 app 角色授权。**ETL 后必须 finalize**。

## 5. 校验

- 看 ETL 输出：行数/主键哈希/值/JSON/tokens/roundtrip 全 OK。
- 看 finalize 输出：owner 回填数、内置模板数、向量 profile 512 维。
- **T9 的验收口径只有 tokens 相等与 roundtrip**；recall@5 / FTS/BM25/HNSW 排序
  不在 T9 验收，挂 T5/T10 的 hard gate（现有罕见词排序用例已明确 xfail）。
  旧库有材料时额外确认 tokens 非空即可，不能把“查得到一条”说成 recall 已验收。
- 真备份/恢复演练（PG18 客户端；会建 scratch 库并删掉）：

```bash
cd /srv/pmstudio
docker compose run --rm --no-deps \
  -e PMSTUDIO_DIRECT_DATABASE_URL="$PMSTUDIO_DIRECT_DATABASE_URL" \
  -e PMSTUDIO_BOOTSTRAP_DATABASE_URL="$PMSTUDIO_BOOTSTRAP_DATABASE_URL" \
  -v "$PWD/backend/data/backups:/backups" \
  backend bash scripts/restore_check.sh
```

## 6. 切 app URL 并 smoke

1. 把 backend 的 `PMSTUDIO_DATABASE_URL` 指向 PgBouncer（6432），ops/init 仍用
   `PMSTUDIO_DIRECT_DATABASE_URL`（5432）；两个 URL 不互相推导。
2. 起后端；`GET /api/ready` 必须 200（`/api/health` 只是 liveness）。
3. 登录（**所有用户重新登录**：`sessions` 不迁，旧 cookie 必然 401）。
4. 打开一个旧项目：文档标题/版本/证据可读；打开最近一轮；材料搜索能返回结果
   （这只是 smoke，不是 recall 验收）。
5. 观察期至少一个工作日：错误率、`/api/ready`、慢查询、备份任务。

## 7. 备份与 RPO

- 切换后每日 03:00 用镜像内 PG18 客户端备份（`PostgreSQL` custom format）：

```bash
cd /srv/pmstudio
docker compose run --rm --no-deps \
  -e PMSTUDIO_DIRECT_DATABASE_URL="$PMSTUDIO_DIRECT_DATABASE_URL" \
  -v "$PWD/backend/data/backups:/backups" \
  backend bash scripts/backup.sh /backups
```

- 保留 7 日 + 4 周；**RPO ≤ 24h**；每小时 `backup.sh --check` 发现“昨天没备”。
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
