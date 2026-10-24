"""两段式材料选择：手勾保底 + 库级检索按分数收敛（stage 2）。

口径（`plan-rag-redesign.md` v5 的 F2/F3）：

1. **手勾的材料一定在**（第一段保留"每份材料露一名"的保底）；
2. 自动检索进来的材料**按分数收敛**——候选只有命中的块，且不摊薄预算；
3. 自动材料**不**算进"你勾了却没带进来"的留痕（那是另一句话）；
4. **两段合用一个预算**（+ 收尾预留一次），并且 `create()` 不能用自动材料
   在 `packets` / `position` 里查不到而 `KeyError`（v2 的崩点）。
"""

from dataclasses import replace

import pytest

from pmstudio.application import context
from pmstudio.application.dto import MaterialHit
from pmstudio.application.use_cases import SECTION_OVERHEAD, external_material_text
from pmstudio.domain.round.retrieval import RetrievalStage
from pmstudio.domain.shared.errors import ShapeError
from tests.application.test_materials import make_material
from tests.support.builders import PROJECT
from tests.support.doubles import RecordingMaterialRetriever
from tests.support.harness import Harness

INPUT = "导出权限怎么定"


def hit(packet, index: int = 0, score: float = 1.0) -> MaterialHit:
    chunk = packet.parsed.chunks[index]
    return MaterialHit(
        material_id=packet.material_id,
        chunk_id=chunk.chunk_id,
        score=score,
        excerpt=chunk.text[:20],
        anchor=chunk.anchor,
        source_ref=packet.parsed.source_ref,
        title=packet.parsed.title,
    )


def harness_with(*packets) -> Harness:
    harness = Harness(replies={})
    harness.seed_project()
    for packet in packets:
        harness.materials.seed(packet)
    return harness


class _BoomRetriever:
    """检索器挂了：本轮的选料**不该**跟着挂（它只是增强项）。"""

    def search(self, *, project_id, query, limit=5):  # type: ignore[no-untyped-def]
        raise RuntimeError("向量索引坏了")


def selection_cost(harness: Harness, round_) -> int:
    """这一轮带进来的块，按**同一把尺子**（`material_block_cost`）算出来的成本。

    抬头算在"该材料被选中的第一块"上——与选块时同一条判据（F0）。
    """

    total = 0
    first_seen: set[str] = set()
    for ref in round_.materials:
        packet = harness.materials.get(ref.material_id)
        chunk = next(c for c in packet.parsed.chunks if c.chunk_id == ref.chunk_id)
        first = ref.material_id not in first_seen
        first_seen.add(ref.material_id)
        total += context.material_block_cost(packet, chunk, first_of_material=first)
    return total


async def test_the_auto_materials_land_in_packets_and_order():
    """I（v2 的崩点）：自动材料进 `packets`/`order`，`create()` 不 KeyError 且都发到号。

    一份**没勾**的材料被检索命中 → 它的块要带 E 编号进这一轮。之前只交手勾的那份
    `packets`/`position`，这里会直接 `KeyError`。
    """

    pinned = make_material("目标：导出要有权限", material_id="m1")
    other = make_material(
        "导出权限按角色控制，管理员才能导出全量数据",
        material_id="m2",
        title="权限设计.md",
    )
    harness = harness_with(pinned, other)

    round_id = await harness.start_round(
        retriever=RecordingMaterialRetriever(returns=(hit(other, 0, 0.9),))
    ).create(project_id=PROJECT, user_input=INPUT, material_ids=["m1"])

    round_ = harness.rounds.get(round_id)
    assert {ref.material_id for ref in round_.materials} == {"m1", "m2"}, (
        "自动检索进来的材料也要出现在这一轮（否则模型的上下文里没有它）"
    )
    eids = [str(ref.eid) for ref in round_.materials]
    assert len(set(eids)) == len(eids), "每块一个号，不许重号"


async def test_the_auto_materials_are_deduped_and_skipped_when_missing():
    """I：同一份材料命中两次只算一份；库里已经没有的材料（`get` 给 None）直接跳过。"""

    pinned = make_material("目标：导出要有权限", material_id="m1")
    other = make_material("权限：只给管理员", "权限：审计留痕", material_id="m2", title="权限.md")
    ghost = make_material("已经删掉了", material_id="m9", title="旧.md")
    harness = harness_with(pinned, other)  # 故意**不** seed m9

    round_id = await harness.start_round(
        retriever=RecordingMaterialRetriever(
            returns=(hit(other, 0, 0.9), hit(other, 1, 0.8), hit(ghost, 0, 0.7))
        )
    ).create(project_id=PROJECT, user_input=INPUT, material_ids=["m1"])

    round_ = harness.rounds.get(round_id)
    assert {ref.material_id for ref in round_.materials} == {"m1", "m2"}
    # 去重是**按材料**去掉重复的"材料"，不是只取一块：m2 的两块分都高，都该进来
    assert sum(1 for ref in round_.materials if ref.material_id == "m2") == 2


async def test_only_handpicked_materials_are_counted_in_missed():
    """I：自动材料**不**被说成"你勾了却没带进来"，它有自己的那句话。"""

    # 手勾的这份一块都装不下（单块就超预算）→ 它才是真正的 missed
    pinned = make_material("超" * 5000, material_id="m1")
    other = make_material("权限：只给管理员", material_id="m2", title="权限.md")
    harness = harness_with(pinned, other)

    round_id = await harness.start_round(
        retriever=RecordingMaterialRetriever(returns=(hit(other, 0, 0.9),))
    ).create(project_id=PROJECT, user_input=INPUT, material_ids=["m1"])

    round_ = harness.rounds.get(round_id)
    handles = [item.handle for item in round_.dropped]
    assert "材料 1 份" in handles, f"手勾的那份要留痕（实际：{handles}）"
    assert "自动检索 1 份" in handles, f"自动材料要单独留一句（实际：{handles}）"
    missed_reason = next(item.reason for item in round_.dropped if item.handle == "材料 1 份")
    assert "还有 1 份" in missed_reason and "0 份" in missed_reason


async def test_a_failing_retriever_falls_back_to_pinned_only():
    """I：检索器抛错 → 这一轮照起，只有手勾的材料，也不留"自动检索"的痕。"""

    pinned = make_material("目标：导出要有权限", material_id="m1")
    other = make_material("权限：只给管理员", material_id="m2", title="权限.md")
    harness = harness_with(pinned, other)

    round_id = await harness.start_round(retriever=_BoomRetriever()).create(
        project_id=PROJECT, user_input=INPUT, material_ids=["m1"]
    )

    round_ = harness.rounds.get(round_id)
    assert {ref.material_id for ref in round_.materials} == {"m1"}
    assert round_.dropped == ()


async def test_no_retriever_means_only_the_picked_materials():
    """R（回归锚点）：不给检索器 = 改前的行为（只用手勾的材料，不留痕）。"""

    pinned = make_material("目标：导出要有权限", material_id="m1")
    other = make_material("权限：只给管理员", material_id="m2", title="权限.md")
    harness = harness_with(pinned, other)

    round_id = await harness.start_round().create(
        project_id=PROJECT, user_input=INPUT, material_ids=["m1"]
    )

    round_ = harness.rounds.get(round_id)
    assert {ref.material_id for ref in round_.materials} == {"m1"}
    assert round_.dropped == ()


async def test_the_pinned_materials_are_never_squeezed_out_and_the_two_segments_share_one_budget():
    """I：手勾的一定在；**两段合计 + 收尾预留 ≤ 一个预算**。

    这是"收敛"最容易写坏的地方：第二段的预算若忘了减第一段用掉的（或忘了减
    `SECTION_OVERHEAD`），渲染就会超预算——那正是 `render_materials` 注释里
    "预算是假的"这句话的来历。
    """

    pinned = make_material(*["目标：导出要有权限。" * 20] * 10, material_id="m1")
    autos = [
        make_material("权限：" + "细" * 200, material_id=f"m{index}", title=f"权限{index}.md")
        for index in range(2, 8)
    ]
    harness = harness_with(pinned, *autos)

    round_id = await harness.start_round(
        retriever=RecordingMaterialRetriever(
            returns=tuple(hit(packet, 0, 1.0 - i * 0.01) for i, packet in enumerate(autos))
        )
    ).create(project_id=PROJECT, user_input=INPUT, material_ids=["m1"])

    round_ = harness.rounds.get(round_id)
    assert "m1" in {ref.material_id for ref in round_.materials}, "手勾的材料一定在"

    total = selection_cost(harness, round_)
    assert total + SECTION_OVERHEAD <= context.MATERIAL_BUDGET, (
        f"选块 {total} + 收尾 {SECTION_OVERHEAD} > 预算 {context.MATERIAL_BUDGET}"
    )


async def test_the_prompt_does_not_claim_auto_materials_were_dropped():
    """I（评审抓到的真 bug）：提示词**不许**把自动检索的材料说成"因预算没带进来"。

    `external_material_text` 数的是 `DropSource.MATERIAL`（= "勾了但装不下"）。
    自动材料混用同一个 source 的话，提示词会写出
    "另有 1 份材料因预算没带进来…这一轮没读"——而那份材料**恰恰读到了**。
    """

    pinned = make_material("目标：导出要有权限", material_id="m1")
    other = make_material("权限：只给管理员", material_id="m2", title="权限.md")
    harness = harness_with(pinned, other)

    round_id = await harness.start_round(
        retriever=RecordingMaterialRetriever(returns=(hit(other, 0, 0.9),))
    ).create(project_id=PROJECT, user_input=INPUT, material_ids=["m1"])

    prompt = external_material_text(harness.rounds.get(round_id), store=harness.materials)

    assert "因预算没带进来" not in prompt, prompt
    assert "只给管理员" in prompt, "自动材料的内容反而该在提示词里"


async def test_a_missing_handpicked_material_still_raises_shape_error():
    """I：开着检索器也一样——手勾的材料不存在要当场拒（`ShapeError`），不许静默降级。"""

    harness = harness_with(make_material("目标：导出要有权限", material_id="m1"))

    with pytest.raises(ShapeError):
        await harness.start_round(
            retriever=RecordingMaterialRetriever(returns=())
        ).create(project_id=PROJECT, user_input=INPUT, material_ids=["m404"])


async def test_a_web_origin_material_is_not_pulled_in_by_the_convergence_segment():
    """I（红线）：联网来源的材料**不进**材料段——它有另一条渲染路（一条结果一个号、`WEB_BUDGET`）。

    混进来的后果是"有号但提示词里一个字没有"：账本与 `Round.materials` 里有 E 号，
    `render_web_results` 却只渲染它认得的那一条。
    """

    pinned = make_material("目标：导出要有权限", material_id="m1")
    upload = make_material("权限：只给管理员", material_id="m2", title="权限.md")
    web_only = make_material("行业做法是 RBAC", material_id="m3", title="权限模型综述.md")
    web_only = replace(
        web_only, parsed=replace(web_only.parsed, origin="web_search")
    )
    harness = harness_with(pinned, upload, web_only)

    round_id = await harness.start_round(
        retriever=RecordingMaterialRetriever(
            returns=(hit(web_only, 0, 0.99), hit(upload, 0, 0.5))
        )
    ).create(project_id=PROJECT, user_input=INPUT, material_ids=["m1"])

    round_ = harness.rounds.get(round_id)
    selected = {ref.material_id for ref in round_.materials}
    assert "m3" not in selected, "联网结果不该走进材料段（红线：有号必在提示词里）"
    assert "m2" in selected, "上传材料照旧收敛进来"


async def test_an_auto_material_from_another_project_is_skipped():
    """I：检索器若没按项目过滤，跨项目材料也不许进来（与手勾那条硬闸同一口径）。"""

    pinned = make_material("目标：导出要有权限", material_id="m1")
    foreign = make_material("别的项目的东西", material_id="m9", title="别人的.md")
    foreign = replace(foreign, project_id="p-other")
    harness = harness_with(pinned, foreign)

    round_id = await harness.start_round(
        retriever=RecordingMaterialRetriever(returns=(hit(foreign, 0, 0.99),))
    ).create(project_id=PROJECT, user_input=INPUT, material_ids=["m1"])

    assert {ref.material_id for ref in harness.rounds.get(round_id).materials} == {"m1"}


async def test_the_round_digest_does_not_call_auto_materials_dropped():
    """I：记忆整理者读的那份摘要**不许**把自动材料列在"被丢掉的"下面。

    它进了 `memory_curator` 的提示词——写错就是把假事实记成记忆。
    """

    pinned = make_material("目标：导出要有权限", material_id="m1")
    other = make_material("权限：只给管理员", material_id="m2", title="权限.md")
    harness = harness_with(pinned, other)

    round_id = await harness.start_round(
        retriever=RecordingMaterialRetriever(returns=(hit(other, 0, 0.9),))
    ).create(project_id=PROJECT, user_input=INPUT, material_ids=["m1"])

    digest = context.render_round_digest(harness.rounds.get(round_id))

    assert "被丢掉的" not in digest, digest


async def test_seed_pins_handpicked_and_caps_their_share():
    """P2.4：手勾来源合计 ≤60%（长文不再把自动段饿死），并留下 SEED trace。"""

    pinned = make_material(
        *[f"第 {index} 段：导出权限说明" for index in range(100)], material_id="m1"
    )
    other = make_material("权限：只给管理员", material_id="m2", title="权限.md")
    harness = harness_with(pinned, other)

    round_id = await harness.start_round(
        retriever=RecordingMaterialRetriever(returns=(hit(other, 0, 0.9),))
    ).create(project_id=PROJECT, user_input="导出权限怎么定", material_ids=["m1"])

    round_ = harness.rounds.get(round_id)
    assert round_.pinned_materials == ("m1",)
    assert round_.retrievals and round_.retrievals[0].stage is RetrievalStage.SEED
    assert round_.retrievals[0].ok is True
    assert round_.retrievals[0].selected
    picked_ids = {ref.material_id for ref in round_.materials}
    assert "m2" in picked_ids, "自动段必须还有位置"
    assert len([ref for ref in round_.materials if ref.material_id == "m1"]) < 100
    assert selection_cost(harness, round_) <= 4000


async def test_no_retriever_means_no_seed_trace():
    """没注入检索器 = **没查**，不是零命中——不该写一条成功的 SEED trace。"""

    harness = harness_with(make_material("权限：管理员才能导出"))
    round_id = await harness.start_round().create(
        project_id=PROJECT, user_input="导出权限怎么定", material_ids=["m1"]
    )

    assert harness.rounds.get(round_id).retrievals == ()
