"""检索调用要能分清"谁发起的、哪一步、命中多少"（P0 Task 0.4）。

老观测行只有词面/语义分解，分不清是顶栏搜索还是建轮——R8 的真查询样本因此被污染。
本文件只钉**用例层**的那一行：`caller=round stage=seed hits=N`。
顶栏搜索那一行由 `SqliteFtsRetriever` 自己记（见 `test_sqlite_material_search_fts.py`）。
"""

import logging

from pmstudio.application.dto import MaterialHit
from tests.support.builders import PROJECT
from tests.support.doubles import RecordingMaterialRetriever
from tests.support.harness import Harness


def _ghost_hit() -> MaterialHit:
    return MaterialHit(
        material_id="ghost",
        chunk_id="ghost#c00",
        score=1.0,
        excerpt="…",
        anchor="第 1 行",
        source_ref="attachment:ghost.md",
        title="ghost.md",
    )


async def test_seed_retrieval_is_observed_with_caller_and_stage(caplog):
    """建轮检索必须留下 caller=round / stage=seed / hits=N。"""

    harness = Harness(replies={})
    harness.seed_project()
    retriever = RecordingMaterialRetriever(returns=(_ghost_hit(),))

    with caplog.at_level(logging.INFO):
        await harness.start_round(retriever=retriever).create(
            project_id=PROJECT, user_input="导出权限怎么定", material_ids=()
        )

    lines = [
        record.getMessage()
        for record in caplog.records
        if "material_search" in record.getMessage()
    ]
    assert len(lines) == 1
    assert "caller=round" in lines[0]
    assert "stage=seed" in lines[0]
    assert "hits=1" in lines[0]
