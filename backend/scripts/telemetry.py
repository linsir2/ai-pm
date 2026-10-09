"""消费遥测：测量"注入给整合者的上下文里，模型实际引用了哪些"。

只读测量，不改生产路径。用途：给记忆层重构（`.codex/docs/设计/07-记忆层.md` §7.5
第 1 步）提供"注入了却从没被引"的证据。

口径：
- **注入** = 整合者这一轮看到的、按来源分槽的 E 编号。从 Round + 账本**确定性重算**，
  不依赖 `model_calls.prompt_text`（它被截断到 20000 字符，中间丢了）。
- **引用** = 提案 refs ∪ 题目 refs（复用 `assembly.cited_eids`，与生产同口径）。

**警告：低引用率 ≠ 没用。** 模型可能"用了但没引"（记忆影响判断却不进 refs）。
本工具是**筛选器**，不是判决：高引用 = 确定用了；低引用 = 要么隐式用了、要么真没用，
需要再看留样分辨。

**测不了**：L2 观察 / L3 灵感 / L4 摘要 / M6 记忆——它们没有 E 编号，模型"引不了"
（这正是 `07` §7 想法⑤要解决的）。

用法：
    cd backend && uv run python scripts/telemetry.py              # 扫 .codex/runs/**/*.db
    cd backend && uv run python scripts/telemetry.py --db 库路径   # 只扫一个库
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "src"))
sys.path.insert(0, str(BACKEND_ROOT))

from pmstudio.application.assembly import cited_eids  # noqa: E402
from pmstudio.application.context import answer_eids  # noqa: E402
from pmstudio.domain.provenance.ledger import EvidenceLedger  # noqa: E402
from pmstudio.domain.round.round import Round  # noqa: E402
from pmstudio.domain.shared.ids import EvidenceId, RoundId  # noqa: E402
from pmstudio.infrastructure.sqlite.repositories import (  # noqa: E402
    SqliteEvidenceRepository,
    SqliteRoundRepository,
)

#: 有 E 编号、且互不重叠的槽（其余记忆层没有编号，测不了）。
SLOTS = ("claims", "answers", "reviews", "materials")

DEFAULT_RUNS_GLOB = ".codex/runs/**/*.db"

#: (轮号, 该轮各槽覆盖率)
MeasuredRounds = tuple[tuple[RoundId, tuple["SlotCoverage", ...]], ...]


@dataclass(frozen=True)
class SlotCoverage:
    slot: str
    injected: int
    cited: int

    @property
    def rate(self) -> float:
        return self.cited / self.injected if self.injected else 0.0


def injected_by_slot(
    round_: Round, *, ledger: EvidenceLedger
) -> dict[str, frozenset[EvidenceId]]:
    """整合者这一轮看到的、按来源分槽的 E 编号。确定性重算，不看 prompt_text。

    四个槽互不重叠：claims=USER_INPUT / answers=CARD_ANSWER / reviews=AGENT_* /
    materials=EXTERNAL。reviews 只算**判断自己的编号**（不算它引用了谁——那会和别的槽重叠）。
    """

    answers = answer_eids(ledger=ledger, round_id=round_.round_id)
    reviews: set[EvidenceId] = set()
    for review in round_.current_reviews:
        for opinion in review.opinions:
            own = ledger.find_by_local(
                seat_id=review.seat_id,
                local_id=f"p{review.pass_index}:{opinion.local_id}",
                round_id=round_.round_id,
            )
            if own is not None:
                reviews.add(own.eid)
    return {
        "claims": frozenset(claim.eid for claim in round_.claims),
        "answers": frozenset(EvidenceId(eid) for eid in answers.values()),
        "reviews": frozenset(reviews),
        "materials": frozenset(ref.eid for ref in round_.materials),
    }


def coverage(round_: Round, *, ledger: EvidenceLedger) -> tuple[SlotCoverage, ...]:
    """每个槽的 (注入数, 被引用数)。引用 = 提案 refs ∪ 题目 refs（整轮所有批次）。"""

    assert round_.draft is not None  # 前置条件：调用方（analyze_db）只喂有草案的轮
    questions = tuple(
        question for form in round_.questionnaire_history for question in form.questions
    )
    cited = cited_eids(round_.draft.proposals, questions)
    return tuple(
        SlotCoverage(slot, len(ids), len(ids & cited))
        for slot, ids in injected_by_slot(round_, ledger=ledger).items()
    )


def analyze_db(path: Path) -> tuple[MeasuredRounds, int]:
    """只读扫库：每个**有草案**的轮 → coverage；坏轮跳过（不炸整轮扫描）。

    返回 `(测到的轮, 跳过的轮数)`。跳过 = 没草案（半截/失败轮）或读不回来的老行。
    """

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rounds = SqliteRoundRepository(connection)
        evidences = SqliteEvidenceRepository(connection)
        measured: list[tuple[RoundId, tuple[SlotCoverage, ...]]] = []
        skipped = 0
        for (raw_id,) in connection.execute("SELECT round_id FROM rounds ORDER BY rowid"):
            try:
                round_ = rounds.get(RoundId(raw_id))
                if round_.draft is None:
                    skipped += 1
                    continue
                ledger = evidences.get(round_.project_id)
                measured.append((round_.round_id, coverage(round_, ledger=ledger)))
            except Exception:  # noqa: BLE001 —— 半截轮/老库：跳过，不炸整轮扫描
                skipped += 1
        return tuple(measured), skipped
    finally:
        connection.close()


def _print_report(label: str, measured: MeasuredRounds) -> None:
    print(f"\n=== {label} ===")
    if not measured:
        print("  （没有可测的轮）")
        return
    for round_id, slots in measured:
        row = "  ".join(
            f"{c.slot} {c.cited}/{c.injected}"
            + (f"({c.rate:.0%})" if c.injected else "(-)")
            for c in slots
        )
        print(f"  {round_id}: {row}")


def main() -> int:
    parser = argparse.ArgumentParser(description="消费遥测（只读）")
    parser.add_argument(
        "--db", default=None, help="只扫这一个库（默认扫 .codex/runs/**/*.db）"
    )
    args = parser.parse_args()

    paths = (
        [Path(args.db)]
        if args.db
        else sorted(BACKEND_ROOT.parent.glob(DEFAULT_RUNS_GLOB))
    )
    if not paths:
        print("没有找到库", file=sys.stderr)
        return 2

    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    measured_rounds = 0
    skipped_rounds = 0
    for path in paths:
        try:
            measured, skipped = analyze_db(path)
        except sqlite3.Error as error:
            print(f"\n=== {path.name} ===\n  跳过：{error}")
            continue
        measured_rounds += len(measured)
        skipped_rounds += skipped
        _print_report(path.name, measured)
        for _round_id, slots in measured:
            for cell in slots:
                totals[cell.slot][0] += cell.injected
                totals[cell.slot][1] += cell.cited

    print(
        f"\n######## 全库汇总（{len(paths)} 库 · 测 {measured_rounds} 轮 · "
        f"跳过 {skipped_rounds}）########"
    )
    for slot in SLOTS:
        injected, cited = totals[slot]
        rate = f"{cited / injected:.0%}" if injected else "—"
        print(f"  {slot:<10} 注入 {injected:>6}  引用 {cited:>6}  {rate:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
