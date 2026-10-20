"""向量写回接用例（批 5/5）：主事务提交后落向量，删除/重解析时清向量。

打的是**真 SQLite 仓 + 真实用例**，不是内存 harness——本批最关键的契约
"推理不许在写事务里"只有真连接才能证（`connection.in_transaction` 是真值）。

三条判据：
- 上传提交后向量已落库（数量 == 块数）；
- 删除后向量随块一起清干净（不留下指向已删块的孤儿向量）；
- 重解析就地换块后向量被重算（不是留着旧块的向量）。
"""

from datetime import UTC

import pytest

from pmstudio.application.use_cases import DeleteMaterial, ReparseMaterial, UploadMaterial
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.materials import SqliteMaterialStore
from pmstudio.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
from tests.conftest import TEST_OWNER
from tests.support.builders import PROJECT
from tests.support.doubles import SeqIds
from tests.support.embedding import FakeEmbedder

DOC = "# 退货\n用户的退货流程与时限".encode()


@pytest.fixture
def wired(tmp_path):
    """真连接 + 真材料仓 + 真向量索引 + 真 UoW，四个用例都从这套装配。"""
    connection = database.connect(tmp_path / "t.db")
    connection.execute(
        "INSERT OR IGNORE INTO users (user_id, password_hash, created_at)"
        " VALUES (?, 'x', '2026-10-18T00:00:00+00:00')",
        (str(TEST_OWNER),),
    )
    store = SqliteMaterialStore(connection, ids=SeqIds())
    vectors = SqliteVectorIndex(connection, embedder=FakeEmbedder(dimensions=512))
    uow = SqliteUnitOfWork(connection)
    yield connection, store, vectors, uow
    connection.close()


def _upload(connection, store, vectors, uow, data=DOC, name="ret.md", media="text/markdown"):
    async def run():
        return await UploadMaterial(
            documents=_Docs(),
            materials=store,
            parser=_Parser(),
            clock=_Clock(),
            uow=uow,
            vectors=vectors,
        )(project_id=PROJECT, filename=name, media_type=media, data=data)

    import asyncio

    return asyncio.run(run())


def _vec_count(connection) -> int:
    return connection.execute("SELECT count(*) FROM material_chunks_vec").fetchone()[0]


def test_upload_writes_vectors_after_the_main_transaction_commits(wired):
    """R：上传返回后向量已在库里，且条数与块数一致（不是 0，也不是重复写）。"""
    connection, store, vectors, uow = wired

    saved = _upload(connection, store, vectors, uow)

    assert _vec_count(connection) == saved.chunk_count == 1
    assert not connection.in_transaction          # 提交干净，推理不在事务里


def test_inference_never_runs_inside_the_uploads_write_transaction(wired):
    """**本批最关键的一条**：推理时连接上**没有**活动写事务。

    推理 ~20ms/块，在写事务里做等于把写锁时间拉长一个数量级，单用户小库
    也会被自己的长事务卡住。这是 c′ 方案存在的全部理由，必须在**用例层**
    证明（仓储层那条证明不了真实装配的调用顺序）。
    """
    connection, store, vectors, uow = wired
    seen: list[bool] = []

    class Recorder(FakeEmbedder):
        def embed_many(self, texts):
            seen.append(connection.in_transaction)
            return super().embed_many(texts)

    recorder_index = SqliteVectorIndex(connection, embedder=Recorder(dimensions=512))
    _upload(connection, store, recorder_index, uow)

    assert seen, "向量一条都没算（测试没有鉴别力）"
    assert not any(seen), "推理时连接上有活动写事务——写锁被推理时长占住了"


def test_deleting_a_material_takes_its_vectors_with_it(wired):
    """R：删材料后向量表里不能留下指向已删块的孤儿（vec 表是影子表，没有 CASCADE 兜底）。"""
    connection, store, vectors, uow = wired
    saved = _upload(connection, store, vectors, uow)
    assert _vec_count(connection) == 1

    async def run():
        return await DeleteMaterial(
            materials=store,
            parser=_Parser(),
            index=_Index(),
            uow=uow,
            vectors=vectors,
        )(project_id=PROJECT, material_id=saved.material_id)

    import asyncio

    asyncio.run(run())

    assert _vec_count(connection) == 0
    assert connection.execute("SELECT count(*) FROM material_chunks").fetchone()[0] == 0


def test_reparsing_recomputes_vectors_instead_of_leaving_stale_ones(wired):
    """R：就地重解析换了块，旧向量必须被清掉重算——否则检索命中的是已经不存在的内容。"""
    connection, store, vectors, uow = wired
    saved = _upload(connection, store, vectors, uow)
    original = {
        row["chunk_id"]: row["embedding"]
        for row in connection.execute("SELECT chunk_id, embedding FROM material_chunks_vec")
    }

    # 换一个解析器（不同 parser_id），产出内容不同的块
    new_parser = _Parser(variant=True)

    async def run():
        return await ReparseMaterial(
            materials=store,
            parser=new_parser,
            index=_Index(),
            uow=uow,
            clock=_Clock(),
            vectors=vectors,
        )(project_id=PROJECT, material_id=saved.material_id)

    import asyncio

    asyncio.run(run())

    after = {
        row["chunk_id"]: row["embedding"]
        for row in connection.execute("SELECT chunk_id, embedding FROM material_chunks_vec")
    }
    assert after, "重解析后向量表空了"
    assert all(
        after.get(chunk_id) != embedding
        for chunk_id, embedding in original.items()
        if chunk_id in after
    ), "重解析后向量原封不动——是旧内容的向量，不是新块的"


# ── 极小的真依赖（避免把整个 container 拉进单测） ──────────────────────────


class _Docs:
    """项目存在性判据：本批只关心 `documents.get` 不抛（它只是"项目在不在"的前置检查）。"""

    def get(self, project_id):  # type: ignore[no-untyped-def]
        return None


class _Parser:
    """包一层真解析器（`LocalMaterialParser`）。

    `variant=True` 时把块切成两半并改写正文——模拟"换了个解析器，块形状与内容都变了"。
    只改 `parser_id` 是不够的：正文相同则向量必然相同，测不出"向量有没有被重算"。
    """

    def __init__(self, *, variant: bool = False) -> None:
        from pmstudio.infrastructure.tools.extract import LocalMaterialParser

        self._inner = LocalMaterialParser()
        self._variant = variant
        self._parser_id = "split-parser" if variant else "local-text"

    def parse(self, *, filename, media_type, data):  # type: ignore[no-untyped-def]
        from dataclasses import replace

        parsed = self._inner.parse(filename=filename, media_type=media_type, data=data)
        if not self._variant:
            return parsed
        chunks = tuple(
            replace(chunk, text=f"改写：{chunk.text}", ordinal=index * 2)
            for index, chunk in enumerate(parsed.chunks)
            for _ in (0,)
        )
        return replace(parsed, parser_id=self._parser_id, chunks=chunks)

    def identities(self):  # type: ignore[no-untyped-def]
        return frozenset({(self._parser_id, "1")})


class _Index:
    def material_references(self, project_id):  # type: ignore[no-untyped-def]
        return {}


class _Clock:
    def now(self):  # type: ignore[no-untyped-def]
        from datetime import datetime

        return datetime.now(UTC)


def _seed_project(connection) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO users (user_id, password_hash, created_at)"
        " VALUES (?, 'x', '2026-10-18T00:00:00+00:00')",
        (str(TEST_OWNER),),
    )

# ── 联网材料也要写向量（批 6a Task 6）──────────────────────────────────────────


def test_a_web_result_also_gets_a_vector(tmp_path):
    """R：**联网搜来的材料也有向量**——今天它没有，所以"F6 搜得到"只对上传的材料成立。

    联网材料是"用户不知道它长什么样"的那一类（外部来源），恰恰最需要语义召回
    （用户只能用自己的话描述需求）。没有向量 = 换个说法就搜不到它。

    装配是**真的**：真 SQLite 仓 + 真向量索引 + 真用例（`SubmitOpening`）。
    """
    from pmstudio.application.use_cases import SubmitOpening
    from pmstudio.infrastructure.sqlite.materials import SqliteMaterialStore
    from pmstudio.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
    from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
    from tests.support.builders import PROJECT, make_web_packet
    from tests.support.doubles import RecordingWebSearch
    from tests.support.harness import Harness

    harness = Harness(replies={}, web_search=RecordingWebSearch())
    connection = database.connect(tmp_path / "web.db")
    connection.execute(
        "INSERT OR IGNORE INTO users (user_id, password_hash, created_at)"
        " VALUES (?, 'x', '2026-10-18T00:00:00+00:00')",
        (str(TEST_OWNER),),
    )
    store = SqliteMaterialStore(connection, ids=SeqIds())
    vectors = SqliteVectorIndex(connection, embedder=FakeEmbedder(dimensions=512))

    opening = SubmitOpening(
        rounds=harness.rounds,
        round_writer=harness.rounds,
        catalog=harness.seats,
        tools=harness.tools,
        available_tool_keys=harness.available_tool_keys,
        web_search=harness.web_search,
        evidence=harness.evidences,
        evidence_writer=harness.evidences,
        materials=store,
        uow=SqliteUnitOfWork(connection),
        clock=harness.clock,
        events=harness.events,
        vectors=vectors,
    )

    draft = make_web_packet(url="https://example.com/a", text="机器内部的线要接到背后")
    packet = opening._keep(PROJECT, draft)

    rows = connection.execute("SELECT count(*) FROM material_chunks_vec").fetchone()[0]
    assert rows == len(packet.parsed.chunks), (
        f"联网材料应有向量（{len(packet.parsed.chunks)} 块），实得 {rows} 条"
    )
    connection.close()
