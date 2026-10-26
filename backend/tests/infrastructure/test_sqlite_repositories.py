from datetime import UTC, datetime

import pytest

from pmstudio.application.errors import LeaseLost, UnknownProject, UnknownRound
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.questionnaire import (
    Question,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.retrieval import ManualChunk
from pmstudio.domain.round.round import (
    DroppedItem,
    DropSource,
    MaterialRef,
    Round,
    SeatNomination,
)
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.round.web import WebRequest, WebRequestState
from pmstudio.domain.shared.ids import (
    DocumentId,
    EntryId,
    EvidenceId,
    FormId,
    ProjectId,
    QuestionId,
    RoundId,
)
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from pmstudio.infrastructure.sqlite import database, owner_scope, serialization
from pmstudio.infrastructure.sqlite.leases import SqliteLeaseStore
from pmstudio.infrastructure.sqlite.repositories import (
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteRoundRepository,
    SqliteWorkspaceIndex,
)
from pmstudio.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.support.builders import NOW, PROJECT, make_document, make_round
from tests.support.leases import bind_round_lease

#: 比 NOW 晚一个小时：用来验"最近更新倒序"（`Document.new(now=...)` 决定 `documents.updated_at`）
LATER = NOW.replace(hour=NOW.hour + 1)


@pytest.fixture()
def repos(tmp_path):
    connection = database.connect(tmp_path / "pmstudio.db")
    yield (
        SqliteDocumentRepository(connection),
        SqliteRoundRepository(connection),
        SqliteEvidenceRepository(connection),
        SqliteWorkspaceIndex(connection),
        SqliteUnitOfWork(connection),
    )
    connection.close()


def test_document_round_trips_blocks_versions_and_tombstones(repos):
    documents, _, _, _, uow = repos
    document = make_document()
    with uow:
        documents.save(document)

    loaded = documents.get(PROJECT)

    assert loaded.title == document.title
    assert [str(block.block_id) for block in loaded.blocks] == [
        str(block.block_id) for block in document.blocks
    ]
    assert loaded.current_version.version_no == document.current_version.version_no
    assert loaded.current_version.trigger is document.current_version.trigger
    assert loaded.view().sections == document.view().sections
    assert loaded.preference_entries == document.preference_entries


def test_document_versions_are_append_only(repos):
    documents, _, _, _, uow = repos
    with uow:
        documents.save(make_document())
    with uow:
        loaded = documents.get(PROJECT)
        loaded.apply(
            [
                WriteInstruction(
                    instruction_id="i9",
                    entry_id=EntryId("r1#99"),
                    op=WriteOp.ADD,
                    target=Target.body("s1"),
                    text="背景：运营每天手工导表",
                    refs=(EvidenceId("E1"),),
                    kind=BlockKind.TEXT,
                )
            ],
            now=NOW,
            summary="第二版",
        )
        documents.save(loaded)

    rows = documents.version_rows(PROJECT)
    assert [row["version_no"] for row in rows] == [1, 2]
    assert rows[0]["summary"] == "初始化 13 章骨架"  # 旧版本行没有被改写


def test_round_round_trips_every_field(repos):
    _, rounds, _, _, uow = repos
    round_ = make_round()
    round_.nominate(
        [
            SeatNomination(seat_id="seat.export_import", why="你说到导出"),
            SeatNomination(seat_id="seat.permissions", why="导出涉及数据外流"),
        ]
    )
    with uow:
        rounds.save(round_)

    loaded = rounds.get(RoundId("r1"))

    assert loaded.step is round_.step
    assert loaded.waiting_on is round_.waiting_on
    assert loaded.user_input == round_.user_input
    assert loaded.seats == round_.seats
    assert [str(claim.eid) for claim in loaded.claims] == [
        str(claim.eid) for claim in round_.claims
    ]
    assert loaded.claims[0].target == round_.claims[0].target
    assert loaded.claims[0].label is round_.claims[0].label
    assert loaded.created_at == round_.created_at
    assert loaded.seat_round_count == round_.seat_round_count
    assert [(item.seat_id, item.why) for item in loaded.nominated_seats] == [
        ("seat.export_import", "你说到导出"),
        ("seat.permissions", "导出涉及数据外流"),
    ]


def test_web_requests_and_step_selection_round_trip(repos):
    _, rounds, _, _, uow = repos
    round_ = make_round()
    round_.attach_web_requests(
        (
            WebRequest(
                request_id="w1",
                query="行业取数口径",
                why="要查行业通行做法",
                origin="analyzer",
                created_at=NOW,
            ),
            WebRequest(
                request_id="w2",
                query="配额上限",
                why="席位需要外部事实",
                origin="seat",
                seat_id="seat.data_scale",
                pass_index=1,
            ),
        )
    )
    round_.decide_web_request("w1", approve=True)
    round_.mark_web_request("w1", state=WebRequestState.DONE, result_note="2 条结果")
    round_.note_web_selection((("m1", "m1#c00"), ("m2", "m2#c00")))
    with uow:
        rounds.save(round_)

    loaded = rounds.get(round_.round_id)

    assert [
        (
            item.request_id,
            item.query,
            item.why,
            item.origin,
            item.seat_id,
            item.pass_index,
            item.state,
            item.result_note,
            item.created_at,
        )
        for item in loaded.web_requests
    ] == [
        (
            "w1",
            "行业取数口径",
            "要查行业通行做法",
            "analyzer",
            None,
            None,
            WebRequestState.DONE,
            "2 条结果",
            NOW,
        ),
        (
            "w2",
            "配额上限",
            "席位需要外部事实",
            "seat",
            "seat.data_scale",
            1,
            WebRequestState.PROPOSED,
            None,
            None,
        ),
    ]
    assert loaded.allow_web is False
    assert loaded.web_selection == (("m1", "m1#c00"), ("m2", "m2#c00"))


def test_empty_web_selection_round_trips_as_empty_not_missing(repos):
    """`()` = 这一趟一块都没选；读回来不能变成 `None`（旧 payload 才回退全部）。"""

    _, rounds, _, _, uow = repos
    round_ = make_round()
    round_.note_web_selection(())
    with uow:
        rounds.save(round_)

    loaded = rounds.get(round_.round_id)

    assert loaded.web_selection == ()


def test_round_payload_without_nominations_still_loads():
    """老行（没有 nominated_seats 键）必须读得回来：给空名单，不炸。"""

    payload = {
        "user_input": "想做个订单导出的功能",
        "claims": [],
        "seats": [],
        "seat_reviews": [],
        "questionnaire": None,
        "answer_sets": [],
        "draft": None,
        "seat_round_count": 0,
        "authorized_new_features": {},
        "failure": None,
        "confirmed_version_no": None,
    }

    round_ = serialization.round_from_payload(
        payload,
        round_id=RoundId("r1"),
        project_id=PROJECT,
        created_at=NOW,
        step=Step.STARTED,
        waiting_on=WaitingOn.NONE,
    )

    assert round_.nominated_seats == ()


def test_ledger_round_trips_and_keeps_next_number(repos):
    documents, _, evidences, _, uow = repos
    with uow:
        documents.save(make_document())  # 账本表有 FK：先有文档才有账本
    ledger = EvidenceLedger.empty(PROJECT)
    first = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="要做订单导出",
        source=EvidenceSource(round_id=RoundId("r1"), raw_span="想做个订单导出"),
        now=NOW,
    )
    with uow:
        evidences.save(ledger)

    loaded = evidences.get(PROJECT)
    second = loaded.record(
        kind=EvidenceKind.USER_INPUT,
        text="数据量几万",
        source=EvidenceSource(round_id=RoundId("r1"), raw_span="数据量大概几万"),
        now=NOW,
    )

    assert str(first) == "E1"
    assert str(second) == "E2"  # 重水合以后编号接着长，不复用
    assert loaded.get(first).text == "要做订单导出"


def test_workspace_index_reports_summary_and_latest_round(repos):
    documents, rounds, _, index, uow = repos
    with uow:
        documents.save(make_document())
        rounds.save(make_round())

    summary = index.summary(PROJECT)
    assert summary.title == "订单中台"
    assert summary.version_no == 1
    assert index.latest_round_id(PROJECT) == RoundId("r1")
    assert [item.project_id for item in index.list_summaries()] == ["p1"]


def test_list_summaries_puts_the_most_recently_updated_first(repos):
    """项目列表按**最近更新倒序**——前端门户的"最近更新"那一格直接取 `[0]`。

    这不是审美，是**数据契约**：`ProjectList` 的 `newestOf()` 假定第一个就是最新
    （不想为一个小数字把整份列表再遍历一遍）。后端哪天不排了，那一个数字会静默变错，
    所以在这里钉住。

    两个项目用不同 project_id（同一 id 会被 `GROUP BY` 合成一行），
    排序依据是仓储的 `updated_at`（`save` 用墙钟盖），所以断言**顺序**而不是具体时间。
    """

    documents, _, _, index, uow = repos
    with uow:
        documents.save(make_document(title="早期的项目"))
        documents.save(
            Document.new(
                document_id=DocumentId("doc2"),
                project_id=ProjectId("p2"),
                title="后来的项目",
                now=LATER,
            )
        )

    summaries = index.list_summaries()
    assert [item.title for item in summaries] == ["后来的项目", "早期的项目"]
    # 顺带钉住"倒序"是真的倒序：后存的那一行的 updated_at 不小
    assert summaries[0].updated_at >= summaries[1].updated_at


def test_recent_rounds_returns_newest_first_and_reads_the_tally(repos):
    """C4 要按项目看最近几轮：最新在前，没确认过的轮字段是 None（不编 0）。"""

    _, rounds, _, index, uow = repos
    confirmed = Round(
        round_id=RoundId("r1"),
        project_id=PROJECT,
        user_input="第一轮",
        created_at=NOW,
        step=Step.DONE,
        waiting_on=WaitingOn.NONE,
        confirmed_version_no=7,
        proposed_offered=1,
        proposed_accepted=0,
    )
    fresh = Round(
        round_id=RoundId("r2"),
        project_id=PROJECT,
        user_input="第二轮",
        created_at=NOW,
        step=Step.SEATS_RUNNING,
        waiting_on=WaitingOn.NONE,
    )
    with uow:
        rounds.save(confirmed)
        rounds.save(fresh)

    outcomes = index.recent_rounds(PROJECT, limit=5)

    assert [item.round_id for item in outcomes] == ["r2", "r1"]  # 最新在前
    assert outcomes[0].step == "seats_running"
    assert outcomes[0].proposed_offered is None  # 还没确认 → None
    assert (outcomes[1].proposed_offered, outcomes[1].proposed_accepted) == (1, 0)


def test_list_rounds_returns_newest_first_and_skips_broken_rows(tmp_path):
    """F4 的入口：轮次列表最新在前；payload 坏了的行跳过，不把整次查询打崩。"""

    connection = database.connect(tmp_path / "pmstudio.db")
    try:
        rounds = SqliteRoundRepository(connection)
        index = SqliteWorkspaceIndex(connection)
        uow = SqliteUnitOfWork(connection)
        with uow:
            rounds.save(
                Round(
                    round_id=RoundId("r1"),
                    project_id=PROJECT,
                    user_input="第一轮：想做个订单导出",
                    created_at=NOW,
                    step=Step.DONE,
                    waiting_on=WaitingOn.NONE,
                    confirmed_version_no=2,
                )
            )
            rounds.save(
                Round(
                    round_id=RoundId("r2"),
                    project_id=PROJECT,
                    user_input="第二轮：再加个权限",
                    created_at=NOW,
                    step=Step.CLAIMS_READY,
                    waiting_on=WaitingOn.OPENING,
                )
            )
        connection.execute(
            "INSERT INTO rounds (round_id, project_id, owner_id, step, waiting_on, payload,"
            " created_at, updated_at) VALUES ('r-bad', ?, ?, 'started', 'none',"
            " '{不是 JSON', ?, ?)",
            (
                str(PROJECT),
                owner_scope.current_owner_scope(),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )

        heads = index.list_rounds(PROJECT, limit=10)
    finally:
        connection.close()

    assert [head.round_id for head in heads] == ["r2", "r1"]   # 坏行不在列表里
    assert heads[0].claims_count == 0
    assert heads[0].user_input == "第二轮：再加个权限"
    assert heads[1].confirmed_version_no == 2


def test_list_rounds_respects_the_limit_and_the_project(repos):
    """只取这个项目的；limit 一眼看得出（与 `recent_rounds` 同形，但那是结果投影）。"""

    _, rounds, _, index, uow = repos
    with uow:
        for number in range(1, 4):
            rounds.save(
                Round(
                    round_id=RoundId(f"r{number}"),
                    project_id=PROJECT,
                    user_input=f"第 {number} 轮",
                    created_at=NOW,
                    step=Step.DONE,
                    waiting_on=WaitingOn.NONE,
                )
            )

    assert [head.round_id for head in index.list_rounds(PROJECT, limit=2)] == ["r3", "r2"]
    assert index.list_rounds(ProjectId("别的项目")) == ()


def test_material_references_tells_which_numbers_came_from_which_material(tmp_path):
    """"编号发出即冻结"的判据：这份材料被**哪些编号**引用过（P19）。

    真相源是轮次 payload（`materials[].material_id` + `eid`）——账本里的 `external_ref`
    是按标题拼的，两份同名文件会撞，不能当判据。
    """

    connection = database.connect(tmp_path / "pmstudio.db")
    try:
        rounds = SqliteRoundRepository(connection)
        index = SqliteWorkspaceIndex(connection)
        uow = SqliteUnitOfWork(connection)
        first = Round(
            round_id=RoundId("r1"),
            project_id=PROJECT,
            user_input="第一轮",
            created_at=NOW,
            step=Step.DONE,
            waiting_on=WaitingOn.NONE,
        )
        first.add_materials(
            (MaterialRef(material_id="m1", chunk_id="m1#c00", eid=EvidenceId("E4")),)
        )
        second = Round(
            round_id=RoundId("r2"),
            project_id=PROJECT,
            user_input="第二轮",
            created_at=NOW,
            step=Step.DONE,
            waiting_on=WaitingOn.NONE,
        )
        second.add_materials(
            (
                MaterialRef(material_id="m1", chunk_id="m1#c07", eid=EvidenceId("E9")),
                MaterialRef(material_id="m2", chunk_id="m2#c00", eid=EvidenceId("E10")),
            )
        )
        with uow:
            rounds.save(first)
            rounds.save(second)
        connection.execute(
            "INSERT INTO rounds (round_id, project_id, owner_id, step, waiting_on, payload,"
            " created_at, updated_at) VALUES ('r-bad', ?, ?, 'started', 'none',"
            " '{不是 JSON', ?, ?)",
            (
                str(PROJECT),
                owner_scope.current_owner_scope(),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )

        references = index.material_references(PROJECT)
    finally:
        connection.close()

    assert references == {"m1": ("E4", "E9"), "m2": ("E10",)}
    assert references.get("m3") is None            # 没被引用过 = 可以删 / 可以就地重解析


def test_material_references_ignores_other_projects(repos):
    _, rounds, _, index, uow = repos
    other = Round(
        round_id=RoundId("r-other"),
        project_id=ProjectId("p2"),
        user_input="别家的一轮",
        created_at=NOW,
        step=Step.DONE,
        waiting_on=WaitingOn.NONE,
    )
    other.add_materials(
        (MaterialRef(material_id="m9", chunk_id="m9#c00", eid=EvidenceId("E1")),)
    )
    with uow:
        rounds.save(other)

    assert index.material_references(PROJECT) == {}


def test_recent_rounds_respects_the_limit(repos):
    _, rounds, _, index, uow = repos
    with uow:
        for index_ in range(1, 4):
            rounds.save(
                Round(
                    round_id=RoundId(f"r{index_}"),
                    project_id=PROJECT,
                    user_input=f"第 {index_} 轮",
                    created_at=NOW,
                    step=Step.DONE,
                    waiting_on=WaitingOn.NONE,
                    confirmed_version_no=index_,
                )
            )

    assert len(index.recent_rounds(PROJECT, limit=2)) == 2


def test_repositories_bad_unknown_ids(repos):
    documents, rounds, evidences, _, _ = repos
    with pytest.raises(UnknownProject):
        documents.get(ProjectId("p9"))
    with pytest.raises(UnknownProject):
        evidences.get(ProjectId("p9"))
    with pytest.raises(UnknownRound):
        rounds.get(RoundId("r9"))


def test_unit_of_work_rolls_back_on_exception(repos):
    documents, _, _, _, uow = repos
    with pytest.raises(RuntimeError):
        with uow:
            documents.save(make_document())
            raise RuntimeError("模拟写一半炸了")

    with pytest.raises(UnknownProject):
        documents.get(PROJECT)  # 回滚干净：库里没有这个项目


def test_unit_of_work_is_reentrant_within_one_use_case(repos):
    """用例里调领域服务不算新事务：内层不再 BEGIN，成败由最外层定。

    手改文档就是这条路径（`EditEntry` → `ProposalCommitter.apply_manual`）。
    """

    documents, _, _, _, uow = repos
    with pytest.raises(RuntimeError):
        with uow:
            with uow:  # 领域服务的那一层
                documents.save(make_document())
            raise RuntimeError("用例后半段炸了")

    with pytest.raises(UnknownProject):
        documents.get(PROJECT)  # 整段回滚


def test_summary_updated_at_comes_from_the_database(repos):
    documents, _, _, index, uow = repos
    with uow:
        documents.save(make_document())
    summary = index.summary(PROJECT)
    assert isinstance(summary.updated_at, datetime)
    assert summary.updated_at.tzinfo == UTC


def test_legacy_questionnaire_dropped_migrates_into_round_dropped():
    """老库里留痕挂在问卷上（裸字符串）：读回来变成 `Round.dropped` 里的 assembly 条目。

    P5b 把留痕收口成一条通道（`04` C5）：旧行不许炸，历史留痕也不许静默丢。
    """

    round_ = make_round()
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f1"),
            round_id=round_.round_id,
            questions=(
                Question(
                    question_id=QuestionId("q1"),
                    target=Target.body("s8"),
                    type=QuestionType.TEXT,
                    title="单次导出的最大行数是多少？",
                    why="量级决定同步还是异步",
                    consequence_if_skipped="按同步实现",
                ),
            ),
        )
    )
    payload = serialization.round_to_payload(round_)
    payload.pop("dropped", None)  # 假装这是新字段出现之前存下的行
    payload["questionnaire"]["dropped"] = ["seat.nope:O1"]

    loaded = serialization.round_from_payload(
        payload,
        round_id=round_.round_id,
        project_id=round_.project_id,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )

    assert [(item.handle, item.source) for item in loaded.dropped] == [
        ("seat.nope:O1", DropSource.ASSEMBLY)
    ]


def test_question_blocking_round_trips_and_defaults_to_false(repos):
    _, rounds, _, _, uow = repos
    round_ = make_round()
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f1"),
            round_id=round_.round_id,
            questions=(
                Question(
                    question_id=QuestionId("q1"),
                    target=Target.body("s8"),
                    type=QuestionType.TEXT,
                    title="单次导出的最大行数是多少？",
                    why="量级决定同步还是异步",
                    consequence_if_skipped="按同步实现",
                    blocking=True,
                ),
            ),
        )
    )
    with uow:
        rounds.save(round_)

    loaded = rounds.get(round_.round_id)
    assert loaded.questionnaire is not None
    assert loaded.questionnaire.questions[0].blocking is True

    # 老 payload（没有 blocking 键）读回来必须是 False
    payload = serialization.questionnaire_to_json(loaded.questionnaire)
    payload["questions"][0].pop("blocking", None)
    assert serialization.questionnaire_from_json(payload).questions[0].blocking is False


def test_round_dropped_items_round_trip(repos):
    """留痕要跟着轮次落库、读回来一条不差（P5b Task 1）。"""

    _, rounds, _, _, uow = repos
    round_ = make_round()
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f1"),
            round_id=round_.round_id,
            questions=(
                Question(
                    question_id=QuestionId("q1"),
                    target=Target.body("s8"),
                    type=QuestionType.TEXT,
                    title="单次导出的最大行数是多少？",
                    why="量级决定同步还是异步",
                    consequence_if_skipped="按同步实现",
                ),
            ),
        ),
        dropped=(
            DroppedItem(
                handle="seat.nope:O1",
                reason="归因对不上号：这一轮没有这条席位条目",
                source=DropSource.ASSEMBLY,
                seat_id=None,
                pass_index=1,
            ),
            DroppedItem(
                handle="seat.data_scale:O2",
                reason="与文档已有内容重复",
                source=DropSource.INTEGRATOR,
                seat_id="seat.data_scale",
                pass_index=1,
            ),
        ),
    )
    with uow:
        rounds.save(round_)

    loaded = rounds.get(round_.round_id)
    assert [
        (item.handle, item.reason, item.source, item.seat_id, item.pass_index)
        for item in loaded.dropped
    ] == [
        (
            "seat.nope:O1",
            "归因对不上号：这一轮没有这条席位条目",
            DropSource.ASSEMBLY,
            None,
            1,
        ),
        (
            "seat.data_scale:O2",
            "与文档已有内容重复",
            DropSource.INTEGRATOR,
            "seat.data_scale",
            1,
        ),
    ]


def test_round_payload_without_dropped_key_still_loads():
    """老行里没有 `dropped` 键：读成空元组，不炸（与 nominated_seats 同一条规矩）。"""

    round_ = make_round()
    payload = serialization.round_to_payload(round_)
    payload.pop("dropped")

    loaded = serialization.round_from_payload(
        payload,
        round_id=round_.round_id,
        project_id=round_.project_id,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )

    assert loaded.dropped == ()


def test_abandon_reason_round_trips(repos):
    """放弃这一轮的原因要跟着轮次落库、读回来一致（`01` F15）。"""

    _, rounds, _, _, uow = repos
    round_ = make_round()
    round_.abandon("用户不想等了")
    with uow:
        rounds.save(round_)

    loaded = rounds.get(round_.round_id)
    assert loaded.step is Step.ABANDONED
    assert loaded.abandon_reason == "用户不想等了"


def test_round_payload_without_abandon_key_still_loads():
    """老行里没有这个键：读成 None，不炸。"""

    round_ = make_round()
    payload = serialization.round_to_payload(round_)
    payload.pop("abandon_reason")

    loaded = serialization.round_from_payload(
        payload,
        round_id=round_.round_id,
        project_id=round_.project_id,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )

    assert loaded.abandon_reason is None


def test_round_payload_skipped_seats_round_trip():
    round_ = make_round(seats=("seat.a", "seat.b"))
    round_.skip_seat("seat.b")

    payload = serialization.round_to_payload(round_)
    loaded = serialization.round_from_payload(
        payload,
        round_id=round_.round_id,
        project_id=round_.project_id,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )

    assert loaded.skipped_seats == ("seat.b",)
    assert loaded.pending_seats == ("seat.a",)


def test_round_payload_without_skipped_seats_key_still_loads():
    """老行（没有 skipped_seats 键）必须读得回来：给空，pending 不变。"""

    round_ = make_round(seats=("seat.a", "seat.b"))
    payload = serialization.round_to_payload(round_)
    payload.pop("skipped_seats", None)  # 假装这是新字段出现之前存下的行

    loaded = serialization.round_from_payload(
        payload,
        round_id=round_.round_id,
        project_id=round_.project_id,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )

    assert loaded.skipped_seats == ()
    assert loaded.pending_seats == ("seat.a", "seat.b")


def test_material_references_counts_pinned_and_manual_chunks(tmp_path):
    """P2.0：还没投影、没有 E 号的"手勾/手动带料"也算引用——不许删、不许就地重解析。"""

    connection = database.connect(tmp_path / "pmstudio.db")
    try:
        rounds = SqliteRoundRepository(connection)
        index = SqliteWorkspaceIndex(connection)
        uow = SqliteUnitOfWork(connection)
        round_ = Round(
            round_id=RoundId("r-pinned"),
            project_id=PROJECT,
            user_input="带料",
            created_at=NOW,
            step=Step.DONE,
            waiting_on=WaitingOn.NONE,
        )
        round_.pin_materials(("m7",))
        round_.note_manual_chunks((ManualChunk("m8", "m8#c00"),))
        with uow:
            rounds.save(round_)

        references = index.material_references(PROJECT)
    finally:
        connection.close()

    assert references.get("m7") == ("手勾",), "手勾材料必须算被引用"
    assert references.get("m8") == ("手动带料",), "手动带进的块必须算被引用"


def test_round_save_is_fenced_by_the_current_lease(tmp_path):
    """仓储 fencing：新 INSERT 允许；existing 行没有活租约就写不进去（P3b Task 1）。"""

    connection = database.connect(tmp_path / "pmstudio.db")
    try:
        rounds = SqliteRoundRepository(connection)
        round_ = make_round()
        rounds.save(round_)  # INSERT：还没有出租，允许

        with pytest.raises(LeaseLost):
            rounds.save(round_)  # existing + 无 token

        token = bind_round_lease(connection, round_.round_id)
        rounds.save(round_)  # 有匹配的活租约

        SqliteLeaseStore(connection).release(
            token.kind, token.target_id, token.user_id, token.holder, token.token
        )
        with pytest.raises(LeaseLost):
            rounds.save(round_)  # 释放后的迟到写
    finally:
        connection.close()
