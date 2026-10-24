"""材料检索升级（FTS5 + jieba）：词级、词序无关、容忍停用词（检索 P1）。

被替换掉的 v1（整句 `LIKE`，**2026-10-17 删除**）要求中文"逐字连续子串"。
验收实测（`docs/plans/2026-10-17-检索升级验收.md`）：v1 对"同词换序 / 插停用词 / 按标题"
三类 recall@5 **全 0**，v2 jieba-FTS+**OR** 各 42/43/42 于 46；合计 top1 34→134。

四条口径（块级、锚点、字面通配符、项目隔离）与 v1 逐字不变——**换实现类，签名与语义不变**。
"""

import logging

import pytest

from pmstudio.domain.shared.ids import ProjectId
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.materials import (
    SqliteFtsRetriever,
    SqliteMaterialStore,
)
from tests.conftest import TEST_OWNER
from tests.support.builders import NOW, PROJECT
from tests.support.doubles import SeqIds

OTHER = ProjectId("p2")


def parsed(*texts: str, title: str = "诊断报告.pdf", origin: str = "upload"):
    """一份解析草稿（单块/多块）。照搬原 v1 测试的构造器（v1 已删，留在这儿）。"""

    from pmstudio.application.dto import MaterialChunk, ParsedMaterial

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
    """一个带材料的库：`store` 盖章、`SqliteFtsRetriever` 检索（共用同一个连接）。"""

    connection = database.connect(tmp_path / "pm.db")
    store = SqliteMaterialStore(connection, ids=SeqIds())

    def seed(*texts: str, title: str = "诊断报告.pdf", project_id: ProjectId = PROJECT) -> str:
        packet = store.put(
            project_id=project_id,
            parsed=parsed(*texts, title=title),
            sha256=f"{title}-{len(texts)}-{texts}".ljust(64, "0")[:64],
            content=b"raw",
            now=NOW,
        )
        return packet.material_id

    yield SqliteFtsRetriever(connection), seed, store, connection
    connection.close()


# ── 核心：v1 命中不了的三种，FTS 要能命中 ──────────────────────────────────────


def test_a_chinese_word_hits_without_being_a_contiguous_substring(world):
    """R：查"权限"要命中"权限模型"——`LIKE '%权限%'` 命中得了，但这不是升级点；
    升级点是**下面两条**。这条先钉住"最短查询词也进倒排"。"""

    retriever, seed, _, _ = world
    mid = seed("权限模型 与 访问控制")

    hits = retriever.search(project_id=PROJECT, query="权限")

    assert [h.chunk_id for h in hits] == [f"{mid}#c00"]


def test_words_in_a_different_order_still_hit(world):
    """R（升级点 1）：查"模型权限"要命中"权限模型"——`LIKE` 必挂（实测 0/15）。"""

    retriever, seed, _, _ = world
    mid = seed("权限模型 的核心是角色绑定")

    hits = retriever.search(project_id=PROJECT, query="模型权限")

    assert [h.chunk_id for h in hits] == [f"{mid}#c00"]


def test_a_stopword_between_words_still_hits(world):
    """R（升级点 2）：查"权限与模型"要命中"权限模型"——`LIKE` 必挂（实测 0/15）。"""

    retriever, seed, _, _ = world
    mid = seed("权限模型 的核心是角色绑定")

    hits = retriever.search(project_id=PROJECT, query="权限与模型")

    assert [h.chunk_id for h in hits] == [f"{mid}#c00"]


# ── 复用 v1 的既有口径（回归锚）───────────────────────────────────────────────


def test_hits_are_chunk_level_with_anchor_and_excerpt(world):
    retriever, seed, _, _ = world
    mid = seed("结论：导出要走异步队列", "边界：只给管理员")

    hits = retriever.search(project_id=PROJECT, query="异步队列")

    assert [h.chunk_id for h in hits] == [f"{mid}#c00"]
    assert hits[0].title == "诊断报告.pdf"
    assert hits[0].anchor == "第 1 行"
    assert "异步队列" in hits[0].excerpt


def test_the_excerpt_is_a_window_around_the_hit(world):
    retriever, seed, _, _ = world
    seed("前" * 100 + "异步队列" + "后" * 100)

    hit = retriever.search(project_id=PROJECT, query="异步队列")[0]

    assert "异步队列" in hit.excerpt
    assert len(hit.excerpt) < 120


def test_a_query_with_wildcards_is_matched_literally(world):
    """搜 `100%` 不许把 `%` 当通配符（token 加引号 → FTS 当字面串）。"""

    retriever, seed, _, _ = world
    seed("导出 100% 的行数上限是 5 万", "纯纯的导出说明")

    hits = retriever.search(project_id=PROJECT, query="100%")

    assert len(hits) == 1
    assert "100%" in hits[0].excerpt


def test_other_projects_material_never_leaks(world):
    retriever, seed, _, _ = world
    seed("权限模型 在别的项目", project_id=OTHER)

    assert retriever.search(project_id=PROJECT, query="权限模型") == ()


def test_a_blank_query_returns_nothing(world):
    retriever, seed, _, _ = world
    seed("权限模型")

    assert retriever.search(project_id=PROJECT, query="   ") == ()


def test_special_characters_do_not_break_the_query(world):
    """`a"b` 分词会切出一个 `"` token——它不该把 FTS 语法搞崩（纯标点 token 丢弃）。"""

    retriever, seed, _, _ = world
    seed("关于 a 和 b 的说明")

    hits = retriever.search(project_id=PROJECT, query='a"b')

    assert [h.chunk_id for h in hits]  # 命中（a / b 任一即可），且不抛


def test_more_matches_rank_first(world):
    """`score` 与展示顺序同向：命中越多越靠前。"""

    retriever, seed, _, _ = world
    mid = seed("异步队列 与 异步队列：两次都提了", "这里只提了一次 异步队列")

    hits = retriever.search(project_id=PROJECT, query="异步队列")

    assert [h.chunk_id for h in hits] == [f"{mid}#c00", f"{mid}#c01"]
    assert hits[0].score > hits[1].score


# ── 索引与写路径同步（块随材料增删改）──────────────────────────────────────────


def test_rewriting_a_material_refreshes_the_index(world):
    """改材料正文（`replace_chunks`）→ 旧词不再中、新词要中。"""

    retriever, seed, store, _ = world
    mid = seed("旧的权限模型说明")
    assert retriever.search(project_id=PROJECT, query="权限模型")

    store.replace_chunks(mid, parsed("全新的异步队列说明"))

    assert retriever.search(project_id=PROJECT, query="权限模型") == ()
    assert [h.chunk_id for h in retriever.search(project_id=PROJECT, query="异步队列")] == [
        f"{mid}#c00"
    ]


def test_deleting_a_material_drops_its_index(world):
    retriever, seed, store, _ = world
    mid = seed("权限模型 说明")
    assert retriever.search(project_id=PROJECT, query="权限模型")

    store.delete(mid)

    assert retriever.search(project_id=PROJECT, query="权限模型") == ()


def test_backfill_indexes_existing_chunks_idempotently(tmp_path):
    """老库（有块、无 FTS）跑 migrate → 补全；再跑一次不重复、也不报错。"""

    connection = database.connect(tmp_path / "old.db")
    # 绕过 store 直接塞一行块，模拟"升级前入库的旧数据"
    connection.execute(
        "INSERT INTO materials (material_id, project_id, owner_id, origin, title, source_ref,"
        " media_type, bytes_len, sha256, parser_id, parser_version, schema_version, truncated,"
        " warnings, content, created_at) VALUES ('m0', ?, ?, 'upload', 't', 'r', 'text/x',"
        " 1, 'sha', 'p', 'v', 1, 0, '[]', X'00', ?)",
        (str(PROJECT), str(TEST_OWNER), NOW.isoformat()),
    )
    connection.execute(
        "INSERT INTO material_chunks (chunk_id, material_id, owner_id, ordinal, text,"
        " heading_path, anchor) VALUES ('m0#c00', 'm0', ?, 0, '权限模型 说明', '[]', 'a')",
        (str(TEST_OWNER),),
    )
    assert SqliteFtsRetriever(connection).search(project_id=PROJECT, query="权限模型") == ()

    database.migrate(connection)
    assert [h.chunk_id for h in SqliteFtsRetriever(connection).search(
        project_id=PROJECT, query="权限模型")] == ["m0#c00"]

    database.migrate(connection)  # 幂等：再跑不重复
    rows = connection.execute("SELECT COUNT(*) FROM material_chunks_fts").fetchone()[0]
    assert rows == 1
    connection.close()


# ── 索引源：标题 + 祖先标题 + 正文（真数据坐实的缺口）──────────────────────────


def _parsed_with_chunks(title, chunks):
    from pmstudio.application.dto import ParsedMaterial

    return ParsedMaterial(
        origin="upload", title=title, source_ref=f"attachment:{title}",
        media_type="text/markdown", bytes_len=1, chunks=tuple(chunks),
        parser_id="local-text", parser_version="v1",
    )


def test_a_section_name_finds_every_chunk_under_it(world):
    """R：长节被切成多块时，**后几块正文里没有节名**（只在 `heading_path`）——
    所以搜节名必须也命中它们（改前只中第 1 块）。用**真解析器**切块，不手搓。"""

    from pmstudio.infrastructure.tools.extract import chunk_text

    retriever, _, store, _ = world
    chunks = chunk_text("## 权限模型\n" + "角色绑定说明。" * 120, parser_id="t")
    assert len(chunks) >= 3
    in_text = [c for c in chunks if "权限模型" in c.text]
    assert len(in_text) == 1, "前置不成立：本该只有第一块正文带节名"

    packet = store.put(
        project_id=PROJECT,
        parsed=_parsed_with_chunks("长节.md", chunks),
        sha256="long-section".ljust(64, "0")[:64],
        content=b"raw",
        now=NOW,
    )

    hits = retriever.search(project_id=PROJECT, query="权限模型")

    assert len(hits) == len(chunks), "节名只命中了正文带它的那块，`heading_path` 没进索引"
    assert all(h.material_id == packet.material_id for h in hits)


def test_a_material_is_findable_by_its_title(world):
    """R：材料标题（文件名/页标题）不在任何块正文里——搜它要能按标题找到。"""

    retriever, seed, _, _ = world
    mid = seed("正文里一个字都没有那个词", title="权限说明.pdf")

    hits = retriever.search(project_id=PROJECT, query="权限说明")

    assert [h.chunk_id for h in hits] == [f"{mid}#c00"]


def test_every_search_is_observed_with_its_caller(world, caplog):
    """顶栏搜索那一行必须带 caller/stage/命中数（P0.4）。

    分解行（词面/语义）只在混合检索里；纯 FTS 只有命中数。
    """

    retriever, seed, _, _ = world
    seed("权限模型 与 访问控制")

    with caplog.at_level(logging.INFO):
        hits = retriever.search(project_id=PROJECT, query="权限", owner=TEST_OWNER)

    assert hits
    line = next(
        record.getMessage()
        for record in caplog.records
        if "material_search" in record.getMessage()
    )
    assert "caller=search" in line
    assert "stage=lookup" in line
    assert f"hits={len(hits)}" in line
