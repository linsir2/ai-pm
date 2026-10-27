"""记忆命中遥测：纯函数（现算，不落库）。

口径与生产同一处：窗口常量从 `domain/memory/memory.py` import（`MEMORY_USAGE_WINDOW`），
本脚本不另抄一份；判据是"交集非空即命中"（与 `unused_entries` 同一判据），
只不过这里报的是**每条命中了几轮**（unsigned → 抽屉标只看 0 / 非 0），
而不是"谁是 0"。
"""

from datetime import datetime

from scripts.telemetry import MEMORY_USAGE_WINDOW, memory_hit_counts, scan_memory_hits

from pmstudio.domain.memory.memory import (
    MEMORY_USAGE_WINDOW as PRODUCTION_WINDOW,
)
from pmstudio.domain.memory.memory import MemoryEntry, ProjectMemory
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.questionnaire import Question, Questionnaire, QuestionType
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.ids import (
    DraftId,
    EntryId,
    EvidenceId,
    FormId,
    ProjectId,
    ProposalId,
    QuestionId,
    RoundId,
)
from pmstudio.domain.shared.memory import MemoryChange, MemoryKind, MemoryOp
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp
from pmstudio.infrastructure.sqlite.repositories import (
    SqliteMemoryRepository,
    SqliteRoundRepository,
)
from tests.support.doubles import FixedClock, SeqIds
from tests.support.postgres import direct_url, open_direct

NOW = datetime(2026, 10, 1)


def _entry(memory_id, refs=("E1",)):
    return MemoryEntry(
        memory_id=memory_id, kind=MemoryKind.LESSON, text="不要做多租户",
        refs=tuple(EvidenceId(r) for r in refs), round_id="r0",
        created_at=NOW, updated_at=NOW,
    )


def test_window_constant_is_the_production_one_not_a_local_copy():
    """P：脚本里另抄一份数字，两边各说各话——只认 `memory.py` 的常量。"""
    assert MEMORY_USAGE_WINDOW == PRODUCTION_WINDOW == 10


def test_never_cited_entry_scores_zero():
    """R：最近 10 轮里一次没交代过它的编号 → 0（抽屉里就是「久未使用」）。"""
    entries = (_entry("mem-1", refs=("E3",)),)
    cited = {"r-1": frozenset({"E1"}), "r-2": frozenset({"E2"})}

    assert memory_hit_counts(entries, cited) == {"mem-1": 0}


def test_hits_count_the_rounds_where_its_refs_were_cited():
    """R：E3 在两轮里被交代过 → 2；交集非空即命中（与 `unused_entries` 同判据）。"""
    entries = (_entry("mem-1", refs=("E3",)),)
    cited = {
        "r-1": frozenset({"E3"}),
        "r-2": frozenset({"E9"}),
        "r-3": frozenset({"E3", "E9"}),
    }

    assert memory_hit_counts(entries, cited) == {"mem-1": 2}


def test_only_the_last_window_rounds_count():
    """I：窗口含端点——第 11 轮之前的引用不算，当轮引用才算。"""
    entries = (_entry("mem-1", refs=("E3",)),)
    order = [f"r-{i}" for i in range(11)]
    cited = {**{"r-0": frozenset({"E3"})}, **{f"r-{i}": frozenset() for i in range(1, 11)}}

    assert memory_hit_counts(entries, cited, order=order) == {"mem-1": 0}


def test_no_entries_means_nothing_to_report():
    assert memory_hit_counts((), {}) == {}


def test_scan_reads_real_rows_and_counts_hits_per_entry():
    """R：真库行 → 命中表：交代过 E1 的那条 1 轮命中，没人交代 E9 的那条 0（= 久未使用）。

    走真仓储落库、真序列化读回——与 `--memory-hits` 线上跑的是同一条路。
    """
    connection = open_direct()
    clock, ids = FixedClock(), SeqIds()
    pid = ProjectId("p-scan")
    memory = ProjectMemory.empty(pid).apply(
        (
            MemoryChange(
                op=MemoryOp.ADD, kind=MemoryKind.DECISION,
                text="导出保留期是 30 天", refs=("E1",),
            ),
            MemoryChange(
                op=MemoryOp.ADD, kind=MemoryKind.DECISION,
                text="导出的列顺序固定", refs=("E9",),
            ),
        ),
        round_id="r0",
        known_refs=frozenset({"E1", "E9"}),
        now=clock.now(),
        ids=ids,
    )
    SqliteMemoryRepository(connection).save(memory)
    rid = RoundId("r1")
    SqliteRoundRepository(connection).save(
        Round(
            round_id=rid,
            project_id=pid,
            user_input="想做个订单导出的功能",
            created_at=clock.now(),
            draft=Draft(
                draft_id=DraftId("d-r1"),
                round_id=rid,
                proposals=(
                    Proposal(
                        proposal_id=ProposalId("p-r1"),
                        entry_id=EntryId("en-1"),
                        op=WriteOp.REMOVE,
                        target=Target.body("s5"),
                        text="导出记录保留 30 天",
                        origin=Origin(
                            OriginType.DERIVED, (EvidenceId("E1"),), "席位判断"
                        ),
                    ),
                ),
            ),
            questionnaire_history=(
                Questionnaire(
                    form_id=FormId("f1"),
                    round_id=rid,
                    questions=(
                        Question(
                            question_id=QuestionId("q1"),
                            target=Target.body("s5"),
                            type=QuestionType.TEXT,
                            title="导出保留期定多久？",
                            why="落 s5 的保留口径",
                            consequence_if_skipped="落成开放问题",
                            evidence_refs=(EvidenceId("E2"),),
                        ),
                    ),
                ),
            ),
        )
    )
    connection.close()

    hits = scan_memory_hits(direct_url())[str(pid)]
    by_text = {entry.text: entry.memory_id for entry in memory.entries}

    assert hits[by_text["导出保留期是 30 天"]] == 1
    assert hits[by_text["导出的列顺序固定"]] == 0
