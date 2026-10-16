#!/usr/bin/env bash
#
# 备份 PM Studio 的 SQLite 库。
#
# 用法:
#   scripts/backup.sh [库路径] [备份目录] [保留份数]
# 不传时:
#   库   = $PMSTUDIO_DB（相对路径按 backend/ 解析；再从 backend/.env 读；兜底 ~/.pmstudio/pmstudio.db）
#   目录 = <库目录>/backups
#   份数 = $PMSTUDIO_BACKUP_KEEP（默认 7）
#
# 为什么用 sqlite3 的 ".backup" 而**不是 cp**：
#   WAL 模式下最近的写入可能还在 -wal 文件里，`cp` 只拷主库文件会**丢掉整段数据**
#   （验证时真踩过：拷出来的库少了整个项目）。".backup" 走 SQLite 自己的在线备份 API，
#   拿到某一时刻的**一致快照**，与 -wal 合并后落成一个完整文件。
#
# 安全: 备份里含会话 token 哈希与密码哈希——落盘后立刻 chmod 600（只给属主读写），
#       别放进同步盘 / 版本库。下面已自动 chmod，换介质时请自觉保持这个权限。
set -euo pipefail

command -v sqlite3 >/dev/null 2>&1 || { echo "缺 sqlite3 命令" >&2; exit 1; }

BACKEND_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

env_db=""
if [[ -f "$BACKEND_ROOT/.env" ]]; then
  env_db="$(sed -n "s/^[[:space:]]*PMSTUDIO_DB[[:space:]]*=[[:space:]]*//p" "$BACKEND_ROOT/.env" | tail -n1)"
  env_db="${env_db%\"}"; env_db="${env_db#\"}"
  env_db="${env_db%\'}"; env_db="${env_db#\'}"
fi
db="${1:-${PMSTUDIO_DB:-${env_db:-$HOME/.pmstudio/pmstudio.db}}}"
[[ "$db" = /* ]] || db="$BACKEND_ROOT/$db"

dest="${2:-${PMSTUDIO_BACKUP_DIR:-$(dirname "$db")/backups}}"
keep="${3:-${PMSTUDIO_BACKUP_KEEP:-7}}"

[[ -f "$db" ]] || { echo "库不存在: $db" >&2; exit 1; }
mkdir -p "$dest"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="$dest/pmstudio-$stamp.db"

# 在线一致快照 + 立刻收权限
sqlite3 "$db" ".backup '$out'"
chmod 600 "$out"

# 留最近 keep 份（文件名带 UTC 时间戳，按名排序即按时间）
mapfile -t all < <(ls -1 "$dest"/pmstudio-*.db 2>/dev/null | sort)
if (( ${#all[@]} > keep )); then
  for old in "${all[@]:0:${#all[@]}-keep}"; do
    rm -f "$old"
  done
fi

echo "备份完成: $out（$(du -h "$out" | cut -f1)），保留最近 $keep 份"
