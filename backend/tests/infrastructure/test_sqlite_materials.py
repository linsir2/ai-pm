"""材料存储：包 + 块 + **原始字节**一起落库，同项目内按 sha256 去重。

原始字节必须留：换解析器（docling）时要能**重解析**，不能让用户重传。
"""

import pytest

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.domain.shared.ids import ProjectId
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.materials import SqliteMaterialStore
from tests.support.builders import NOW, PROJECT
from tests.support.doubles import SeqIds


def parsed_material(*chunks: str, truncated: bool = False) -> ParsedMaterial:
    return ParsedMaterial(
        origin="upload",
        title="需求.md",
        source_ref="attachment:需求.md",
        media_type="text/markdown",
        bytes_len=42,
        chunks=tuple(
            MaterialChunk(
                chunk_id=f"local-text#c{index:02d}",
                ordinal=index,
                text=text,
                heading_path=(f"# 第 {index} 节",),
                anchor=f"第 {index + 1} 行",
            )
            for index, text in enumerate(chunks)
        ),
        parser_id="local-text",
        parser_version="pmstudio 0.0.0",
        truncated=truncated,
        warnings=("材料太长（已截断）",) if truncated else (),
    )


@pytest.fixture()
def store(tmp_path):
    connection = database.connect(tmp_path / "pm.db")
    yield SqliteMaterialStore(connection, ids=SeqIds())
    connection.close()


def test_put_then_get_round_trips_the_whole_packet(store):
    saved = store.put(
        project_id=PROJECT,
        parsed=parsed_material("目标：导出", "边界：只给管理员"),
        sha256="a" * 64,
        content=b"raw bytes",
        now=NOW,
    )

    loaded = store.get(saved.material_id)

    assert loaded is not None
    assert loaded.project_id == str(PROJECT)
    assert loaded.parsed.title == "需求.md"
    assert loaded.parsed.parser_id == "local-text"
    assert [(c.chunk_id, c.anchor) for c in loaded.parsed.chunks] == [
        (f"{saved.material_id}#c00", "第 1 行"),
        (f"{saved.material_id}#c01", "第 2 行"),
    ]
    assert loaded.parsed.chunks[1].text == "边界：只给管理员"
    assert store.raw_bytes(saved.material_id) == b"raw bytes"


def test_chunk_ids_are_restamped_with_the_material_id(store):
    """解析器给的是 `local-text#c00`（它还不知道 id）；存储层盖章成 `m-xxx#c00`。"""

    saved = store.put(
        project_id=PROJECT, parsed=parsed_material("一条"), sha256="b" * 64,
        content=b"x", now=NOW,
    )

    assert saved.parsed.chunks[0].chunk_id == f"{saved.material_id}#c00"
    assert saved.parsed.chunks[0].chunk_id.startswith(saved.material_id)


def test_the_same_bytes_are_deduped_per_project(store):
    first = store.put(
        project_id=PROJECT, parsed=parsed_material("一条"), sha256="c" * 64,
        content=b"x", now=NOW,
    )
    second = store.put(
        project_id=PROJECT, parsed=parsed_material("另一份内容"), sha256="c" * 64,
        content=b"x", now=NOW,
    )

    assert second.material_id == first.material_id
    assert second.parsed.chunks[0].text == "一条"      # 复用旧的那份，不覆盖
    assert store.chunk_count(first.material_id) == 1


def test_a_packet_with_a_warning_is_persisted_with_it(store):
    saved = store.put(
        project_id=PROJECT, parsed=parsed_material("一条", truncated=True), sha256="d" * 64,
        content=b"x", now=NOW,
    )

    loaded = store.get(saved.material_id)

    assert loaded.parsed.truncated is True
    assert loaded.parsed.warnings == ("材料太长（已截断）",)


def test_list_returns_summaries_without_chunk_text(store):
    """列表是**摘要**：块正文一个字都不进（50 份 × 400 块不能塞进一个响应）。"""

    saved = store.put(
        project_id=PROJECT, parsed=parsed_material("目标：导出", "边界：只给管理员"),
        sha256="e" * 64, content=b"x", now=NOW,
    )

    summaries = store.list_for_project(
        PROJECT, current=frozenset({("local-text", "pmstudio 0.0.0")})
    )

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.material_id == saved.material_id
    assert summary.chunk_count == 2
    assert summary.chars == len("目标：导出") + len("边界：只给管理员")
    assert not hasattr(summary, "text")
    assert summary.stale is False


def test_a_packet_read_by_an_older_parser_is_marked_stale(store):
    store.put(
        project_id=PROJECT, parsed=parsed_material("一条"), sha256="f" * 64,
        content=b"x", now=NOW,
    )

    summaries = store.list_for_project(PROJECT, current=frozenset({("docling", "docling 2.5.0")}))

    assert summaries[0].stale is True
    assert summaries[0].parser_id == "local-text"       # 照实说是谁读的


def test_get_unknown_returns_none_and_delete_is_idempotent(store):
    assert store.get("m-不存在") is None
    assert store.raw_bytes("m-不存在") is None
    store.delete("m-不存在")                            # 不抛


def test_other_projects_materials_are_not_listed(store):
    store.put(
        project_id=ProjectId("别的项目"), parsed=parsed_material("一条"), sha256="1" * 64,
        content=b"x", now=NOW,
    )

    assert store.list_for_project(PROJECT, current=frozenset()) == ()


def test_refresh_replaces_bytes_and_chunks_but_keeps_identity(store):
    """同址再抓（`P27`）：原始字节、块、解析器身份与标题一起换；id / 去重键 / 创建时间不动。"""

    saved = store.put(
        project_id=PROJECT, parsed=parsed_material("旧正文"), sha256="2" * 64,
        content=b"old html", now=NOW,
    )

    refreshed = store.refresh(
        saved.material_id,
        parsed=parsed_material("新正文", "还有一段"),
        content=b"new html",
    )

    assert refreshed.material_id == saved.material_id
    assert refreshed.created_at == saved.created_at
    assert store.raw_bytes(saved.material_id) == b"new html"
    assert [chunk.text for chunk in refreshed.parsed.chunks] == ["新正文", "还有一段"]
    assert refreshed.parsed.chunks[0].chunk_id == f"{saved.material_id}#c00"
    assert refreshed.parsed.bytes_len == len(b"new html")
    assert len(store.list_for_project(PROJECT, current=frozenset())) == 1
