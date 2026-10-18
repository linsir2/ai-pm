"""三层水位遥测：纯函数 + 只读扫库（RIPR：可达 / 感染 / 传播 / 可揭示）。

口径：`memory_watermarks(counts)` 只做一件事——把"每层多少行"翻译成
"有没有超该层水位"。水位数字**不在本脚本里定义**，从
`application.context` 的 `L*_CAPACITY` import 过来：提示词尾注与遥测必须
说同一条线。

**超水位不是删除。** 水位是"该去翻旧账"的提醒（提示词里照实说"更早的
待复核"），删不删只有用户能触发——所以遥测**只读**，扫库不开写。
"""

import sqlite3

from scripts.telemetry import memory_watermarks, scan_memory_layers

from pmstudio.application.context import L2_CAPACITY, L3_CAPACITY, L4_CAPACITY
from pmstudio.infrastructure.sqlite import database


def test_watermark_flags_only_layers_over_capacity():
    """R：46 / 0 / 0 三层都远未到水位（真库 2026-10-18 观测值），不许报超。"""
    rows = memory_watermarks({"L2": 46, "L3": 0, "L4": 0})
    assert rows["L2"].over is False
    assert rows["L3"].over is False
    assert rows["L4"].over is False


def test_watermark_flags_the_layer_that_is_past_its_capacity():
    """R：超水位的那层必须报 True——超了要有人看见，否则等于没有水位。"""
    rows = memory_watermarks({"L2": L2_CAPACITY + 1, "L3": 0, "L4": 0})
    assert rows["L2"].over is True
    assert rows["L3"].over is False


def test_exactly_at_capacity_is_not_over():
    """I：判据是 `>` 不是 `>=`——与渲染层尾注（`len(...) > CAPACITY`）同一判据，
    两边差一位就会"表里说有 201 条、提示词里一句多余的话都没提"。"""
    rows = memory_watermarks({"L2": L2_CAPACITY, "L3": L3_CAPACITY, "L4": L4_CAPACITY})
    assert all(row.over is False for row in rows.values())


def test_capacity_is_the_production_constant_not_a_local_copy():
    """P：脚本里另抄一份数字，两边各说各话。判据只认 context 的常量。"""
    rows = memory_watermarks({"L2": 0, "L3": 0, "L4": 0})
    assert rows["L2"].capacity == L2_CAPACITY
    assert rows["L3"].capacity == L3_CAPACITY
    assert rows["L4"].capacity == L4_CAPACITY


def test_scan_memory_layers_counts_rows_and_finds_the_oldest(tmp_path):
    """R：真读三层表——层名↔表名配错（最可能的错法）当场就红；
    最旧 `created_at` 是"该从哪开始翻"的落点，空表 / 无时间戳都报 `''`。"""
    db = tmp_path / "wm.db"
    connection = database.connect(db)
    connection.executemany(
        "INSERT INTO observations (id, project_id, round_id, obs_type, title,"
        " detail, source_ref, created_at) VALUES (?, 'p1', 'r1', 'risk', ?, '', '', ?)",
        [
            ("obs-1", "新行", "2026-10-18T00:00:00"),
            ("obs-2", "老行", "2026-10-01T00:00:00"),
        ],
    )
    connection.execute(
        "INSERT INTO inspirations (id, project_id, origin, statement, created_at)"
        " VALUES ('insp-1', 'p1', 'discussion', '一个想法', '2026-10-02T00:00:00')"
    )
    connection.commit()
    connection.close()

    marks = scan_memory_layers(db)

    assert (marks["L2"].count, marks["L2"].oldest) == (2, "2026-10-01T00:00:00")
    assert (marks["L3"].count, marks["L3"].oldest) == (1, "2026-10-02T00:00:00")
    assert (marks["L4"].count, marks["L4"].oldest) == (0, "")


def test_scan_skips_layers_whose_table_is_absent(tmp_path):
    """I：老库 / 半截库缺表时抛异常会让整次扫描白跑——跳过即可（同本脚本既有防守）。"""
    db = tmp_path / "bare.db"
    sqlite3.connect(db).close()  # 空库：三层表一张都没有

    assert scan_memory_layers(db) == {}


def test_scan_skips_only_the_layer_whose_created_at_column_is_missing(tmp_path):
    """I：迁移前的库——表在、`created_at` 列不在（真库 2026-10-18 的实际形状）。
    炸掉整次扫描就等于连"还剩几行"都报不出来；把"查不出"说成"没有时间"则是撒谎。
    行数照报，时间戳老实说查不出。"""
    db = tmp_path / "pre-v14.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE observations (id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO observations VALUES ('obs-1')")
    connection.execute(
        "CREATE TABLE inspirations (id TEXT PRIMARY KEY, created_at TEXT NOT NULL)"
    )
    connection.execute("INSERT INTO inspirations VALUES ('insp-1', '2026-10-02')")
    connection.commit()
    connection.close()

    marks = scan_memory_layers(db)

    assert (marks["L2"].count, marks["L2"].oldest) == (1, None)
    assert (marks["L3"].count, marks["L3"].oldest) == (1, "2026-10-02")
