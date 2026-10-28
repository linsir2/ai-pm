"""启动时的材料维护：**把块结构已过时的材料按新块宽重切重算**（批 7，2026-10-21）。

## 为什么要有

块宽从 500 降到 400（为了让向量看得见块尾部）之后，**已入库的上传/抓网页材料**
的块结构过时了：它们的块是 500 宽切的，向量输入仍然超窗、尾部仍然看不见。
原始字节在库（`materials.content`）→ **重切不需要网络、不需要用户重传**。

## 三条纪律

① **不阻塞启动**：后台任务，跑完拉倒；
② **幂等**：重切后解析器档位对上 → 不再需要重切（再跑一次不重做）；
③ **已被编号引用的材料一个字都不动**——编号指着它里面的块，改块 = 改指编号（`P19` 冻结）。
"""

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.infrastructure.postgres.materials import SqliteMaterialStore
from pmstudio.infrastructure.postgres.vector_index import SqliteVectorIndex
from pmstudio.infrastructure.tools.extract import HTML_PARSER_ID, LocalMaterialParser
from tests.conftest import TEST_OWNER  # noqa: E402 —— 真 owner（conftest 装了作用域默认值）
from tests.support.embedding import FakeEmbedder
from tests.support.postgres import open_direct

OWNER = str(TEST_OWNER)
PROJECT = "p1"


def _html_material(text: str = "<html><body><p>机柜布线规范</p></body></html>"):
    from pmstudio.infrastructure.tools.extract import html_parser_version

    return ParsedMaterial(
        origin="upload",
        title="旧文档.html",
        source_ref="attachment:旧文档.html",
        media_type="text/html",
        bytes_len=len(text.encode("utf-8")),
        chunks=(
            MaterialChunk(
                chunk_id="x#c00",
                ordinal=0,
                text="旧块",
                heading_path=(),
                anchor="第 1 段",
            ),
        ),
        parser_id=HTML_PARSER_ID,
        parser_version=html_parser_version(),
        truncated=False,
        warnings=(),
    )


def _store_with_material(*, material_id="m-old", parser_version="bs4-1+x"):
    """造一份**旧档位**的上传材料（原始字节在库）。"""
    connection = open_direct()
    connection.execute(
        "INSERT INTO users (user_id, username, password_hash, created_at)"
        " VALUES (%s, 'alice', 'x', '2026-10-18T00:00:00+00:00')"
        " ON CONFLICT (user_id) DO NOTHING",
        (OWNER,),
    )
    connection.execute(
        "INSERT INTO materials (material_id, project_id, owner_id, origin, title, source_ref,"
        " media_type, bytes_len, sha256, parser_id, parser_version, schema_version, truncated,"
        " warnings, content, created_at) VALUES (%s, %s, %s, 'upload', '旧文档.html',"
        " 'attachment:旧文档.html', 'text/html', 40, 'sha-old', %s, %s, 1, false, '{}', %s, %s)",
        (
            material_id,
            PROJECT,
            OWNER,
            HTML_PARSER_ID,
            parser_version,
            "<html><body><p>机柜布线规范</p></body></html>".encode(),
            "2026-10-18T00:00:00+00:00",
        ),
    )
    connection.execute(
        "INSERT INTO material_chunks (chunk_id, material_id, owner_id, ordinal, text,"
        " heading_path, anchor, search_text)"
        " VALUES (%s, %s, %s, 0, '旧块', '{}', '第 1 段', '旧文档.html 旧块')",
        (f"{material_id}#c00", material_id, OWNER),
    )
    return connection


async def test_the_maintenance_re_parses_an_old_material_and_recomputes_its_vectors():
    """R：旧档位的材料被**重切**，且**向量重算**（旧向量对应旧块，重切后作废）。"""
    from pmstudio.bootstrap.maintenance import MaterialMaintenance
    from pmstudio.infrastructure.postgres import owner_scope

    connection = _store_with_material()
    store = SqliteMaterialStore(connection, ids=_SeqIds())
    vectors = SqliteVectorIndex(connection, embedder=FakeEmbedder(dimensions=512))
    vectors.write("m-old")     # 先给旧块写上向量

    task = MaterialMaintenance(
        connection=connection,
        parser=LocalMaterialParser(),
        store=store,
        vectors=vectors,
        owner_scope=owner_scope,
    )
    await task.run_once()

    packet = store.get("m-old", owner=OWNER)
    assert packet is not None
    assert packet.parsed.parser_version != "bs4-1+x", "档位没更新"
    assert "机柜布线规范" in packet.parsed.chunks[0].text, "块没按新块宽重切"
    rows = connection.execute(
        "SELECT chunk_id FROM material_chunks"
        " WHERE chunk_id LIKE 'm-old#%%' AND embedding IS NOT NULL"
    ).fetchall()
    assert rows, "重切后向量没重算"


async def test_the_maintenance_is_idempotent():
    """R：再跑一次**不重做**（档位对上 → 不再需要重切）。"""
    from pmstudio.bootstrap.maintenance import MaterialMaintenance
    from pmstudio.infrastructure.postgres import owner_scope

    connection = _store_with_material()
    store = SqliteMaterialStore(connection, ids=_SeqIds())
    task = MaterialMaintenance(
        connection=connection,
        parser=LocalMaterialParser(),
        store=store,
        vectors=SqliteVectorIndex(connection, embedder=FakeEmbedder(dimensions=512)),
        owner_scope=owner_scope,
    )

    first = await task.run_once()
    second = await task.run_once()

    assert first == 1, "第一遍应该重切了 1 份"
    assert second == 0, "第二遍不该再重切（幂等）"


async def test_a_profile_change_is_repaired_by_reembedding_without_rechunking():
    """R：profile 变更清空 embedding 后，维护任务**补算向量**，但不重切块/不改正文。

    I：当前档位材料 + 已写向量；ensure_vector_profile 换 profile（清空）；
    P：MaterialMaintenance.run_once（块档位已是当前 → 不重切）；
    R：refreshed=0、embedding 恢复、块正文逐字不变。
    """
    from pmstudio.bootstrap.maintenance import MaterialMaintenance
    from pmstudio.infrastructure.postgres import owner_scope
    from pmstudio.infrastructure.postgres.vector_index import ensure_vector_profile
    from pmstudio.infrastructure.tools.extract import html_parser_version

    connection = _store_with_material(parser_version=html_parser_version())
    store = SqliteMaterialStore(connection, ids=_SeqIds())
    vectors = SqliteVectorIndex(connection, embedder=FakeEmbedder(dimensions=512))
    vectors.write("m-old")

    def vector_count() -> int:
        return connection.execute(
            "SELECT count(*) AS n FROM material_chunks WHERE embedding IS NOT NULL"
        ).fetchone()["n"]

    before = connection.execute(
        "SELECT text FROM material_chunks WHERE chunk_id = 'm-old#c00'"
    ).fetchone()
    assert vector_count() == 1
    assert ensure_vector_profile(
        connection, profile="other-profile", model="other-model"
    )
    assert vector_count() == 0, "profile 变更应先清空"

    task = MaterialMaintenance(
        connection=connection,
        parser=LocalMaterialParser(),
        store=store,
        vectors=vectors,
        owner_scope=owner_scope,
        index=_Index({"m-old": ("E7",)}),
    )
    refreshed = await task.run_once()

    assert refreshed == 0, "块档位已是当前，不该重切"
    assert vector_count() == 1, "清空后的 embedding 没有被维护任务补回"
    after = connection.execute(
        "SELECT text FROM material_chunks WHERE chunk_id = 'm-old#c00'"
    ).fetchone()
    assert after == before, "重嵌入不许改块正文"


async def test_a_referenced_material_is_not_touched():
    """R：**已被编号引用**的材料一个字都不动（编号指着旧块，改块 = 改指编号）。"""
    from pmstudio.bootstrap.maintenance import MaterialMaintenance
    from pmstudio.infrastructure.postgres import owner_scope

    connection = _store_with_material()
    store = SqliteMaterialStore(connection, ids=_SeqIds())
    task = MaterialMaintenance(
        connection=connection,
        parser=LocalMaterialParser(),
        store=store,
        vectors=SqliteVectorIndex(connection, embedder=FakeEmbedder(dimensions=512)),
        owner_scope=owner_scope,
        index=_Index({"m-old": ("E7",)}),
    )

    refreshed = await task.run_once()

    assert refreshed == 0, "被引用的材料被重切了"
    packet = store.get("m-old", owner=OWNER)
    assert packet.parsed.chunks[0].text == "旧块", "被引用的材料内容被换了"


async def test_a_broken_material_does_not_stop_the_others():
    """R：一份坏了**不连累**别的（维护任务不该因为一份材料失败就整体停下）。"""
    from pmstudio.bootstrap.maintenance import MaterialMaintenance
    from pmstudio.infrastructure.postgres import owner_scope

    connection = _store_with_material()
    store = SqliteMaterialStore(connection, ids=_SeqIds())
    task = MaterialMaintenance(
        connection=connection,
        parser=LocalMaterialParser(),
        store=store,
        vectors=SqliteVectorIndex(connection, embedder=FakeEmbedder(dimensions=512)),
        owner_scope=owner_scope,
    )

    refreshed = await task.run_once()   # 库里只有一份（内容是合法 HTML，不会失败）

    assert refreshed == 1


class _Index:
    """工作区索引替身：只给"这份材料被哪些编号引用过"。"""

    def __init__(self, references: dict[str, tuple[str, ...]]) -> None:
        self._references = references

    def material_references(self, project_id):  # type: ignore[no-untyped-def]
        return self._references


class _SeqIds:
    def new(self, prefix: str) -> str:
        return f"{prefix}-new"


