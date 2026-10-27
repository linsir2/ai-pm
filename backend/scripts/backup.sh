#!/usr/bin/env bash
#
# 备份 PM Studio 的 PostgreSQL 库（custom format，PG18 客户端）。
#
# 用法:
#   scripts/backup.sh [备份目录]          # 做一份 dump
#   scripts/backup.sh --check             # RPO 自检：最新备份超过 N 小时就失败
# 不传时:
#   目录 = $PMSTUDIO_BACKUP_DIR（默认 <backend>/data/backups）
#   保留 = $PMSTUDIO_BACKUP_KEEP_DAYS（默认 7）+ $PMSTUDIO_BACKUP_KEEP_WEEKS（默认 4）
#   自检 = $PMSTUDIO_BACKUP_MAX_AGE_HOURS（默认 24）
#
# 纪律:
#   · 只认 `PMSTUDIO_DIRECT_DATABASE_URL`（owner 直连），**不读 app URL**；
#   · 用 PG18 的 `pg_dump -Fc`；版本不对直接失败（老客户端会静默丢掉新特性）；
#   · dump 落盘即 chmod 600；备份里有用户/会话/密码哈希，不进版本库/同步盘；
#   · 每日 03:00 跑一次 → RPO ≤ 24h；保留最近 7 份日备份 + 4 份周备份。
#   · `--check` 是交付出去的 RPO 自检：没有调度时也能在运维检查里发现"昨天没备"。
#
# cron 示例（容器外；备份与自检都交给 cron）:
#   0 3 * * * cd /srv/pmstudio/backend && PMSTUDIO_DIRECT_DATABASE_URL=... scripts/backup.sh
#   0 * * * * cd /srv/pmstudio/backend && scripts/backup.sh --check
#
# 恢复演练见 `scripts/restore_check.sh`（真 dump → scratch 库 → owner 指纹比对）。
set -euo pipefail

umask 077

mode="dump"
dest_arg=""
for arg in "$@"; do
  case "$arg" in
    --check) mode="check" ;;
    --*) echo "未知参数：$arg" >&2; exit 2 ;;
    *) dest_arg="$arg" ;;
  esac
done

BACKEND_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dest="${dest_arg:-${PMSTUDIO_BACKUP_DIR:-$BACKEND_ROOT/data/backups}}"
keep_days="${PMSTUDIO_BACKUP_KEEP_DAYS:-7}"
keep_weeks="${PMSTUDIO_BACKUP_KEEP_WEEKS:-4}"
max_age_hours="${PMSTUDIO_BACKUP_MAX_AGE_HOURS:-24}"

if [[ "$mode" == "check" ]]; then
  [[ "$max_age_hours" =~ ^[0-9]+$ ]] || {
    echo "PMSTUDIO_BACKUP_MAX_AGE_HOURS 要是整数，现在是：$max_age_hours" >&2
    exit 1
  }
  latest="$(ls -1 "$dest"/daily/pmstudio-*.dump 2>/dev/null | sort | tail -1 || true)"
  [[ -n "$latest" ]] || { echo "FAIL：没有找到任何日备份（$dest/daily）" >&2; exit 1; }
  modified="$(stat -c %Y "$latest")"
  now="$(date +%s)"
  age_hours="$(( (now - modified) / 3600 ))"
  if (( age_hours > max_age_hours )); then
    echo "FAIL：最新备份已 $age_hours 小时（> $max_age_hoursh）：$latest" >&2
    exit 1
  fi
  echo "PASS：最新备份 $age_hours 小时前（≤ ${max_age_hours}h）：$latest"
  exit 0
fi

command -v pg_dump >/dev/null 2>&1 || { echo "缺 pg_dump 18" >&2; exit 1; }
pg_dump_version="$(pg_dump --version)"
case "$pg_dump_version" in
  "pg_dump (PostgreSQL) 18."*) ;;
  *) echo "需要 PostgreSQL 18 的 pg_dump，现在是：$pg_dump_version" >&2; exit 1 ;;
esac

url="${PMSTUDIO_DIRECT_DATABASE_URL:-}"
[[ -n "$url" ]] || { echo "缺 PMSTUDIO_DIRECT_DATABASE_URL（backup 只走直连）" >&2; exit 1; }

mkdir -p "$dest/daily" "$dest/weekly"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="$dest/daily/pmstudio-$stamp.dump"

PGAPPNAME=pmstudio-backup pg_dump \
  --dbname="$url" \
  --format=custom \
  --no-owner \
  --no-password \
  --file="$out"
chmod 600 "$out"

# 每周留一份独立快照（同一 ISO 周只留第一份）。
week="$(date -u +%G%V)"
weekly="$dest/weekly/pmstudio-$week.dump"
if [[ ! -f "$weekly" ]]; then
  cp -p "$out" "$weekly"
  chmod 600 "$weekly"
fi

# 日备份保留最近 N 份；周备份保留最近 N 份（文件名按时间排序）。
mapfile -t dailies < <(ls -1 "$dest"/daily/pmstudio-*.dump 2>/dev/null | sort)
if (( ${#dailies[@]} > keep_days )); then
  rm -f "${dailies[@]:0:${#dailies[@]}-keep_days}"
fi
mapfile -t weeklies < <(ls -1 "$dest"/weekly/pmstudio-*.dump 2>/dev/null | sort)
if (( ${#weeklies[@]} > keep_weeks )); then
  rm -f "${weeklies[@]:0:${#weeklies[@]}-keep_weeks}"
fi

daily_count="$(ls -1 "$dest"/daily/pmstudio-*.dump 2>/dev/null | wc -l)"
weekly_count="$(ls -1 "$dest"/weekly/pmstudio-*.dump 2>/dev/null | wc -l)"
echo "备份完成: $out（$(du -h "$out" | cut -f1)），日备 $daily_count 份 / 周备 $weekly_count 份"
