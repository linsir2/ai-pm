#!/usr/bin/env bash
# 没有 Docker 时，用原生进程复现 compose 的启动序列。
#
# **它证明什么**：compose 里写的环境变量、端口、代理目标、启动顺序是**对的**
# ——因为原生跑通了。**它不证明**镜像本身能构建（那要 Docker）。
#
# **它与 compose 用同一套环境变量名**——那是两者"等价"的根据，
# `tests/architecture/test_container_assets.py::test_the_dev_stack_script_uses_the_same_env_keys_as_compose`
# 盯着这件事。
#
# 用法：
#   scripts/dev_stack.sh            # 起（前台等 Ctrl-C）
#   scripts/dev_stack.sh --stop     # 收
#   scripts/dev_stack.sh --check    # 只检查配置一致性，不起

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

#: 与 `docker-compose.yml` 一致的端口。
BACKEND_PORT="${PMSTUDIO_PORT:-8000}"
FRONTEND_PORT="${PMSTUDIO_WEB_PORT:-5173}"

# ── 与 compose **完全相同**的环境变量（键名与取值口径一致）─────────────
# 这一段是"compose 与本脚本等价"的**可核对凭据**：
# `tests/architecture/test_container_assets.py` 盯着两边的键名不许漂。
# PostgreSQL 的 app/direct URL 从 backend/.env 与 backend/.env.ops 读
# （已导出的环境变量优先）；backend 只拿 app URL，init/maintenance 用 direct。
load_env_file() {
  local file="$1"
  [ -f "$file" ] || return 0
  while IFS= read -r line; do
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    local key="${line%%=*}"
    local value="${line#*=}"
    key="${key%"${key##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value%\"}"; value="${value#\"}"
    value="${value%\'}"; value="${value#\'}"
    if [ -n "$key" ] && [ -z "${!key+x}" ]; then
      export "$key=$value"
    fi
  done < "$file"
}

load_env_file "$BACKEND/.env"
load_env_file "$BACKEND/.env.ops"

export PMSTUDIO_PORT="$BACKEND_PORT"
export PMSTUDIO_WEB_PORT="$FRONTEND_PORT"
# 代理目标**带协议前缀**：vite 的 proxy target 与 `frontend/.env` 的
# `PMSTUDIO_API_TARGET` 要同口径（不带 `http://` 时部分版本会被当成路径）。
export PMSTUDIO_API_TARGET="${PMSTUDIO_API_TARGET:-http://127.0.0.1:$BACKEND_PORT}"
# **不导出 `PMSTUDIO_WEB_ROOT`**：那是 compose 的 Caddy 用的静态根目录；
# 本机跑的是 vite dev server，它自己伺服，没有消费者（导出了就是死变量）。
# 同机反代：源 IP 就是本机，信任 127.0.0.1 即可（**无条件信任转发头
# 等于让任何人伪造源 IP**，那限流就形同虚设）。
export PMSTUDIO_FORWARDED_ALLOW_IPS="${PMSTUDIO_FORWARDED_ALLOW_IPS:-127.0.0.1}"
# Redis：留空 = 只用进程内计数（单机单 worker 够用）。
export PMSTUDIO_REDIS_URL="${PMSTUDIO_REDIS_URL:-}"

log() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m!!!\033[0m %s\n' "$*" >&2; exit 1; }

# ── 配置一致性检查：compose 与本脚本的键名必须一致 ──
check_keys() {
  local compose="$ROOT/docker-compose.yml"
  [ -f "$compose" ] || die "找不到 docker-compose.yml"
  # 只比 compose **environment 段**里的键：`env_file` 段里的那些
  # （PMSTUDIO_MODEL / PMSTUDIO_API_KEY …）由 backend/.env 提供，
  # 两边读的是**同一个文件**，不必在本脚本里重写一遍。
  # **两个服务的 environment 段都要**（前端有 PMSTUDIO_API_TARGET / PMSTUDIO_WEB_ROOT）。
  local compose_keys
  compose_keys="$(grep -oE '^[[:space:]]+(PMSTUDIO|FASTEMBED|PUBLIC)_[A-Z_]+:' "$compose" \
    | grep -oE '(PMSTUDIO|FASTEMBED|PUBLIC)_[A-Z_]+' | sort -u)"
  # 脚本侧只认**真的被赋值**的键（注释里提到不算——提到是解释，赋值才是事实）。
  local script_keys
  script_keys="$(grep -oE 'PMSTUDIO_[A-Z_]+[[:space:]]*=' "${BASH_SOURCE[0]}" \
    | sed 's/[[:space:]]*=$//' | sort -u)"
  # compose 独有、脚本**故意**没有的键：
  #   · PMSTUDIO_WEB_ROOT  —— Caddy 的静态根；本机跑 vite，自己伺服。
  #   · FASTEMBED_CACHE_PATH —— compose 要指到挂卷目录；本机用 fastembed 默认缓存。
  #   · PUBLIC_DOMAIN —— compose 的 Caddy 站点地址；本机没有 Caddy。
  #   · PMSTUDIO_PGBOUNCER_AUTH_FILE/UID —— compose 的 init 写共享卷用；本机
  #     PgBouncer 的配置由部署者自己给。
  local compose_only="PMSTUDIO_WEB_ROOT FASTEMBED_CACHE_PATH PUBLIC_DOMAIN PMSTUDIO_PGBOUNCER_AUTH_FILE PMSTUDIO_PGBOUNCER_AUTH_UID"
  local missing=""
  while read -r key; do
    [ -z "$key" ] && continue
    case " $compose_only " in *" $key "*) continue ;; esac
    grep -qxF "$key" <<< "$script_keys" || missing="$missing $key"
  done <<< "$compose_keys"
  if [ -n "$missing" ]; then
    die "compose 里的这些键 dev_stack.sh 没覆盖：$missing"
  fi
  log "配置键名一致（$(echo "$compose_keys" | grep -c . ) 个 PMSTUDIO_* 键）"
}

check_db_env() {
  [ -n "${PMSTUDIO_DATABASE_URL:-}" ] || die "缺 PMSTUDIO_DATABASE_URL（backend 只认 app URL）"
  [ -n "${PMSTUDIO_DIRECT_DATABASE_URL:-}" ] || die "缺 PMSTUDIO_DIRECT_DATABASE_URL（init/maintenance/backup 直连）"
  case "$PMSTUDIO_DATABASE_URL" in
    *pgbouncer*|*:6432*) log "backend app URL：PgBouncer（transaction mode）" ;;
    *) log "backend app URL：直连（本机可以，生产必须经 PgBouncer）" ;;
  esac
}

wait_for() {
  local url="$1" name="$2" tries="${3:-40}"
  for _ in $(seq 1 "$tries"); do
    if curl -fsS -o /dev/null -m 3 "$url"; then
      log "$name 就绪：$url"
      return 0
    fi
    sleep 1
  done
  die "$name 没起来：$url（看上面的日志）"
}

stop_all() {
  log "收尾…"
  [ -f /tmp/pmstudio-devstack.pids ] && while read -r pid; do
    kill "$pid" 2>/dev/null && log "停掉 $pid"
  done < /tmp/pmstudio-devstack.pids
  rm -f /tmp/pmstudio-devstack.pids
  log "收干净了"
}

case "${1:-start}" in
  --check)
    check_keys
    check_db_env
    exit 0
    ;;
  --stop)
    stop_all
    exit 0
    ;;
esac

check_keys
check_db_env

[ -f "$BACKEND/.env" ] || die "没有 backend/.env —— 先 cp backend/.env.example backend/.env 并填 key"
command -v uv >/dev/null || die "没有 uv"
[ -d "$FRONTEND/node_modules" ] || die "前端依赖没装 —— 先 cd frontend && npm install"

stop_all
: > /tmp/pmstudio-devstack.pids

# compose 的启动序列里 init 是**独立的一次性 job**；本机有 bootstrap URL 时
# 复现同一段（幂等），没有就要求库已初始化。
if [ -n "${PMSTUDIO_BOOTSTRAP_DATABASE_URL:-}" ]; then
  log "初始化数据库（init_db.py --all，bootstrap→schema→finalize）"
  ( cd "$BACKEND" && ./.venv/bin/python scripts/init_db.py --all ) || die "init_db --all 失败"
else
  log "跳过 init：没有 PMSTUDIO_BOOTSTRAP_DATABASE_URL（假定库已由 init job 初始化）"
fi

log "起后端（端口 $BACKEND_PORT）"
(
  cd "$BACKEND"
  # 与 Dockerfile 的 CMD 一致：`--host 0.0.0.0` 在容器里是必须的；
  # 本机用 127.0.0.1 就够（且更安全）。
  exec ./.venv/bin/python scripts/dev.py --host 127.0.0.1 --port "$BACKEND_PORT" --no-reload
) > /tmp/pmstudio-backend.log 2>&1 &
echo $! >> /tmp/pmstudio-devstack.pids

wait_for "http://127.0.0.1:$BACKEND_PORT/api/ready" "后端（就绪）"

log "起前端（端口 $FRONTEND_PORT）"
(
  cd "$FRONTEND"
  exec npx vite --host 127.0.0.1 --port "$FRONTEND_PORT"
) > /tmp/pmstudio-frontend.log 2>&1 &
echo $! >> /tmp/pmstudio-devstack.pids

wait_for "http://127.0.0.1:$FRONTEND_PORT/" "前端"

cat <<EOF

两个服务都起来了：
  前端   http://127.0.0.1:$FRONTEND_PORT
  后端   http://127.0.0.1:$BACKEND_PORT/api/health（存活）
         http://127.0.0.1:$BACKEND_PORT/api/ready（就绪）
  日志   /tmp/pmstudio-backend.log · /tmp/pmstudio-frontend.log

收尾：scripts/dev_stack.sh --stop
EOF

wait
