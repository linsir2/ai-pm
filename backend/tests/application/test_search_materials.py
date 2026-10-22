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


# ── 检索观测（批 12 的 R8 → 批 13 挪进检索器）──────────────────────────


async def test_the_observation_lives_in_the_retriever_not_here(caplog):
    """R：**观测行只出现在检索器里**（用例层不重复记）。

    2026-10-22（批 13，R9）：观测从用例挪进 `SqliteHybridRetriever.search`——
    只有那里知道**分解**（词面/语义各出了多少候选、纯词面命中几个）。
    用例拿到的只是融合后的结果 → 它记不出分解，也**不许重复记**（一行就够，
    便于后面按行抽样本）。
    """

    harness = harness_with((hit(),))

    with caplog.at_level(logging.INFO):
        await harness.search()(project_id=PROJECT, query="异步队列")

    assert not [
        record for record in caplog.records if "material_search" in record.getMessage()
    ]
