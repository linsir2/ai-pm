#!/usr/bin/env bash
#
# 恢复演练（手册式 · 跑一次贴输出）。
#
# **真调用 `scripts/backup.sh`** 产出备份，再把它恢复到一个临时 scratch 库：
#   建 scratch（先 CREATE EXTENSION vector）→ pg_restore --exit-on-error --no-owner
#   → TRUNCATE sessions → 行数/指纹逐字比对。
# 全程只碰 ops URL（`PMSTUDIO_DIRECT_DATABASE_URL`）；scratch 用完即删。
#
# 恢复后 sessions 为空 = 旧 cookie 必然 401（端到端那条由
# `tests/test_backup_restore.py` 真验，不只靠这里打印）。
#
#   bash scripts/restore_check.sh
set -euo pipefail
umask 077

for command in psql pg_restore pg_dump; do
  command -v "$command" >/dev/null 2>&1 || { echo "缺 $command（需要 PG18 客户端）" >&2; exit 1; }
done
psql_version="$(psql --version)"
case "$psql_version" in
  "psql (PostgreSQL) 18."*) ;;
  *) echo "需要 PostgreSQL 18 的 psql，现在是：$psql_version" >&2; exit 1 ;;
esac

url="${PMSTUDIO_DIRECT_DATABASE_URL:-}"
[[ -n "$url" ]] || { echo "缺 PMSTUDIO_DIRECT_DATABASE_URL（restore 只走直连）" >&2; exit 1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # backend/
BACKUP="$ROOT/scripts/backup.sh"

WORK="$(mktemp -d)"
dest="$WORK/backups"
scratch="pmstudio_restore_$$_$(date -u +%H%M%S)"

# 从 ops URL 派生 maintenance / scratch URL（丢掉 query，避免把 search_path 带进新库）。
base="${url%%\?*}"
prefix="${base%/*}"
dbname="${base##*/}"
maint_url="${prefix}/postgres"
scratch_url="${prefix}/${scratch}"

cleanup() {
  psql "$maint_url" -v ON_ERROR_STOP=0 -c "DROP DATABASE IF EXISTS \"$scratch\" WITH (FORCE)" \
    >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

fingerprint() {
  psql "$1" -v ON_ERROR_STOP=1 -tA -F '|' -c "
    SELECT 'documents', count(*)::text FROM documents
    UNION ALL SELECT 'document_versions', count(*)::text FROM document_versions
    UNION ALL SELECT 'rounds', count(*)::text FROM rounds
    UNION ALL SELECT 'round_skips', count(*)::text FROM round_skips
    UNION ALL SELECT 'templates', count(*)::text FROM templates
    UNION ALL SELECT 'evidence_ledgers', count(*)::text FROM evidence_ledgers
    UNION ALL SELECT 'materials', count(*)::text FROM materials
    UNION ALL SELECT 'material_chunks', count(*)::text FROM material_chunks
    UNION ALL SELECT 'explorations', count(*)::text FROM explorations
    UNION ALL SELECT 'discussions', count(*)::text FROM discussions
    UNION ALL SELECT 'project_memory', count(*)::text FROM project_memory
    UNION ALL SELECT 'model_calls', count(*)::text FROM model_calls
    UNION ALL SELECT 'tool_calls', count(*)::text FROM tool_calls
    UNION ALL SELECT 'observations', count(*)::text FROM observations
    UNION ALL SELECT 'round_digests', count(*)::text FROM round_digests
    UNION ALL SELECT 'users', count(*)::text FROM users
    UNION ALL SELECT 'inspirations', count(*)::text FROM inspirations
    UNION ALL SELECT 'project_titles',
      coalesce(md5(string_agg(project_id || ':' || title, '|' ORDER BY project_id)), '')
      FROM documents;
  "
}

echo "== 1. 真调用 backup.sh（直连 ops URL，临时备份目录）=="
original="$(fingerprint "$url")"
echo "   原始指纹行数: $(wc -l <<< "$original")"
PMSTUDIO_DIRECT_DATABASE_URL="$url" PMSTUDIO_BACKUP_DIR="$dest" bash "$BACKUP"

dump="$(ls -1 "$dest"/daily/pmstudio-*.dump | sort | tail -1)"
perm="$(stat -c '%a' "$dump")"
echo "   dump: $dump（权限 $perm）"
[[ "$perm" == "600" ]] || { echo "FAIL：dump 权限不是 600" >&2; exit 1; }

echo "== 2. 建 scratch 库（先装 vector 扩展）=="
psql "$maint_url" -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"$scratch\"" >/dev/null
psql "$scratch_url" -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector" >/dev/null

echo "== 3. pg_restore --exit-on-error --no-owner =="
pg_restore --exit-on-error --no-owner --dbname="$scratch_url" "$dump" >/dev/null

echo "== 4. 恢复后 TRUNCATE sessions（旧 cookie 作废）=="
psql "$scratch_url" -v ON_ERROR_STOP=1 -c "TRUNCATE sessions" >/dev/null
sessions="$(psql "$scratch_url" -tA -c "SELECT count(*) FROM sessions")"
[[ "$sessions" == "0" ]] || { echo "FAIL：sessions 未清空：$sessions" >&2; exit 1; }

echo "== 5. 指纹逐字比对（sessions 除外）=="
restored="$(fingerprint "$scratch_url")"
if [[ "$restored" == "$original" ]]; then
  echo "PASS：dump 可恢复，行数/指纹一致，sessions 已清空（旧 cookie 必然 401）"
else
  echo "FAIL：恢复后指纹不一致" >&2
  diff <(printf '%s\n' "$original") <(printf '%s\n' "$restored") >&2 || true
  exit 1
fi
