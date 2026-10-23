# PM Studio 后端镜像。
#
# **为什么是 `python:3.13-slim` 而不是 alpine**：`fastembed` 依赖
# `onnxruntime`，它要 glibc，alpine（musl）装不上 → 构建失败或静默不可用。
#
# **为什么整树 COPY `src/` 而不是只 COPY `.py`**：提示词（`.md`）、工具与席位
# 声明（`.toml`）、模板、`schema.sql` 都是**运行时读的资源**。漏 COPY 的症状是
# **静默的**——`Template("").substitute()` 是合法的，于是每一轮都拿着空提示词
# 去调模型而**不报错**（`tests/architecture/test_resource_paths.py` 点名过这个坑）。

FROM python:3.13-slim

# lxml 需要 libxml2/libxslt；sqlite3 CLI 是 backup.sh / restore_check.sh 用的
# （用 `sqlite3 .backup` 而不是 `cp`——WAL 模式下 `cp` 会丢 `-wal` 里的数据）。
#
# ⚠ 包名是 **libxslt1.1**，不是 `libxslt1`：后者在 Debian 的源里**不存在**
# （trixie 的 main 索引里 `Package: libxslt1` 命中 0 条，`libxslt1.1` 才是）。
# 2026-10-23 CI 实测：`E: Unable to locate package libxslt1` → 镜像构建直接红。
# 基础镜像 `python:3.13-slim` 是**浮动标签**，它会随 Debian 发版漂移，
# 所以这里不钉 bookworm/trixie——包名在两者上一致（都是 `libxslt1.1`）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libxml2 libxslt1.1 sqlite3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖再 COPY 源码：改代码时不必重装依赖（层缓存）。
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev

COPY src ./src
COPY scripts ./scripts

# 库放**绝对路径**并挂卷：相对路径按 `backend/` 解析（`scripts/dev.py`），
# 容器里 WORKDIR 不同会落到别处。
ENV PYTHONUNBUFFERED=1 \
    PMSTUDIO_DB=/data/pmstudio.db \
    PMSTUDIO_PORT=8000

# 容器里必须 `--host 0.0.0.0`：`dev.py` 的默认是 `127.0.0.1`
# （那样容器外**连不上，而且没有日志**）。`--no-reload` 是必须的：
# 热重载会挂文件监听，且与多 worker 互斥。
EXPOSE 8000
CMD ["uv", "run", "python", "scripts/dev.py", "--host", "0.0.0.0", "--port", "8000", "--no-reload"]
