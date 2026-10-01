"""材料检索（工具层 C-2c-1）：**块级命中** + 锚点 + 片段，`score` 就是展示顺序。

v1 用 SQLite `LIKE`：中文整段子串可靠、零索引、零迁移。
四条规矩：空查询回空（绝不"不填就是全量"）；`%` / `_` 按字面匹配；
只在这个项目里找（跨项目串味会让用户看到别家的东西）；到 `limit` 就停。
"""

import pytest

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.domain.shared.ids import ProjectId
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.materials import SqliteLikeRetriever, SqliteMaterialStore
from tests.support.builders import NOW, PROJECT
from tests.support.doubles import SeqIds

OTHER = ProjectId("p2")


def parsed(*texts: str, title: str = "诊断报告.pdf", origin: str = "upload") -> ParsedMaterial:
    return ParsedMaterial(
        origin=origin,
        title=title,
        source_ref=f"attachment:{title}",
        media_type="text/markdown",
        bytes_len=42,
        chunks=tuple(
            MaterialChunk(
                chunk_id=f"local-text#c{index:02d}",
                ordinal=index,
                text=text,
                heading_path=(),
                anchor=f"第 {index + 1} 行",
            )
            for index, text in enumerate(texts)
        ),
        parser_id="local-text",
        parser_version="v1",
    )


@pytest.fixture()
def world(tmp_path):
    """一个带材料的库：`store` 盖章、`retriever` 检索（两者共用同一个连接）。"""

    connection = database.connect(tmp_path / "pm.db")
    store = SqliteMaterialStore(connection, ids=SeqIds())

    def seed(*texts: str, title: str = "诊断报告.pdf", project_id: ProjectId = PROJECT) -> str:
        packet = store.put(
            project_id=project_id,
            parsed=parsed(*texts, title=title),
            sha256=f"{title}-{len(texts)}".ljust(64, "0"),
            content=b"raw",
            now=NOW,
        )
        return packet.material_id

    yield SqliteLikeRetriever(connection), seed
    connection.close()


def test_hits_are_chunk_level_with_anchor_and_excerpt(world):
    retriever, seed = world
    material_id = seed("结论：导出要走异步队列", "边界：只给管理员")

    hits = retriever.search(project_id=PROJECT, query="异步队列")

    assert [hit.chunk_id for hit in hits] == [f"{material_id}#c00"]
    assert hits[0].material_id == material_id
    assert hits[0].title == "诊断报告.pdf"
    assert hits[0].anchor == "第 1 行"
    assert "异步队列" in hits[0].excerpt


def test_the_excerpt_is_a_window_around_the_hit(world):
    """片段是**命中词前后一小段**，不是整块（列表里一行放不下整块）。"""

    retriever, seed = world
    seed("前" * 100 + "异步队列" + "后" * 100)

    hit = retriever.search(project_id=PROJECT, query="异步队列")[0]

    assert "异步队列" in hit.excerpt
    assert len(hit.excerpt) < 120


def test_more_hits_in_a_chunk_ranks_it_first(world):
    """`score` 的消费者就是展示顺序：命中次数多 → 位置靠前 → 块更短。"""

    retriever, seed = world
    seed("异步队列 与 异步队列：两次都提了", "这里只提了一次 异步队列")

    hits = retriever.search(project_id=PROJECT, query="异步队列")

    assert [hit.chunk_id for hit in hits] == [
        f"{hits[0].material_id}#c00",
        f"{hits[0].material_id}#c01",
    ]
    assert hits[0].score > hits[1].score        # 分数与顺序同向，不各说各的


def test_a_query_with_wildcards_is_matched_literally(world):
    """搜 `100%` 不许把 `%` 当通配符（`ESCAPE '\\'`）。"""

    retriever, seed = world
    seed("导出 100% 的行数上限是 5 万", "纯纯的导出说明")

    hits = retriever.search(project_id=PROJECT, query="100%")

    assert len(hits) == 1
    assert "100%" in hits[0].excerpt


def test_underscore_is_matched_literally_too(world):
    retriever, seed = world
    seed("字段名是 order_id", "另一个块")

    assert [hit for hit in retriever.search(project_id=PROJECT, query="order_id")] != []
    # 返回的是 tuple（不是 list）——`_` 按字面匹配，所以 orderXid 一条都不该有
    assert retriever.search(project_id=PROJECT, query="orderXid") == ()


def test_empty_query_returns_nothing(world):
    retriever, seed = world
    seed("随便什么内容")

    assert retriever.search(project_id=PROJECT, query="   ") == ()
    assert retriever.search(project_id=PROJECT, query="") == ()


def test_another_project_never_leaks_in(world):
    retriever, seed = world
    seed("我们的导出要走异步队列")
    seed("别家的导出也走异步队列", title="别家.pdf", project_id=OTHER)

    hits = retriever.search(project_id=PROJECT, query="异步队列")

    assert len(hits) == 1
    assert hits[0].title == "诊断报告.pdf"


def test_limit_is_honoured(world):
    retriever, seed = world
    seed(*[f"第 {index} 块都提到 异步队列" for index in range(8)])

    assert len(retriever.search(project_id=PROJECT, query="异步队列", limit=3)) == 3
