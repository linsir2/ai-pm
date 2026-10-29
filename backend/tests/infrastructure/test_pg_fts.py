"""真 PostgreSQL：材料写入、pg_search BM25 候选与 Robertson 重排。

写路径只写 `search_text`（`index_text`：标题 + 章节链 + 正文原样）；
`search_tokens` 是 generated 列（统一由 `pmstudio_tokens` 的 jieba 产生）。
查询侧 `pmstudio_tokens(query)` → `search_text ||| tokens::text[]` 取 200 候选，
再按全语料 df 做 Robertson BM25 重排；旧词袋列与 GIN 索引已删除。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from scripts.migrate import apply_migrations

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.domain.shared.ids import ProjectId, UserId
from pmstudio.infrastructure.postgres.database import connect
from pmstudio.infrastructure.postgres.materials import (
    SqliteFtsRetriever,
    SqliteHybridRetriever,
    SqliteMaterialStore,
    _robertson_scores,
)
from pmstudio.infrastructure.postgres.owner_scope import (
    clear_owner_scope,
    set_owner_scope,
)
from pmstudio.infrastructure.postgres.tokenize import index_text
from tests.support.doubles import SeqIds
from tests.support.postgres import (
    PgTestDatabase,
    load_environment,
    prepare,
    truncate_data_tables,
    upsert_vector_profile,
)

OWNER = UserId("u-fts")
OTHER_OWNER = UserId("u-fts-other")
PROJECT = ProjectId("p-fts")
OTHER_PROJECT = ProjectId("p-fts-other")
NOW_TEXT = "2026-10-27T00:00:00+00:00"
_IDS = SeqIds()


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


@pytest.fixture(scope="module")
def database() -> PgTestDatabase:
    pg = load_environment()
    prepare(pg)
    apply_migrations(pg.direct_url)
    return pg


@pytest.fixture()
def conn(database: PgTestDatabase) -> Iterator[object]:
    truncate_data_tables(database)
    upsert_vector_profile(database)
    token = set_owner_scope(OWNER)
    with connect(database.direct_url) as connection:
        yield connection
    clear_owner_scope(token)
    truncate_data_tables(database)
    upsert_vector_profile(database)


def seed(
    conn,  # type: ignore[no-untyped-def]
    *texts: str,
    title: str = "诊断报告.pdf",
    project_id: ProjectId = PROJECT,
    owner: UserId = OWNER,
    origin: str = "upload",
) -> str:
    store = SqliteMaterialStore(conn, ids=_IDS)
    return store.put(
        project_id=project_id,
        parsed=parsed(*texts, title=title, origin=origin),
        sha256=hashlib.sha256(f"{title}:{texts}:{origin}".encode()).hexdigest(),
        content=b"raw",
        now=datetime.fromisoformat(NOW_TEXT),
        owner=owner,
    ).material_id


# ── 写路径：search_text / generated search_tokens / 原生类型 ───────────────────


def test_search_text_is_written_and_feeds_generated_tokens(conn) -> None:
    mid = seed(conn, "权限模型 与 访问控制", title="权限说明.pdf")
    row = conn.execute(
        "SELECT search_text, search_tokens, pmstudio_tokens(search_text) AS expected"
        " FROM material_chunks WHERE chunk_id = %s",
        (f"{mid}#c00",),
    ).fetchone()
    assert row is not None
    assert row["search_text"] == index_text(
        title="权限说明.pdf", heading_path=(), text="权限模型 与 访问控制"
    )
    assert row["search_text"] == "权限说明.pdf 权限模型 与 访问控制"
    assert list(row["search_tokens"]) == list(row["expected"])
    assert "权限" in row["search_tokens"] and "模型" in row["search_tokens"]


def test_material_native_types_roundtrip(conn) -> None:
    from dataclasses import replace

    store = SqliteMaterialStore(conn, ids=_IDS)
    packet = store.put(
        project_id=PROJECT,
        parsed=replace(parsed("退货时限是七天"), truncated=True, warnings=("截断",)),
        sha256="types-roundtrip",
        content=b"\x00raw\xff",
        now=datetime.fromisoformat(NOW_TEXT),
        owner=OWNER,
    )
    row = conn.execute(
        "SELECT truncated, warnings, content, created_at FROM materials"
        " WHERE material_id = %s",
        (packet.material_id,),
    ).fetchone()
    assert row is not None
    assert row["truncated"] is True
    assert list(row["warnings"]) == ["截断"]
    assert bytes(row["content"]) == b"\x00raw\xff"
    assert row["created_at"].isoformat() == NOW_TEXT

    loaded = store.get(packet.material_id, owner=OWNER)
    assert loaded is not None
    assert loaded.created_at.isoformat() == NOW_TEXT
    assert loaded.parsed.truncated is True
    assert loaded.parsed.warnings == ("截断",)
    assert loaded.parsed.chunks[0].heading_path == ()


def test_bm25_index_covers_search_text(conn) -> None:
    row = conn.execute(
        "SELECT indexdef FROM pg_indexes"
        " WHERE schemaname = current_schema()"
        " AND indexname = 'idx_material_chunks_search_text_bm25'"
    ).fetchone()
    assert row is not None
    assert "using bm25" in row["indexdef"].lower()
    assert "search_text" in row["indexdef"]


# ── 查询：中文、OR、转义、空查询、隔离 ──────────────────────────────────────


def test_chinese_words_hit_and_order_does_not_matter(conn) -> None:
    mid = seed(conn, "权限模型 的核心是角色绑定")
    retriever = SqliteFtsRetriever(conn)

    assert [h.chunk_id for h in retriever.search(project_id=PROJECT, query="权限")] == [
        f"{mid}#c00"
    ]
    assert [h.chunk_id for h in retriever.search(project_id=PROJECT, query="模型权限")] == [
        f"{mid}#c00"
    ]
    assert [h.chunk_id for h in retriever.search(project_id=PROJECT, query="权限与模型")] == [
        f"{mid}#c00"
    ]


def test_special_characters_are_tokenized_not_parsed_as_query_operators(conn) -> None:
    mid = seed(conn, "a&b 与 c:d 以及 x!y 与 100% 的说明", title="符号.md")
    retriever = SqliteFtsRetriever(conn)

    for query in ("a&b", "c:d", "x!y", "a'b", "100%"):
        hits = retriever.search(project_id=PROJECT, query=query)
        assert hits, f"{query!r} 应该命中且不抛查询语法错"
        assert hits[0].material_id == mid

    assert retriever.search(project_id=PROJECT, query="   ") == ()
    assert retriever.search(project_id=PROJECT, query="!!!") == ()


def test_rank_is_positive_and_more_matches_rank_first(conn) -> None:
    mid = seed(conn, "异步队列 与 异步队列：两次都提了", "这里只提了一次 异步队列")
    for index in range(3):  # 补无关文档让 df < N-1，走原始 Robertson（这里 IDF 为正）
        seed(conn, f"机柜布线规范 第 {index} 版")
    hits = SqliteFtsRetriever(conn).search(project_id=PROJECT, query="异步队列")

    assert [h.chunk_id for h in hits] == [f"{mid}#c00", f"{mid}#c01"]
    assert all(hit.score > 0 for hit in hits), [hit.score for hit in hits]
    assert hits[0].score > hits[1].score


def test_more_matches_rank_first_without_decoys(conn) -> None:
    """无 decoy 的 2 块小语料：共同词（`df >= N-1`）钳到 0 后仍按 tf 排序。

    Robertson 原始 IDF 在 `df == N` 时为负，会让"多命中一次"反而扣分、
    短块压过长块。这类**高频/小语料**词钳到 `0 + 1e-9` 后，tf/长度仍能排出先后，
    且分数仍为正。
    """

    mid = seed(conn, "异步队列 与 异步队列：两次都提了", "这里只提了一次 异步队列")
    hits = SqliteFtsRetriever(conn).search(project_id=PROJECT, query="异步队列")

    assert [h.chunk_id for h in hits] == [f"{mid}#c00", f"{mid}#c01"]
    assert all(hit.score > 0 for hit in hits), [hit.score for hit in hits]
    assert hits[0].score > hits[1].score


def test_a_rare_term_beats_common_terms(conn) -> None:
    """Robertson IDF：只命中一个罕见词的块，压过命中多个常见词的块。"""

    rare = seed(conn, "紫貂 皮草 拍卖 记录")
    for index in range(5):
        seed(conn, f"出口 关税 报关 流程 第 {index} 版")

    hits = SqliteFtsRetriever(conn).search(
        project_id=PROJECT, query="紫貂 出口 关税 报关"
    )

    assert hits
    assert hits[0].material_id == rare


def _idf_corpus(*, total: int, frequency: int) -> dict[str, tuple[str, ...]]:
    """N=total、查询词 df=frequency 的语料：多命中块排第一是唯一区分点。

    块长都为 3，避免长度归一掩盖 tf 差异。
    """
    corpus: dict[str, tuple[str, ...]] = {
        "c-multi": ("共同", "共同", "填充"),
        "c-single": ("共同", "填充", "填充"),
    }
    for index in range(frequency - 2):
        corpus[f"c-common-{index}"] = ("共同", "填充", "填充")
    for index in range(total - frequency):
        corpus[f"c-filler-{index}"] = ("其他", "填充", "填充")
    assert len(corpus) == total
    return corpus


@pytest.mark.parametrize(("total", "frequency"), [(2, 2), (3, 2), (17, 16), (17, 17)])
def test_a_high_frequency_term_is_clamped_and_still_ranks_more_matches_first(
    total: int, frequency: int
) -> None:
    """`df >= N-1` 的高频词钳到 `0 + 1e-9`，tf 仍能排出先后。

    覆盖 N=2/3 的小语料与 N=17 的大语料；这几种情况下原始 Robertson IDF 为负，
    不钳会把"多命中一次"变成扣分。
    """
    corpus = _idf_corpus(total=total, frequency=frequency)
    scores = _robertson_scores(
        [{"chunk_id": "c-multi"}, {"chunk_id": "c-single"}], ["共同"], corpus
    )

    assert scores["c-multi"] > scores["c-single"] > 0


def test_a_normal_corpus_does_not_clamp_terms_below_n_minus_one() -> None:
    """N=17、df=15 < N-1：保留 Robertson raw（负值），**不许误钳**。

    钳到 0 的实现会把"多命中一次"排前；保留 raw 负值时，多命中一次反而扣分
    （这是 Robertson 的原义，用于压低过于常见的词）。
    """
    corpus = {
        "c-twice": ("常见", "常见", "填充"),
        "c-once": ("常见", "填充", "填充"),
    }
    for index in range(13):
        corpus[f"c-common-{index}"] = ("常见", "填充", "填充")
    for index in range(2):
        corpus[f"c-filler-{index}"] = ("其他", "填充", "填充")
    assert len(corpus) == 17

    scores = _robertson_scores(
        [{"chunk_id": "c-twice"}, {"chunk_id": "c-once"}], ["常见"], corpus
    )

    assert scores["c-once"] > scores["c-twice"], (
        "df=15 < N-1=16 被钳平了：多命中又变成加分（误钳）"
    )


def test_a_rare_term_still_beats_a_common_term_at_scale() -> None:
    """N=17：罕见词（df=1）仍压过常见词（df=15，raw 为负）。"""
    corpus: dict[str, tuple[str, ...]] = {
        "c-rare": ("紫貂", "皮草", "记录"),
        "c-common": ("出口", "关税", "流程"),
    }
    for index in range(14):
        corpus[f"c-common-{index}"] = ("出口", "关税", "流程")
    corpus["c-filler"] = ("无关", "内容", "归档")
    assert len(corpus) == 17

    scores = _robertson_scores(
        [{"chunk_id": "c-rare"}, {"chunk_id": "c-common"}], ["紫貂", "出口"], corpus
    )

    assert scores["c-rare"] > scores["c-common"]


def test_title_and_heading_path_are_searchable(conn) -> None:
    mid = seed(conn, "正文里没有那个词", title="别的说明.pdf")
    retriever = SqliteFtsRetriever(conn)
    assert [h.chunk_id for h in retriever.search(project_id=PROJECT, query="权限说明")] == [
        f"{mid}#c00"
    ]

    from pmstudio.infrastructure.tools.extract import chunk_text

    chunks = chunk_text("## 权限模型\n" + "角色绑定说明。" * 120, parser_id="t")
    store = SqliteMaterialStore(conn, ids=_IDS)
    packet = store.put(
        project_id=PROJECT,
        parsed=ParsedMaterial(
            origin="upload",
            title="长节.md",
            source_ref="attachment:长节.md",
            media_type="text/markdown",
            bytes_len=1,
            chunks=chunks,
            parser_id="local-text",
            parser_version="v1",
        ),
        sha256="long-section",
        content=b"raw",
        now=datetime.fromisoformat(NOW_TEXT),
        owner=OWNER,
    )
    hits = retriever.search(project_id=PROJECT, query="权限模型")
    assert len(hits) == len(chunks)
    assert all(h.material_id == packet.material_id for h in hits)


def test_project_and_owner_isolation(conn) -> None:
    seed(conn, "权限模型 在别的项目", project_id=OTHER_PROJECT)
    seed(conn, "权限模型 是别人的", owner=OTHER_OWNER)
    mine = seed(conn, "权限模型 是我的")

    hits = SqliteFtsRetriever(conn).search(project_id=PROJECT, query="权限模型")

    assert [h.material_id for h in hits] == [mine]


def test_replace_and_delete_keep_the_generated_index_in_sync(conn) -> None:
    mid = seed(conn, "旧的权限模型说明")
    store = SqliteMaterialStore(conn, ids=_IDS)
    retriever = SqliteFtsRetriever(conn)
    assert retriever.search(project_id=PROJECT, query="权限模型")

    store.replace_chunks(mid, parsed("全新的异步队列说明"))
    assert retriever.search(project_id=PROJECT, query="权限模型") == ()
    assert [h.chunk_id for h in retriever.search(project_id=PROJECT, query="异步队列")] == [
        f"{mid}#c00"
    ]

    store.delete(mid, owner=OWNER)
    assert retriever.search(project_id=PROJECT, query="异步队列") == ()
    assert conn.execute(
        "SELECT count(*) AS n FROM material_chunks WHERE material_id = %s", (mid,)
    ).fetchone() == {"n": 0}


# ── 子串兜底：LIKE → ILIKE，通配符按字面处理 ─────────────────────────────────


def _put_chunk(conn, chunk_id: str, text: str, *, ordinal: int = 0) -> None:
    mid = f"m-{chunk_id}"
    conn.execute(
        "INSERT INTO materials (material_id, project_id, owner_id, origin, title, source_ref,"
        " media_type, bytes_len, sha256, parser_id, parser_version, schema_version, truncated,"
        " warnings, content, created_at)"
        " VALUES (%s, %s, %s, 'upload', 't', 'r', 'text/plain', 0, %s, 'p', 'v', 1,"
        " false, '{}', %s, %s)",
        (mid, str(PROJECT), str(OWNER), f"sha-{chunk_id}", b"", NOW_TEXT),
    )
    conn.execute(
        "INSERT INTO material_chunks (chunk_id, material_id, owner_id, ordinal, text,"
        " heading_path, anchor, search_text) VALUES (%s, %s, %s, %s, %s, '{}', 'a', %s)",
        (
            chunk_id,
            mid,
            str(OWNER),
            ordinal,
            text,
            index_text(title="t", heading_path=(), text=text),
        ),
    )


def test_substring_fallback_uses_ilike_and_escapes_wildcards(conn) -> None:
    _put_chunk(conn, "c1", "进度 100% 完成")
    _put_chunk(conn, "c2", "进度 axb 完成")
    _put_chunk(conn, "c3", r"路径 C:\tmp\a_b")
    _put_chunk(conn, "c4", "路径 C:/tmp/axb")
    index = SqliteHybridRetriever(conn, embedder=object())

    def candidates(query: str) -> set[str]:
        return set(
            index._substring_candidates(
                project_id=str(PROJECT),
                owner=str(OWNER),
                query=query,
                limit=10,
                exclude=set(),
            )
        )

    assert candidates("%") == {"c1"}
    assert candidates("_") == {"c3"}
    assert candidates("\\") == {"c3"}
    assert candidates("路径") == {"c3", "c4"}
    assert candidates("100%") == {"c1"}


def test_candidates_are_capped_before_rerank(conn, monkeypatch) -> None:
    from pmstudio.infrastructure.postgres import materials as materials_module

    mid = seed(conn, "退货流程 第一段", "退货流程 第二段", "退货流程 第三段")
    monkeypatch.setattr(materials_module, "_FTS_CANDIDATE_LIMIT", 2)

    hits = SqliteFtsRetriever(conn).search(
        project_id=PROJECT, query="退货流程", limit=10
    )

    assert len(hits) == 2
    assert all(hit.material_id == mid for hit in hits)


# ── 守卫：FTS5 / vec0 / 影子表路径已不在 PG 模块 ───────────────────────────────


def test_pg_material_modules_have_no_sqlite_fts_or_vec_paths() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "pmstudio" / "infrastructure" / "postgres"
    banned = (
        "material_chunks_" + "fts",
        "material_chunks_" + "vec",
        "sqlite" + "_vec",
        "bm25(",
        "MATCH ?",
        "INSERT" + " OR REPLACE",
    )
    offenders = {}
    for name in ("materials.py", "vector_index.py"):
        source = (root / name).read_text(encoding="utf-8")
        found = [needle for needle in banned if needle in source]
        if found:
            offenders[name] = found
    assert offenders == {}
