# PM Studio 后端镜像。
#
# **为什么是 `python:3.13-slim` 而不是 alpine**：`fastembed` 依赖
# `onnxruntime`，它要 glibc，alpine（musl）装不上 → 构建失败或静默不可用。
#
# **为什么整树 COPY `src/` 而不是只 COPY `.py`**：提示词（`.md`）、工具与席位
# 声明（`.toml`）、模板、`schema.sql` 都是**运行时读的资源**。漏 COPY 的症状是
# **静默的**——`Template("").substitute()` 是合法的，于是每一轮都拿着空提示词
# 去调模型而**不报错**（`tests/architecture/test_resource_paths.py` 点名过这个坑）。

# 基础镜像**按 digest 钉死**（浮动标签会随 Debian 发版漂移）。
FROM python:3.13-slim@sha256:57b29a7c050c4e679e4bea572e14b3965cf4b5839de2da49d55e49ffc4024f39

# lxml 需要 libxml2/libxslt；备份/恢复需要 **PostgreSQL 18 客户端**
# （`pg_dump -Fc` / `pg_restore`）。Debian trixie 自带的是 17，所以从 PGDG 装 18。
# **不装 sqlite3**：运行时已无 SQLite。
#
# ⚠ 包名是 **libxslt1.1**，不是 `libxslt1`：后者在 Debian 的源里**不存在**
# （trixie 的 main 索引里 `Package: libxslt1` 命中 0 条，`libxslt1.1` 才是）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libxml2 libxslt1.1 ca-certificates curl gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖再 COPY 源码：改代码时不必重装依赖（层缓存）。
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev

COPY src ./src
# **只 COPY 运行时脚本**：`scripts/migrate_sqlite_to_pg.py` 是迁移窗口的一次性 ETL，
# 绝不能进生产镜像（T9）。其余 bench/验收脚本同样不是运行时依赖。
COPY scripts/dev.py scripts/init_db.py scripts/migrate.py \
     scripts/maintenance_worker.py scripts/backup.sh scripts/restore_check.sh \
     ./scripts/
COPY migrations ./migrations

# 数据库地址由 compose 注入 `PMSTUDIO_DATABASE_URL`（PgBouncer）。
# 镜像里不写库路径：backend 不拥有 SQLite/文件库配置。
ENV PYTHONUNBUFFERED=1 \
    PMSTUDIO_PORT=8000

# 容器里必须 `--host 0.0.0.0`：`dev.py` 的默认是 `127.0.0.1`
# （那样容器外**连不上，而且没有日志**）。`--no-reload` 是必须的：
# 热重载会挂文件监听，且与多 worker 互斥。
# `--no-sync` 是必须的：`uv run` 默认每次起都全量 sync（镜像里 `.venv`
# 是 `--no-dev` 的，它会把 ruff/playwright 等 dev 包再下一遍——每次起多等
# 约一分钟，且启动依赖网关可达）。镜像构建时依赖已锁死，运行时不再 sync。
# 端口**不写在这里**：`dev.py` 读 `PMSTUDIO_PORT`（默认 8000），CMD 再写
# `--port 8000` 会让改环境变量失效（CLI 永远赢），两处迟早漂移。
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "python", "scripts/dev.py", "--host", "0.0.0.0", "--no-reload"]
