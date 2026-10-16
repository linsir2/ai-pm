#!/usr/bin/env bash
#
# 恢复演练（手册式 · 跑一次贴输出）。
#
# **真调用 `scripts/backup.sh`** 产出备份（用它自己的 env 用法：PMSTUDIO_DB /
# PMSTUDIO_BACKUP_DIR / PMSTUDIO_BACKUP_KEEP），再拿它产出的快照做"改坏 → 恢复 →
# 指纹逐字比对"；顺带把 backup.sh 的**保留份数**与 **chmod 600** 两条接线一起验掉。
# 全程在临时目录里对副本操作，绝不碰真库；结束自动清理。
#
#   bash scripts/restore_check.sh
set -euo pipefail

command -v sqlite3 >/dev/null 2>&1 || { echo "缺 sqlite3 命令" >&2; exit 1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # backend/
BACKUP="$ROOT/scripts/backup.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
live="$WORK/live.db"
dest="$WORK/backups"

# 指纹：项目数 / 材料数 / 项目标题（按 id 排序，结果确定）
fingerprint() {
  sqlite3 "$1" "
    SELECT (SELECT count(*) FROM projects) || ' / '
         || (SELECT count(*) FROM materials) || ' / '
         || (SELECT group_concat(title, '|') FROM
               (SELECT title FROM projects ORDER BY project_id));"
}

echo "== 1. 造一个有已知数据的库（WAL，模拟运行中的 PM Studio 库）=="
sqlite3 "$live" "PRAGMA journal_mode=WAL;" >/dev/null
sqlite3 "$live" <<'SQL'
CREATE TABLE projects(project_id TEXT PRIMARY KEY, title TEXT, owner_id TEXT);
CREATE TABLE materials(material_id TEXT PRIMARY KEY, project_id TEXT, body TEXT);
INSERT INTO projects  VALUES ('p1','订单中台','u-alice'), ('p2','风控','u-bob');
INSERT INTO materials VALUES ('m1','p1','需求初稿'),   ('m2','p2','访谈记录');
SQL
original="$(fingerprint "$live")"
echo "   原始指纹(项目数 / 材料数 / 项目标题): $original"

echo "== 2. 真调用 backup.sh（env 用法：PMSTUDIO_DB / PMSTUDIO_BACKUP_DIR / PMSTUDIO_BACKUP_KEEP=2）=="
# 预埋 3 份旧备份：跑完应只剩 keep=2 份（backup.sh 产出的真快照 + 1 份最新旧备份），验保留接线
mkdir -p "$dest"
for i in 01 02 03; do echo x > "$dest/pmstudio-2020010${i}T000000Z.db"; done
PMSTUDIO_DB="$live" PMSTUDIO_BACKUP_DIR="$dest" PMSTUDIO_BACKUP_KEEP=2 bash "$BACKUP"

count="$(ls -1 "$dest"/pmstudio-*.db | wc -l)"
echo "   备份目录现有 $count 份（keep=2）"
[[ "$count" -eq 2 ]] || { echo "FAIL：保留份数不是 2（backup.sh 的保留接线没生效）" >&2; exit 1; }

snapshot="$(ls -1 "$dest"/pmstudio-*.db | sort | tail -1)"   # 最新 = backup.sh 真产出的那份
perm="$(stat -c '%a' "$snapshot")"
echo "   backup.sh 产出的快照: $snapshot（权限 $perm）"
[[ "$perm" == "600" ]] || { echo "FAIL：快照权限不是 600（backup.sh 的 chmod 没生效）" >&2; exit 1; }
[[ "$(fingerprint "$snapshot")" == "$original" ]] || { echo "FAIL：backup.sh 产出的快照内容对不上" >&2; exit 1; }

echo "== 3. 制造事故：删掉 p1 的材料 + 塞一条脏项目（模拟误删 / 被写坏）=="
sqlite3 "$live" "DELETE FROM materials WHERE project_id='p1';
                 INSERT INTO projects VALUES ('p-evil','脏数据','u-attacker');"
echo "   事故后指纹: $(fingerprint "$live")"

echo "== 4. 从 backup.sh 的快照恢复（写回库文件；生产里先停服务再恢复）=="
sqlite3 "$snapshot" ".backup '$live'"

echo "== 5. 校验：恢复后指纹必须与原始**逐字一致** =="
restored="$(fingerprint "$live")"
echo "   恢复后指纹: $restored"
if [[ "$restored" == "$original" ]]; then
  echo "PASS：backup.sh 产出的 600 快照可作恢复源——脏数据消失，误删材料回来，保留份数正确"
else
  echo "FAIL：恢复后与原始不一致（原始=$original，恢复=$restored）" >&2
  exit 1
fi
