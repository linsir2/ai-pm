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


def test_connect_survives_a_missing_extension(tmp_path, monkeypatch):
    """R：扩展载不了时 `connect()` **不许炸**——没有 vec 模块时照样建出整库形状（降级到纯 FTS5）。

    这是本任务最硬的降级判据：加载失败过去会让 `executescript(schema.sql)` 撞
    「no such module: vec0」，整个后端起不来。vec 表改由代码建、建不成就跳过。
    """

    monkeypatch.setattr(database, "load_sqlite_vec", lambda _connection: False)

    connection = database.connect(tmp_path / "noext.db")   # 不抛即通过

    # 形状迁移照跑：非向量的表/列该在
    assert connection.execute("PRAGMA user_version").fetchone()[0] == database.USER_VERSION
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='materials'"
    ).fetchone() is not None
    connection.close()


def test_backfill_survives_a_broken_embedder(tmp_path):
    """R：回填时模型下载/推理炸了，**不许阻断启动**（老库升级最怕这条）。"""

    class Broken(FakeEmbedder):
        def embed_many(self, texts):
            raise RuntimeError("模型下载失败")

    connection = database.connect(tmp_path / "t.db")
    _seed_chunks_without_vectors(connection)

    database.migrate(connection, embedder=Broken())   # 不抛即通过

    assert connection.execute("SELECT count(*) FROM material_chunks_vec").fetchone()[0] == 0
    connection.close()


def test_first_connect_backfills_an_upgraded_database(tmp_path):
    """R：老库（有块、无 vec 表）**第一次** connect 就把向量补齐。

    迁移顺序要求：vec 表必须在回填之前就建好；否则首次升级永远补不上，
    要等第二次启动才补——这种"要跑两遍才对"的迁移等于坏了。
    """
    connection = database.connect(tmp_path / "old.db")
    _seed_chunks_without_vectors(connection)
    connection.execute("DROP TABLE material_chunks_vec")   # 模拟"升级前的老库"
    connection.close()

    reopened = database.connect(
        tmp_path / "old.db", embedder=FakeEmbedder(dimensions=512)
    )

    assert reopened.execute("SELECT count(*) FROM material_chunks_vec").fetchone()[0] == 2
    reopened.close()


def _seed_chunks_without_vectors(connection) -> None:
    """直接塞两份材料的块（绕过 store），且**不**写向量 —— 升级前的老库形状。"""
    from tests.conftest import TEST_OWNER
    from tests.support.builders import PROJECT

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
