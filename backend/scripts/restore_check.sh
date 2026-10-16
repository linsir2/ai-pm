#!/usr/bin/env bash
#
# 恢复演练（手册式 · 跑一次贴输出）。
#
# 证明 `backup.sh` 用 ".backup" 拍出的快照能**原样**恢复：备份 → 改坏 → 恢复 → 逐项一致。
# 全程在临时目录里对**副本**操作，绝不碰真库；结束自动清理。
#
#   bash scripts/restore_check.sh
set -euo pipefail

command -v sqlite3 >/dev/null 2>&1 || { echo "缺 sqlite3 命令" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
live="$WORK/live.db"
snapshot="$WORK/snapshot.db"

# 指纹：项目数 / 材料数 / 项目标题（按 id 排序，结果确定）
fingerprint() {
  sqlite3 "$1" "
    SELECT (SELECT count(*) FROM projects) || ' / '
         || (SELECT count(*) FROM materials) || ' / '
         || (SELECT group_concat(title, '|') FROM
               (SELECT title FROM projects ORDER BY project_id));"
}

echo "== 1. 造一个有已知数据的库（模拟运行中的 PM Studio 库）=="
sqlite3 "$live" <<'SQL'
CREATE TABLE projects(project_id TEXT PRIMARY KEY, title TEXT, owner_id TEXT);
CREATE TABLE materials(material_id TEXT PRIMARY KEY, project_id TEXT, body TEXT);
INSERT INTO projects  VALUES ('p1','订单中台','u-alice'), ('p2','风控','u-bob');
INSERT INTO materials VALUES ('m1','p1','需求初稿'),   ('m2','p2','访谈记录');
SQL
original="$(fingerprint "$live")"
echo "   原始指纹(项目数 / 材料数 / 项目标题): $original"

echo "== 2. 用 .backup 拍一致快照（等价 backup.sh 的那一步），并 chmod 600 =="
sqlite3 "$live" ".backup '$snapshot'"
chmod 600 "$snapshot"
echo "   快照: $snapshot"

echo "== 3. 制造事故：删掉 p1 的材料 + 塞一条脏项目（模拟误删 / 被写坏）=="
sqlite3 "$live" "DELETE FROM materials WHERE project_id='p1';
                 INSERT INTO projects VALUES ('p-evil','脏数据','u-attacker');"
echo "   事故后指纹: $(fingerprint "$live")"

echo "== 4. 从快照恢复（把备份内容写回库文件；生产里先停服务再恢复）=="
sqlite3 "$snapshot" ".backup '$live'"

echo "== 5. 校验：恢复后指纹必须与原始**逐字一致** =="
restored="$(fingerprint "$live")"
echo "   恢复后指纹: $restored"
if [[ "$restored" == "$original" ]]; then
  echo "PASS：备份/恢复一致——脏数据消失，误删的材料回来了，权限 600 的快照可用作恢复源"
else
  echo "FAIL：恢复后与原始不一致（原始=$original，恢复=$restored）" >&2
  exit 1
fi
