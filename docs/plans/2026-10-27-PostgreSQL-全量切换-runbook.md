# PostgreSQL 部署与运维 runbook（现行）

> **当前形态**：运行时、测试、脚本只连 PostgreSQL 18 + pgvector + pg_search；
> 词法检索是 pg_search BM25，不再有 SQLite、FTS5 或一次性 ETL 路径。
> 本文从空库安装写到备份恢复，按顺序执行即可。

## 0. 前置条件与配置来源

- 镜像：`paradedb/paradedb:0.26.3-pg18`，**按 digest 钉死**
  （`docker-compose.yml` 里的 `sha256:9b15fc…8c02f0`）。官方镜像已带
  **pg_search 0.26.3** 与 **pgvector 0.8.7**，`shared_preload_libraries` 含
  `pg_search`；**换镜像必须用空 PGDATA 卷**（先 `docker compose down -v`）。
- PGDATA 在官方镜像里是 `/var/lib/postgresql/18/docker`，卷要挂父目录
  `/var/lib/postgresql`，不要只挂到 `.../docker`。
- app 侧配置只在 `backend/.env`：`PMSTUDIO_DATABASE_URL`（PgBouncer `pgbouncer:6432`）、
  模型/key、`PMSTUDIO_PORT` 等。backend **不**跑迁移、不 seed。
- 运维侧配置只在 `backend/.env.ops`：`PMSTUDIO_BOOTSTRAP_DATABASE_URL`（超级用户）、
  `PMSTUDIO_DIRECT_DATABASE_URL`（owner 直连）、`POSTGRES_*`、owner/app 角色口令、
  首管理员。
- 三个 URL 分工固定：**bootstrap = 超级用户**（建扩展/角色/auth_file）、
  **direct = owner**（迁移/finalize/备份/维护）、**app = PgBouncer**（请求路径）。
  不要把 app URL 传给 init、backup 或检索基准。

## 1. 空库初始化

### 1.1 compose（默认）

```bash
cp backend/.env.example backend/.env
cp backend/.env.ops.example backend/.env.ops   # 改掉 change-me-* 与首管理员
docker compose up -d --build
```

启动顺序由 compose 保证：`db` 健康 → `init --all`（bootstrap → schema-only →
finalize）→ `pgbouncer` → `backend`。`init` 健康检查必须走 **TCP**
（官方镜像在 init 脚本前会起一个只开 Unix socket 的临时 server，`pg_isready`
默认会命中它，导致迁移过早开跑）。

### 1.2 宿主直连（可选）

只有 direct/bootstrap URL 在宿主机可达时才这样做：

```bash
cd backend
set -a; . .env.ops; set +a
uv run python scripts/init_db.py --bootstrap      # 超级用户：扩展/角色/GRANT/auth_file
uv run python scripts/init_db.py --schema-only    # owner：0001 + 0002
uv run python scripts/init_db.py --finalize       # 首管理员/模板/owner 回填/授权/profile
```

`--bootstrap` 还会给 app/owner 落角色级 DB 超时（app 15s/3s/15s，
owner 60s/10s/60s）；在线重跑后要让 PgBouncer 重连，旧服务端连接不会自动改。

## 2. 初始化后必须核对的五件事

```sql
-- 超级用户
SHOW shared_preload_libraries;                    -- 必须含 pg_search
SELECT extname, extversion FROM pg_extension
 WHERE extname IN ('vector', 'pg_search');         -- vector 0.8.x / pg_search 0.26.x

-- owner 直连
SELECT version FROM schema_migrations ORDER BY version;  -- 1, 2
SELECT profile, dimensions FROM vector_index_meta WHERE id = 1; -- …/natural, 512
SELECT current_schema(), current_setting('timezone');     -- public / UTC

-- app（PgBouncer）
SELECT current_user, current_schema();            -- pmstudio_app / public
SELECT pmstudio_tokens('中文检索');                -- 有 token
SELECT count(*) FROM material_chunks;             -- SELECT 可用
```

- `pg_search` 的 `pdb` / `paradedb` 两个 schema 必须有 **USAGE**，函数必须有
  **EXECUTE**；`vector` 类型必须有 **USAGE**。这些由 `--bootstrap` / `--finalize`
  补齐，恢复或换库后要重新确认。
- app 角色**没有 DDL 权限**：只授 `SELECT/INSERT/UPDATE/DELETE`、序列
  `USAGE/SELECT`、函数 `EXECUTE`；建表/迁移/备份一律走 owner。
- PgBouncer 是 transaction mode（`MAX_PREPARED_STATEMENTS=0`），backend 连接层
  `prepare_threshold=None`；不要在请求路径上开 session 级 GUC。

## 3. 备份与恢复

### 3.1 每日备份（RPO ≤ 24h）

```bash
cd backend
PMSTUDIO_DIRECT_DATABASE_URL=... bash scripts/backup.sh
PMSTUDIO_DIRECT_DATABASE_URL=... bash scripts/backup.sh --check   # 超 24h 未备即失败
```

- 只用 **PG18 客户端** `pg_dump -Fc`；`--no-owner`，排除 `pg_search` / `postgis` /
  `pg_ivm` 等扩展对象和 `paradedb` / `pdb` / `tiger` / `topology` schema
  ——恢复时由超级用户预建扩展，避免与 template1 继承的扩展冲突。
- 备份落盘即 `chmod 600`；保留 7 份日备 + 4 份周备。
- **不要 `cp` 正在运行的 PGDATA**：文件系统快照可能不一致；只认 `pg_dump`。

### 3.2 恢复演练 / 恢复

```bash
cd backend
PMSTUDIO_DIRECT_DATABASE_URL=... PMSTUDIO_BOOTSTRAP_DATABASE_URL=... \
  bash scripts/restore_check.sh
```

演练会真调 `backup.sh`，用超级用户建 scratch 库并装 `vector` / `pg_search`，
再以 **owner** 执行 `pg_restore --exit-on-error --no-owner --no-comments`，
清空 `sessions`，逐表比对行数与指纹。

生产恢复顺序：

1. 超级用户建空库、`CREATE EXTENSION vector` + `CREATE EXTENSION pg_search CASCADE`；
2. 给 owner/app `GRANT USAGE ON SCHEMA pdb, paradedb` 与函数 EXECUTE；
3. owner 执行 `pg_restore --exit-on-error --no-owner --no-comments`；
4. `uv run python scripts/init_db.py --finalize` 重新断言 owner/模板/app 授权与
   向量 profile（幂等；恢复库已有用户时不会重建管理员）；
5. `TRUNCATE sessions`（旧 cookie 必然 401）；
6. 起 backend，打 `/api/ready`，再抽查一次材料搜索（smoke，不是检索验收）。

## 4. 维护与故障

- **向量缺失**：`vector_index_meta` profile 变更会清空 `embedding`；随后由维护任务
  `reembed_missing()` 按 `all-null` + advisory lock + 分批补算。不跑 = 语义检索为空。
  手动触发：`docker compose run --rm maintenance` 或
  `cd backend && uv run python scripts/maintenance_worker.py`。
- **检索验收**：`cd backend && uv run python scripts/retrieval_quality_bench.py
  --chunk-chars 300`（400 复现旧口径）。它只建临时 schema，跑完销毁，不碰生产库。
- **`/api/ready` 200 才是就绪**：`/api/health` 只是 liveness；DB/PgBouncer
  掉线时 health 仍可能 200。
- **慢查询/锁等待**：app 角色有语句 15s / 锁 3s / 泄漏事务 15s 的角色级超时；
  看到 55P03 / 57014 时先查 PgBouncer 池和长事务，不要直接抬 backend 超时。

## 5. 换镜像 / 升级

1. 先备份（custom format）。
2. `docker compose down`；只删旧库卷：`docker volume rm <project>_pgdata`
   （`down -v` 会连 `fastembed-cache`、`pgbouncer-auth` 一起删）。
3. 换 `docker-compose.yml` 的镜像 digest；从**空 PGDATA** 起。
4. 重跑 `init --all`，按 §2 核对；恢复数据走 §3.2。
5. **不做**数据目录拷贝、不双写、不反向迁移；旧库数据未导入就是未导入，
   恢复来源只有备份。

## 6. 明确不做

- 不需要也不提供 SQLite / FTS5 / 一次性 ETL 路径。
- 不把 `pg_dump` 之外的 PGDATA 文件复制当备份。
- 不在线 `ALTER COLUMN TYPE vector(N)`；换维度走显式迁移 + 重建 HNSW + 重嵌。
- 不在请求路径上跑迁移、seed 或扩展安装。
