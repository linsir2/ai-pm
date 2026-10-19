"""向量索引的存储面（检索阶段 2）：vec0 影子表 + 扩展加载降级 + 幂等回填。

与 FTS 回填同一体例（见 `test_sqlite_material_search_fts.py` 的同名用例）：
老库（有块无向量）跑 migrate → 补全；再跑不重复。

回填要一个 embedder：`migrate(conn)` 不带时只做形状迁移（离线安全）；
要补向量就显式传 —— 测试里传确定性假实现，真跑里容器传 `FastEmbedEmbedder`。
"""

import sqlite_vec

from pmstudio.infrastructure.sqlite import database
from tests.support.embedding import FakeEmbedder


def _blob(values):
    """用 sqlite-vec 自己的序列化造一行合法向量（这就是存进去的格式契约）。"""
    return sqlite_vec.serialize_float32(list(values))


def test_vector_table_exists_and_accepts_a_512_float_row(tmp_path):
    connection = database.connect(tmp_path / "t.db")
    connection.execute(
        "INSERT INTO material_chunks_vec (chunk_id, embedding) VALUES (?, ?)",
        ("c1", _blob([0.0] * 512)),
    )
    assert connection.execute("SELECT count(*) FROM material_chunks_vec").fetchone()[0] == 1
    connection.close()


def test_extension_is_loaded_or_reported_not_crashed(tmp_path):
    """R：扩展载不了也不许把 `connect()` 炸掉（降级不判死）。"""
    connection = database.connect(tmp_path / "t.db")
    assert isinstance(database.load_sqlite_vec(connection), bool)
    connection.close()


def test_backfill_fills_missing_vectors_idempotently(tmp_path):
    """R：老库（有块无向量）跑 migrate → 补全；再跑不重复。"""
    from tests.conftest import TEST_OWNER
    from tests.support.builders import PROJECT

    connection = database.connect(tmp_path / "old.db")
    # 绕过 store 直接塞块（模拟"升级前入库的旧数据"；store.put 此时还不写向量）。
    connection.execute(
        "INSERT INTO materials (material_id, project_id, owner_id, origin, title, source_ref,"
        " media_type, bytes_len, sha256, parser_id, parser_version, schema_version, truncated,"
        " warnings, content, created_at) VALUES ('m0', ?, ?, 'upload', 't', 'r', 'text/x',"
        " 1, 'sha', 'p', 'v', 1, 0, '[]', X'00', '2026-10-18T00:00:00+00:00')",
        (str(PROJECT), str(TEST_OWNER)),
    )
    for index, text in enumerate(["退货流程", "机柜布线规范"]):
        connection.execute(
            "INSERT INTO material_chunks (chunk_id, material_id, owner_id, ordinal, text,"
            " heading_path, anchor) VALUES (?, 'm0', ?, ?, ?, '[]', ?)",
            (f"m0#c{index:02d}", str(TEST_OWNER), index, text, f"第{index}段"),
        )

    database.migrate(connection, embedder=FakeEmbedder(dimensions=512))
    assert connection.execute("SELECT count(*) FROM material_chunks_vec").fetchone()[0] == 2

    database.migrate(connection, embedder=FakeEmbedder(dimensions=512))  # 幂等：再跑不重复
    assert connection.execute("SELECT count(*) FROM material_chunks_vec").fetchone()[0] == 2
    connection.close()
