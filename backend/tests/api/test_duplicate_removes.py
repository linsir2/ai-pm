"""端到端（HTTP 层）：**老草案**里带着两条重复 `remove`，确认写入也得成功。

装配层已经不让重复的 `remove` 进新卡片了（`draft_of` 收口）。但**修好之前存下来的草案**
是原样存在库里的（`serialization.py` 不去重），用户点"确认写入"时它们照样会到写入端——
这一条测的就是那条路：**API 只该看到 200 与版本 +1**，而不是
`422 shape_error: 找不到条目`（P30 · F28 的真跑现场）。

（为什么不用 `tests/e2e`：那边的草案是脚本化模型现跑出来的，**再也造不出重复**了——
要测"老草案"就得直接把那份草案写进仓储，这是这条用例唯一能忠实复现现场的搭法。）
"""

from __future__ import annotations

from pmstudio.api.app import create_app
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.round.claim import Claim
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.ids import (
    DraftId,
    EntryId,
    EvidenceId,
    ProjectId,
    ProposalId,
    RoundId,
)
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.api.test_routes import _services
from tests.support.api_client import async_client
from tests.support.harness import Harness

ENTRY = "en-dup"


def remove(proposal_id: str, *, text: str) -> Proposal:
    return Proposal(
        proposal_id=ProposalId(proposal_id),
        entry_id=EntryId(ENTRY),
        op=WriteOp.REMOVE,
        target=Target.body("s2"),
        text=text,
        origin=Origin(OriginType.SYSTEM_DEFAULT, (EvidenceId("E1"),), None),
    )


def seed_round_with_duplicate_removes(harness: Harness, project_id: str) -> str:
    """把"修好之前存下来的那种草案"直接写进仓储：同一条目两条 `remove`。"""

    now = harness.clock.now()
    round_id = RoundId("r-dup")
    round_ = Round.start(
        round_id=round_id,
        project_id=ProjectId(project_id),
        user_input="把这一轮答过的未决项撤下",
        now=now,
    )
    round_.attach_claims(
        (
            Claim(
                claim_id="c1",
                text="撤下答过的未决项",
                label=ClaimLabel.NEED,
                target=Target.body("s11"),
                raw_span="撤下未决项",
                eid=EvidenceId("E1"),
            ),
        )
    )
    round_.submit_opening(seats=("seat.data_scale",))
    round_.integrate(
        draft=Draft(
            draft_id=DraftId("d-dup"),
            round_id=round_id,
            proposals=(
                remove("p01", text="已经把想法整理成能交付的文档"),
                remove("p02", text="本轮已回答，撤下这条未决项"),
            ),
        )
    )
    harness.rounds.seed(round_)
    return str(round_id)


async def test_an_old_draft_with_duplicate_removes_still_confirms():
    harness = Harness(replies={}, seat_replies={})
    app = create_app(_services(harness))

    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]

        # 铺一条可被撤下的内容（走完整闭环太慢，它不是被测对象）
        document = harness.documents.get(ProjectId(project_id))
        document.apply(
            [
                WriteInstruction(
                    instruction_id="seed",
                    entry_id=EntryId(ENTRY),
                    op=WriteOp.ADD,
                    target=Target.body("s2"),
                    text="已经把想法整理成能交付的文档",
                    refs=(EvidenceId("E1"),),
                    kind=BlockKind.TEXT,
                )
            ],
            now=harness.clock.now(),
            summary="铺一条",
        )
        harness.documents.seed(document)

        # 铺那条已经是一版（v2），所以确认之后该是 v3——**别把这一步算漏**
        # （第一版用例就是这么写错的：写成 2，红在版本号上，而不是红在 422 上）
        before = harness.documents.get(ProjectId(project_id)).current_version.version_no
        round_id = seed_round_with_duplicate_removes(harness, project_id)
        response = await client.post(
            f"/api/rounds/{round_id}/confirm", json={"accepted": ["p01", "p02"]}
        )

    assert response.status_code == 200, response.text
    assert response.json()["version_no"] == before + 1
    assert response.json()["round"]["step"] == "done"


async def test_confirming_only_the_duplicate_still_works():
    harness = Harness(replies={}, seat_replies={})
    app = create_app(_services(harness))

    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        document = harness.documents.get(ProjectId(project_id))
        document.apply(
            [
                WriteInstruction(
                    instruction_id="seed",
                    entry_id=EntryId(ENTRY),
                    op=WriteOp.ADD,
                    target=Target.body("s2"),
                    text="已经把想法整理成能交付的文档",
                    refs=(EvidenceId("E1"),),
                    kind=BlockKind.TEXT,
                )
            ],
            now=harness.clock.now(),
            summary="铺一条",
        )
        harness.documents.seed(document)
        before = harness.documents.get(ProjectId(project_id)).current_version.version_no
        round_id = seed_round_with_duplicate_removes(harness, project_id)

        response = await client.post(
            f"/api/rounds/{round_id}/confirm", json={"accepted": ["p02"]}
        )

    assert response.status_code == 200, response.text
    assert response.json()["version_no"] == before + 1
