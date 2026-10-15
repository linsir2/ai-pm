"""把 03-案例与验收.md 案例 A 的领域部分走一遍：A2 → A9。

席位意见手写（模型在 P2 接），但**所有结构、状态迁移、编号、写入都走真代码**。
这是 P1 最强的一个测试：它证明领域层能表达真实案例，而不是只能通过玩具用例。
"""

from datetime import datetime

from pmstudio.domain.authoring.document import Document
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.claim import Assumption, Claim
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview
from pmstudio.domain.round.questionnaire import (
    Answer,
    AnswerSet,
    Option,
    Question,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.round import Round
from pmstudio.domain.services.assigner import EvidenceAssigner
from pmstudio.domain.services.committer import ProposalCommitter
from pmstudio.domain.services.scope_guard import ScopeGuard
from pmstudio.domain.services.traceability import TraceabilityChecker
from pmstudio.domain.shared.ids import (
    DocumentId,
    DraftId,
    EntryId,
    EvidenceId,
    FeatureSlug,
    FormId,
    ProjectId,
    ProposalId,
    QuestionId,
    RoundId,
)
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.builders import STD
from tests.support.fakes import (
    FakeTransaction,
    InMemoryDocumentRepository,
    InMemoryEvidenceRepository,
    InMemoryRoundRepository,
)

NOW = datetime(2026, 9, 22, 10, 0, 0)
PROJECT = ProjectId("p1")
ROUND_ID = RoundId("r1")
SEATS = (
    "seat.data_scale",
    "seat.role",
    "seat.frontend",
    "seat.business_rules",
)


def _card_answer(ledger: EvidenceLedger, question_id: str, text: str) -> EvidenceId:
    return ledger.record(
        kind=EvidenceKind.CARD_ANSWER,
        text=text,
        source=EvidenceSource(round_id=ROUND_ID, question_id=QuestionId(question_id)),
        now=NOW,
    )


def _proposal(
    proposal_id: str,
    entry_id: str,
    op: WriteOp,
    target: Target,
    text: str,
    origin_type: OriginType,
    refs: tuple[EvidenceId, ...],
    kind: BlockKind,
) -> Proposal:
    return Proposal(
        proposal_id=ProposalId(proposal_id),
        entry_id=EntryId(entry_id),
        op=op,
        target=target,
        text=text,
        origin=Origin(origin_type, refs, "见草案卡"),
        kind=kind,
    )


def test_case_a_from_input_to_version_two():
    ledger = EvidenceLedger.empty(PROJECT)
    documents = InMemoryDocumentRepository()
    rounds = InMemoryRoundRepository()
    evidences = InMemoryEvidenceRepository()
    transaction = FakeTransaction(documents, rounds, evidences)
    documents.seed(
        Document.new(
            document_id=DocumentId("doc1"), project_id=PROJECT, title="订单导出", now=NOW
        )
    )
    assigner = EvidenceAssigner()

    # —— A2 主张（Analyzer 的产出，这里手写）——
    raw = (
        "想做个订单导出的功能，运营每天都要用，最好能按时间筛选，数据量大概几万吧。"
        "另外登录页太丑了，顺便改一下，支持微信登录。月底必须上线。"
    )
    claims = (
        Claim("c1", "要做一个订单导出功能", ClaimLabel.NEED, Target.body("s5"), raw),
        Claim("c2", "运营每天都要用", ClaimLabel.FACT, Target.body("s3"), raw),
        Claim("c3", "最好能按时间筛选", ClaimLabel.NEED, Target.body("s5"), raw),
        Claim(
            "c4",
            "数据量大概几万",
            ClaimLabel.GUESS,
            Target.body("s10"),
            raw,
            assumption=Assumption(
                failure_consequence="若实际是百万级，同步导出会超时",
                verification="上线前用真实数据量压测一次",
            ),
        ),
        Claim("c5", "登录页太丑", ClaimLabel.PREFERENCE, Target.preference(), raw),
        Claim("c6", "登录页要改一下", ClaimLabel.NEED, Target.body("s5"), raw),
        Claim("c7", "支持微信登录", ClaimLabel.NEED, Target.body("s5"), raw),
        Claim("c8", "月底必须上线", ClaimLabel.CONSTRAINT, Target.body("s10"), raw),
    )
    claims = assigner.assign_claims(claims, ledger=ledger, round_id=ROUND_ID, now=NOW)
    by_claim = {claim.claim_id: claim.eid for claim in claims}

    # —— 建轮 → 开场卡 → 席位轮 ——
    round_ = Round.start(round_id=ROUND_ID, project_id=PROJECT, user_input=raw, now=NOW)
    round_.attach_claims(claims)
    round_.submit_opening(seats=SEATS)
    # 用户点名了两个新功能，授权在这里落下
    round_.authorize_new_feature(FeatureSlug("order-export"), basis=by_claim["c1"])
    round_.authorize_new_feature(FeatureSlug("login-wechat"), basis=by_claim["c6"])

    reviews = (
        SeatReview(
            seat_id=SEATS[0],
            basis="读了 s5、s8 与本轮 E4",
            opinions=(
                Opinion(
                    "O1",
                    OpinionForm.QUESTION,
                    "导出的数据量级",
                    "量级是同步与异步的分界，必须问用户",
                    "1 万以内可同步，10 万以上必须异步，是两套实现",
                    evidence_refs=(by_claim["c4"],),
                ),
                Opinion(
                    "O2",
                    OpinionForm.JUDGMENT,
                    "导出的性能上限",
                    "单次导出应在 60 秒内完成",
                    "量级在 1 万级，同步实现可满足；同类导出常见上限 60 秒",
                    evidence_refs=(by_claim["c4"],),
                ),
            ),
        ),
        SeatReview(
            seat_id=SEATS[1],
            basis="读了 s5、s7 与本轮 E1",
            opinions=(
                Opinion(
                    "O1",
                    OpinionForm.QUESTION,
                    "谁可以导出",
                    "导出文件与页面可见范围未必相同，必须问用户",
                    "订单数据含客户信息，导出即数据外流",
                    evidence_refs=(by_claim["c1"],),
                ),
                Opinion(
                    "O2",
                    OpinionForm.JUDGMENT,
                    "导出文件的字段范围",
                    "导出文件只含订单号与金额",
                    "订单数据含客户信息，默认按最小可见处理",
                    evidence_refs=(by_claim["c1"],),
                ),
            ),
        ),
        SeatReview(
            seat_id=SEATS[2],
            basis="读了 s5 与本轮 E7",
            opinions=(
                Opinion(
                    "O1",
                    OpinionForm.QUESTION,
                    "微信登录覆盖哪些端",
                    "端不同，实现不同",
                    "PC 扫码与移动端授权是两条链路",
                    evidence_refs=(by_claim["c7"],),
                ),
            ),
        ),
        SeatReview(
            seat_id=SEATS[3],
            basis="读了 s5 与本轮 E3",
            opinions=(
                Opinion(
                    "O1",
                    OpinionForm.QUESTION,
                    "时间筛选按哪个时间",
                    "创建时间与支付时间语义不同，必须问用户",
                    "运营对账口径取决于这个",
                    evidence_refs=(by_claim["c3"],),
                ),
                Opinion(
                    "O2",
                    OpinionForm.JUDGMENT,
                    "导出记录保留期",
                    "导出记录保留 30 天",
                    "同类系统的常见做法",
                    basis_note="纯专业判断，无用户依据",
                ),
            ),
        ),
    )
    for review in reviews:
        round_.attest(review)
    assignments = assigner.assign_opinions(reviews, ledger=ledger, round_id=ROUND_ID, now=NOW)
    # 疑问不占编号：全场 5 条疑问，只有 3 条判断拿到编号
    assert len(assignments) == 3
    proposed_eid = next(
        item.eid for item in assignments if item.seat_id == SEATS[3] and item.local_id == "O2"
    )

    # —— A5/A6 整合 → 问卷（5 题，正好一批）——
    questions = (
        Question(
            QuestionId("q1"),
            Target.body("s8"),
            QuestionType.SINGLE,
            "单次导出的最大行数是多少？",
            "量级决定同步还是异步",
            "按同步实现，超时风险由 PM 承担",
            (Option("a", "少于 1 万"), Option("b", "1 万–10 万"), Option("c", "超过 10 万")),
            (by_claim["c4"],),
            (SEATS[0], SEATS[2]),
        ),
        Question(
            QuestionId("q2"),
            Target.body("s8"),
            QuestionType.SINGLE,
            "一次导出最多能等多久？",
            "决定同步还是后台任务",
            "按 60 秒上限实现",
            (Option("a", "10 秒内"), Option("b", "1 分钟内"), Option("c", "可以等几分钟")),
            (by_claim["c4"],),
            (SEATS[0],),
        ),
        Question(
            QuestionId("q3"),
            Target.body("s3"),
            QuestionType.SINGLE,
            "谁能执行导出？",
            "权限边界只有你能定",
            "按所有登录用户可导出实现",
            (
                Option("a", "运营专员"),
                Option("b", "运营专员 + 主管"),
                Option("c", "所有登录用户"),
            ),
            (by_claim["c1"],),
            (SEATS[1],),
        ),
        Question(
            QuestionId("q4"),
            Target.body("s5"),
            QuestionType.SINGLE,
            "时间筛选按哪个时间？",
            "两个时间语义不同",
            "按创建时间实现",
            (Option("a", "创建时间"), Option("b", "支付时间"), Option("c", "两个都要")),
            (by_claim["c3"],),
            (SEATS[3],),
        ),
        Question(
            QuestionId("q5"),
            Target.body("s5"),
            QuestionType.SINGLE,
            "微信登录要覆盖哪些端？",
            "端不同，实现不同",
            "记录成 s11 开放问题（待决人：PM）",
            (Option("a", "只 PC"), Option("b", "PC + 移动"), Option("c", "还没想好")),
            (by_claim["c7"],),
            (SEATS[2],),
        ),
    )
    round_.integrate(
        questionnaire=Questionnaire(form_id=FormId("f1"), round_id=ROUND_ID, questions=questions)
    )

    # —— A7 作答（Q5 跳过）+ 回灌 ——
    answer_refs = {
        "q1": _card_answer(ledger, "q1", "1 万–10 万"),
        "q2": _card_answer(ledger, "q2", "1 分钟内"),
        "q3": _card_answer(ledger, "q3", "运营专员 + 主管"),
        "q4": _card_answer(ledger, "q4", "创建时间"),
    }
    skipped_ref = ledger.record(
        kind=EvidenceKind.SYSTEM_DEFAULT,
        text="微信登录的覆盖端未决定（待决人：PM）",
        source=EvidenceSource(
            round_id=ROUND_ID, rationale="Q5 被跳过，按题面后果落成开放问题"
        ),
        now=NOW,
    )
    round_.submit_answers(
        AnswerSet(
            form_id=FormId("f1"),
            answers=tuple(
                Answer(QuestionId(question_id), text)
                for question_id, text in (
                    ("q1", "1 万–10 万"),
                    ("q2", "1 分钟内"),
                    ("q3", "运营专员 + 主管"),
                    ("q4", "创建时间"),
                )
            ),
            skipped=(QuestionId("q5"),),
            answered_at=NOW,
        )
    )
    assert round_.seat_round_count == 2
    # 答过的题把 s8 纳入了本轮写范围
    assert Target.body("s8") in round_.write_scope

    # —— 缩编席位轮：数据规模席确认 60 秒上限 ——
    round_.attest(
        SeatReview(
            seat_id=SEATS[0],
            basis="读了本轮 E16（1 万–10 万）与 E17（1 分钟内）",
            opinions=(
                Opinion(
                    "O3",
                    OpinionForm.JUDGMENT,
                    "导出的性能上限",
                    "同步导出，单次 60 秒内完成；超过 5 万条提示分批",
                    "用户答了 1 万–10 万且可等 1 分钟，同步实现够用",
                    evidence_refs=(answer_refs["q1"], answer_refs["q2"]),
                ),
            ),
        )
    )
    second_round = assigner.assign_opinions(
        (round_.seat_reviews[-1],), ledger=ledger, round_id=ROUND_ID, now=NOW
    )
    perf_eid = second_round[0].eid

    # —— 草案：三组 ——
    draft = Draft(
        draft_id=DraftId("d1"),
        round_id=ROUND_ID,
        proposals=(
            _proposal(
                "p1",
                "en1",
                WriteOp.ADD,
                Target.feature_field("order-export", "one_liner", home=STD.feature_home),
                "运营按时间筛选并导出订单",
                OriginType.USER,
                (by_claim["c1"], by_claim["c3"]),
                BlockKind.TEXT,
            ),
            _proposal(
                "p2",
                "en2",
                WriteOp.ADD,
                Target.feature_field("order-export", "acceptance", home=STD.feature_home),
                "5 万条以内 60 秒可下载",
                OriginType.DERIVED,
                (perf_eid, answer_refs["q1"]),
                BlockKind.ACCEPTANCE,
            ),
            _proposal(
                "p3",
                "en3",
                WriteOp.ADD,
                Target.feature_field("order-export", "exceptions", home=STD.feature_home),
                "无导出权限的账号不显示导出入口",
                OriginType.DERIVED,
                (by_claim["c1"], answer_refs["q3"]),
                BlockKind.BULLET_LIST,
            ),
            _proposal(
                "p4",
                "en4",
                WriteOp.ADD,
                Target.body("s7", feature_slug=None),
                "导出文件只含订单号与金额，客户手机号不落盘",
                OriginType.DERIVED,
                (by_claim["c1"],),
                BlockKind.ENTITY,
            ),
            _proposal(
                "p5",
                "en5",
                WriteOp.ADD,
                Target.body("s10"),
                "月底必须上线（无缓冲）",
                OriginType.USER,
                (by_claim["c8"],),
                BlockKind.CONSTRAINT,
            ),
            _proposal(
                "p6",
                "en6",
                WriteOp.ADD,
                Target.body("s11"),
                "微信登录的覆盖端未决定（待决人：PM）",
                OriginType.SYSTEM_DEFAULT,
                (skipped_ref,),
                BlockKind.QUESTION,
            ),
            _proposal(
                "p7",
                "en7",
                WriteOp.ADD,
                Target.feature_field("order-export", "rules", home=STD.feature_home),
                "导出记录保留 30 天",
                OriginType.PROPOSED,
                (proposed_eid,),
                BlockKind.BULLET_LIST,
            ),
        ),
        gaps=("订单导出 · 名称", "订单导出 · 服务的目标"),
    )
    round_.integrate(draft=draft)
    assert [title for title, _ in draft.grouped()] == ["你说过的", "它替你判断的", "它提议的"]

    # —— A8/A9 确认写入：提议那条不勾 ——
    rounds.seed(round_)
    evidences.seed(ledger)
    committer = ProposalCommitter(
        documents=documents,
        writer=documents,
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        scope=ScopeGuard(),
        traceability=TraceabilityChecker(),
        uow=transaction,
    )
    accepted = tuple(ProposalId(f"p{n}") for n in range(1, 7))
    result = committer.commit(round_id=ROUND_ID, accepted=accepted, now=NOW)

    assert result.version.version_no == 2
    assert transaction.commits == 1
    # p4（s7）跑出了本轮去处 → 提示，但没被拒绝
    assert [item.target.label() for item in result.advisories] == ["s7"]
    stored = documents.get(PROJECT)
    feature = stored.feature(FeatureSlug("order-export"))
    assert feature is not None
    assert stored.field_block(FeatureSlug("order-export"), "acceptance") is not None
    assert stored.field_block(FeatureSlug("order-export"), "rules") is None  # 提议那条没写进去
    assert [gap.field for gap in stored.gaps()] != []
    assert stored.current_version.trigger.value == "confirm"

    # —— 最重要的一条：进正文的每条内容都能回溯到人 ——
    checker = TraceabilityChecker()
    for block in stored.blocks:
        for entry in block.entries:
            assert entry.refs, f"{entry.entry_id} 没有编号"
            if entry.entry_id == EntryId("en6"):
                continue  # s11 开放问题来自跳过的题（system_default），本来就回溯不到人
            traced = checker.check(entry.refs, ledger=evidences.get(PROJECT))
            assert traced.traceable, f"{entry.entry_id} 回溯不到用户输入：{traced.reason}"
