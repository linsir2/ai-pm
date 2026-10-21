"""F6 的第五类来源：材料块（`P18` 包 C-2c-1）。

两条口径：
1. `SearchProject` 把查询**原样**交给 `MaterialRetriever`（含上限），自己不筛不排；
2. 材料命中排最后（前面四类装满就没有它），且它带锚点与片段——不是一段裸文本。
"""

import logging

from pmstudio.application.dto import MaterialHit
from tests.support.builders import PROJECT
from tests.support.harness import Harness


def hit(**over: object) -> MaterialHit:
    base: dict = {
        "material_id": "m1",
        "chunk_id": "m1#c03",
        "score": 3.0,
        "excerpt": "…结论：导出要走异步队列…",
        "anchor": "第 3 页 §2.1",
        "source_ref": "attachment:诊断报告.pdf",
        "title": "诊断报告.pdf",
    }
    base.update(over)
    return MaterialHit(**base)


def harness_with(hits: tuple[MaterialHit, ...]) -> Harness:
    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()
    harness.material_retriever.returns = hits
    return harness


async def test_search_project_asks_the_retriever_with_the_query_and_the_cap():
    harness = harness_with((hit(),))

    results = await harness.search()(project_id=PROJECT, query="异步队列")

    assert harness.material_retriever.calls == [(str(PROJECT), "异步队列", 50)]
    assert [item.kind for item in results.hits] == ["material"]


async def test_material_hits_carry_their_anchor_and_excerpt():
    harness = harness_with((hit(),))

    results = await harness.search()(project_id=PROJECT, query="异步队列")
    material = results.hits[0]

    assert material.ref == "m1#c03"                      # 点得动的落点是块 id
    assert material.label == "诊断报告.pdf：…结论：导出要走异步队列…"
    assert material.where == "材料 · 第 3 页 §2.1"


async def test_without_material_hits_nothing_changes():
    harness = harness_with(())

    results = await harness.search()(project_id=PROJECT, query="异步队列")

    assert results.hits == ()


async def test_a_blank_query_never_reaches_the_retriever():
    """空查询回空是 F6 的老规矩——连问都不该问检索器（省一次全表扫）。"""

    harness = harness_with((hit(),))

    results = await harness.search()(project_id=PROJECT, query="   ")

    assert results.hits == ()
    assert harness.material_retriever.calls == []


# ── 零命中观测（批 12，R8）────────────────────────────────────────────


async def test_a_zero_hit_material_search_is_logged(caplog):
    """R：非空查询**一个材料都没命中** → 打一行结构化日志。

    为什么要有（这是检索侧最大的缺口）：现在的检索判据**全是「从文档反推」的合成
    查询**——真实的用户查询分布是**空的** → 语义召回的价值没法证明（R9：真数据
    r@5 9/11 与纯 FTS5 持平，"改述增量"一直是 0）。要看到增量，得先有**真查询**。

    I：一条查不到任何材料的查询；
    P：日志里出现一行 `material_search_zero_hit`，带着项目与**查询原文**；
    R：那行能被 grep 出来（`scripts/collect_zero_hit_queries.py` 靠它攒样本）。
    """
    harness = harness_with(())

    with caplog.at_level(logging.INFO):
        await harness.search()(project_id=PROJECT, query="导出权限谁来定")

    lines = [
        record.getMessage()
        for record in caplog.records
        if "material_search_zero_hit" in record.getMessage()
    ]
    assert len(lines) == 1, caplog.records
    assert "导出权限谁来定" in lines[0], lines[0]
    assert str(PROJECT) in lines[0], lines[0]


async def test_a_hit_material_search_is_not_logged(caplog):
    """材料**有**命中 → 不记零命中日志（否则正常查询会把日志淹掉）。"""

    harness = harness_with((hit(),))

    with caplog.at_level(logging.INFO):
        await harness.search()(project_id=PROJECT, query="异步队列")

    assert not [
        record for record in caplog.records if "material_search_zero_hit" in record.getMessage()
    ]


async def test_an_empty_query_is_not_logged(caplog):
    """空查询 → 不记（它连检索器都不问，记下来纯是噪音）。"""

    harness = harness_with(())

    with caplog.at_level(logging.INFO):
        await harness.search()(project_id=PROJECT, query="")

    assert not [
        record for record in caplog.records if "material_search_zero_hit" in record.getMessage()
    ]
