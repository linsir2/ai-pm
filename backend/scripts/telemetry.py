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

本脚本还有**第二件**测量（`--memory`）：三层记忆的水位读数（行数 + 最旧 `created_at` + 是否
超 `L*_CAPACITY`）。那是**上下文里看不见的那一半**——提示词只带最近 5 条，其余的"还在库里"
这件事只有遥测能说。水位**只报不删**（`02` 的三层生命周期口径：删除只有用户能触发），
所以这里一律 `mode=ro` 开库。

 用法：
     cd backend && uv run python scripts/telemetry.py                       # 扫 .codex/runs/**/*.db
     cd backend && uv run python scripts/telemetry.py --db 库路径            # 只扫一个库
     cd backend && uv run python scripts/telemetry.py --db 库路径 --memory   # 只打三层水位表
     cd backend && uv run python scripts/telemetry.py --db 库路径 --memory-hits  # 只打记忆命中表
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "src"))
sys.path.insert(0, str(BACKEND_ROOT))

from pmstudio.application.assembly import cited_eids  # noqa: E402
from pmstudio.application.context import (  # noqa: E402
    L2_CAPACITY,
    L3_CAPACITY,
    L4_CAPACITY,
    answer_eids,
)
from pmstudio.domain.memory.memory import (  # noqa: E402
    MEMORY_USAGE_WINDOW,
    MemoryEntry,
)
from pmstudio.domain.provenance.ledger import EvidenceLedger  # noqa: E402
from pmstudio.domain.round.round import Round  # noqa: E402
from pmstudio.domain.shared.ids import EvidenceId, ProjectId, RoundId, UserId  # noqa: E402
from pmstudio.harness.model.prompt_log import TRUNCATION_MARK  # noqa: E402
from pmstudio.infrastructure.sqlite.repositories import (  # noqa: E402
    SqliteEvidenceRepository,
    SqliteMemoryRepository,
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


def real_prompt_length(prompt_text: str) -> int:
    """`model_calls.prompt_text` 的**真实长度**（口径，别再量错）。

    留样被 `keep_head_and_tail` 截成"头尾各半 + 中间标记"，直接 `len()` 会把所有大提示词
    压成 20,017 的假平原。标记**整段**（含外层换行）都是留样新加的、不属于原文；
    真实长度 = 存储长度 − 标记整段 + 标记里写的略去数。
    """

    found = re.search(r"…（略去 (\d+) 字符）…", prompt_text)
    if found is None:
        return len(prompt_text)
    omitted = int(found.group(1))
    whole_mark = TRUNCATION_MARK.format(count=omitted)
    return len(prompt_text) - len(whole_mark) + omitted


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


# ── 三层水位遥测（只读） ─────────────────────────────────────────

#: 三层的固定顺序（报告里按 L2 → L3 → L4 排，跟 `02` 的层次编号一致）。
LAYERS = ("L2", "L3", "L4")

#: 层 → 库里的表。层名只是 `07` 的叫法，遥测要落到表上才量得到。
LAYER_TABLES = {"L2": "observations", "L3": "inspirations", "L4": "round_digests"}

#: 层 → 水位线。**从生产那边 import**（`render_*` 的尾注用的就是这三个数），
#: 这里再抄一份就会两边漂移：表里报"超水位"、提示词里一句多余的话都没说。
LAYER_CAPACITIES = {"L2": L2_CAPACITY, "L3": L3_CAPACITY, "L4": L4_CAPACITY}


@dataclass(frozen=True)
class LayerWatermark:
    """一层的只读水位读数。`over` 是**算出来的**，不给第二处赋值的地方。"""

    count: int
    capacity: int
    #: 最旧一行的 `created_at`。三种"没有"必须分开：查不出（`created_at` 列不在，
    #: 库还没迁移）／`''`（一行有时间戳的都没有，全是老行）／有值。
    #: 混为一谈就是把"没问出来"说成"没有"，那比报错更坏。
    oldest: str | None = None

    @property
    def over(self) -> bool:
        """超水位 = 该去翻旧账。**超了也不删**——尾注里照实说"更早的待复核"。

        判据 `>` 与渲染层（`len(...) > L*_CAPACITY`）同一位：等于水位不算超。
        """

        return self.count > self.capacity


def memory_watermarks(counts: dict[str, int]) -> dict[str, LayerWatermark]:
    """纯函数：每层行数 → 水位读数（只认给的层，缺的层不补零）。

    补零会撒谎：某张表在老库里根本不存在，报 0 行像"这层是空的"，实际上是没这层。
    反过来，**层名拼错直接 KeyError**——悄悄少报一层，比炸掉更难发现。
    """

    return {
        layer: LayerWatermark(count=count, capacity=LAYER_CAPACITIES[layer])
        for layer, count in counts.items()
    }


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def scan_memory_layers(path: Path) -> dict[str, LayerWatermark]:
    """只读扫三层表：行数 + 最旧 `created_at`。

    两种"读不到"分开处置（同本脚本扫轮次时的防守：不炸整次扫描）：
    **表压根没有** → 跳这一层，且**不补零**（"没这层"和"这层是空的"是两回事，
    补零会撒谎）；**表在但 `created_at` 列不在**（迁移前的库，真库 2026-10-18 就是
    这副样子）→ 行数照报，时间戳老实说"查不出"。

    最旧时间戳用 `MIN` 且**跳过没时间的行**：老行 `created_at=''` 参与 `MIN` 会把
    结果压成 `''`，等于白问一句。老行是"没时间"，不是"最早"。
    """

    counts: dict[str, int] = {}
    oldest: dict[str, str | None] = {}
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for layer in LAYERS:
            table = LAYER_TABLES[layer]
            if not _table_exists(connection, table):
                continue
            counts[layer] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            try:
                oldest[layer] = (
                    connection.execute(
                        f"SELECT MIN(created_at) FROM {table}"
                        " WHERE COALESCE(created_at, '') <> ''"
                    ).fetchone()[0]
                    or ""
                )
            except sqlite3.Error:
                oldest[layer] = None  # 行数照样报（老库也该看得见还剩几行），时间说"查不出"
    finally:
        connection.close()
    return {
        layer: replace(row, oldest=oldest[layer])
        for layer, row in memory_watermarks(counts).items()
    }


def _print_memory_report(paths: Sequence[Path]) -> int:
    """水位表：一层一行，数 / 水位线 / 超没超 / 最旧时间戳。超了只标，不动数据。"""

    for path in paths:
        print(f"\n=== {path.name} · 三层记忆水位 ===")
        try:
            marks = scan_memory_layers(path)
        except sqlite3.Error as error:
            print(f"  跳过：{error}")
            continue
        for layer in LAYERS:
            if layer not in marks:
                print(f"  {layer}  （读不到表 {LAYER_TABLES[layer]}，跳过）")
                continue
            mark = marks[layer]
            # 三种"没有最旧时间"分开说：查不出（列不在）／一行有时间戳的都没有／有值
            if mark.oldest is None:
                oldest = "查不出（created_at 列不在）"
            elif mark.oldest:
                oldest = mark.oldest
            else:
                oldest = "无一行有时间戳"
            print(
                f"  {layer} {mark.count:>6} 行 / 水位 {mark.capacity:<4}"
                f" {'超水位' if mark.over else '未超  '}  最旧 {oldest}"
            )
    return 0


# ── 记忆命中遥测（只读） ─────────────────────────────────────────

def memory_hit_counts(
    entries: Sequence[MemoryEntry],
    cited_by_round: Mapping[str, frozenset[str]],
    *,
    window: int = MEMORY_USAGE_WINDOW,
    order: Sequence[str] = (),
) -> dict[str, int]:
    """每条记忆在最近 `window` 轮里**被引用过的轮数**（现算，只读）。

    判据与 `unused_entries` 同一判据（交集非空即命中），只是这里报的是轮数
    而不是"谁是 0"：0 的那几条，抽屉里标「久未使用」。`order` 是按时间**升序**
    的 `round_id`，只取尾部 `window` 轮。
    """

    if not entries:
        return {}
    recent = order[-window:] if order else list(cited_by_round)[-window:]
    return {
        entry.memory_id: sum(
            1
            for round_id in recent
            if {str(ref) for ref in entry.refs} & cited_by_round.get(round_id, frozenset())
        )
        for entry in entries
    }


def scan_memory_hits(path: Path) -> dict[str, dict[str, int]]:
    """只读扫库：每个项目 → 每条 `memory_id` × 最近 `WINDOW` 轮命中轮数。

    口径与生产端点（`GetMemoryUsage`）同一套：最近 WINDOW 轮 + `cited_eids` +
    上面的纯函数。两种"读不到"分开处置（同本脚本既有防守）：**表不在**（老库）
    → 跳过不补零；**半截轮读不回来** → 跳过那一轮，不炸整次扫描。
    """

    result: dict[str, dict[str, int]] = {}
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row  # 仓储读行用 `row["payload"]`，没它就 TypeError
    try:
        if not _table_exists(connection, "project_memory"):
            return result
        rounds_missing = not _table_exists(connection, "rounds")
        memories = SqliteMemoryRepository(connection)
        round_repo = SqliteRoundRepository(connection)
        for project_id, owner_id in connection.execute(
            "SELECT project_id, owner_id FROM project_memory"
        ).fetchall():
            owner = UserId(owner_id)
            entries = memories.get(ProjectId(project_id), owner=owner).entries
            if rounds_missing:
                recent: list[str] = []
            else:
                recent = [
                    row[0]
                    for row in connection.execute(
                        "SELECT round_id FROM rounds"
                        " WHERE project_id = ? AND owner_id = ?"
                        " ORDER BY rowid DESC LIMIT ?",
                        (project_id, owner_id, MEMORY_USAGE_WINDOW),
                    ).fetchall()
                ][::-1]
            cited_by_round: dict[str, frozenset[str]] = {}
            for round_id in recent:
                try:
                    round_ = round_repo.get(RoundId(round_id), owner=owner)
                except Exception:  # noqa: BLE001 —— 半截轮/老行：跳过，不炸整次扫描
                    continue
                if round_.draft is None:
                    cited_by_round[round_id] = frozenset()
                    continue
                questions = tuple(
                    question
                    for form in round_.questionnaire_history
                    for question in form.questions
                )
                cited_by_round[round_id] = frozenset(
                    str(eid) for eid in cited_eids(round_.draft.proposals, questions)
                )
            result[project_id] = memory_hit_counts(
                entries, cited_by_round, order=tuple(recent)
            )
    finally:
        connection.close()
    return result


def _print_memory_hits_report(paths: Sequence[Path]) -> int:
    """命中表：每条 `memory_id` × 最近 WINDOW 轮命中轮数。0 = 抽屉里标「久未使用」。"""

    for path in paths:
        print(f"\n=== {path.name} · 记忆命中（最近 {MEMORY_USAGE_WINDOW} 轮） ===")
        try:
            scans = scan_memory_hits(path)
        except sqlite3.Error as error:
            print(f"  跳过：{error}")
            continue
        if not scans:
            print("  （没有记忆行）")
            continue
        for project_id, hits in scans.items():
            if not hits:
                print(f"  {project_id}  （空记忆）")
                continue
            for memory_id in sorted(hits):
                stale = "久未使用" if hits[memory_id] == 0 else ""
                print(
                    f"  {memory_id}  {hits[memory_id]}/{MEMORY_USAGE_WINDOW}"
                    f" 轮命中  {stale}"
                )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="消费遥测（只读）")
    parser.add_argument(
        "--db", default=None, help="只扫这一个库（默认扫 .codex/runs/**/*.db）"
    )
    parser.add_argument(
        "--memory",
        action="store_true",
        help="只打三层记忆水位表（行数 / 水位线 / 最旧 created_at）",
    )
    parser.add_argument(
        "--memory-hits",
        action="store_true",
        help="只打记忆命中表（每条 memory_id × 最近 10 轮命中轮数）",
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

    if args.memory:
        return _print_memory_report(paths)

    if args.memory_hits:
        return _print_memory_hits_report(paths)

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
