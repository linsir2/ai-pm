"""出处编号的写法归一化。**它只解决写法，不解决幻觉。**

真跑实测（`.codex/runs/2026-09-28/报告.md` F3）：
- 席位把 `E8` 写成 `E08`、`e8`、全角 `Ｅ８`——同一个编号的另一种写法，该认；
- 把上一轮的**提案编号**（`r-f2a00f4e#08`）或席位地址（`seat.x:O1`）当出处编号——
  那是另一个命名空间，该判死，只是提示要说清楚它错在哪。

规矩：**只有归一化后能唯一落回账本**才采纳；落不回就原样返回，由调用方按"不认识"处理
（GB1：没有出处的提案不能进文档，这条口径一个字节都不许松）。
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from pmstudio.application.errors import unknown_ref_message
from pmstudio.domain.shared.ids import EvidenceId
from pmstudio.harness.errors import PacketParseError

#: `E` + 可选空格 + 任意个前导零 + 数字；大小写与全角都收
EID = re.compile(r"^[EeＥｅ]\s*0*(\d+)$")


def normalize_ref(raw: str, *, known: frozenset[EvidenceId]) -> str:
    """能唯一落回账本就返回规范写法，否则原样返回（去掉首尾空白）。"""

    text = raw.strip().replace("\u3000", "").replace(" ", "")
    match = EID.match(text)
    if match is None:
        return raw.strip()
    candidate = f"E{int(match.group(1))}"
    return candidate if EvidenceId(candidate) in known else raw.strip()


def candidates_for(
    raw: str, *, known: frozenset[EvidenceId], limit: int = 3
) -> tuple[str, ...]:
    """写错时给"最接近的几个候选"（数字差 1 的最先给），让重装指引说得具体。

    只给数字相邻的候选：账本里几百个编号，编辑距离那套在这里既贵又没意义——
    真跑里错的都是差一位或多了前导零。
    """

    text = raw.strip()
    if not re.match(r"^[EeＥｅ]", text):
        # 席位地址（seat.x:O1）或提案编号（r-…#08）不是"写歪的出处编号"，
        # 给候选只会把它往错的编号上引——这两个命名空间的提示由调用方另说。
        return ()
    digits = re.sub(r"\D", "", text)
    if not digits:
        return ()
    number = int(digits)
    ranked = sorted(
        (abs(int(str(item)[1:]) - number), str(item))
        for item in known
        if re.fullmatch(r"E\d+", str(item))
    )
    return tuple(item for delta, item in ranked if delta <= 1)[:limit]


def resolve_evidence_refs(
    raw_refs: Sequence[str],
    *,
    known: frozenset[EvidenceId],
    what: str,
    local_id: str,
    purpose: str = "inquiry",
) -> tuple[EvidenceId, ...]:
    """`based_on` 原串 → 规范 E 编号元组；未知编号抛 `PacketParseError`（带 candidates 供重装）。

    inquiry 与 discussion 共用（`_NUMERIC` 不搬：discussion 不验数量，M8.1 结论）。
    `purpose` 默认 inquiry（调用方 inquirer 的口径）；discusser 按自己那一趟传。
    """

    refs = tuple(EvidenceId(normalize_ref(ref, known=known)) for ref in raw_refs)
    unknown = [str(ref) for ref in refs if ref not in known]
    if unknown:
        raise PacketParseError(
            unknown_ref_message(unknown, what=what),
            purpose=purpose,
            local_id=local_id,
            candidates=[candidates_for(item, known=known) for item in unknown],
        )
    return refs


@dataclass(frozen=True, slots=True)
class ResolvedRefs:
    """引用解析结果：`kept` 是落回账本的规范编号，`dropped` 是摘掉的原始串（留痕要报）。"""

    kept: tuple[EvidenceId, ...]
    dropped: tuple[str, ...]


def resolve_refs_lenient(
    raw_refs: Sequence[str],
    *,
    known: frozenset[EvidenceId],
    what: str,
    local_id: str,
) -> ResolvedRefs:
    """**降级版解析**：认得的留下、认不得的摘掉（不抛）。

    给"重装一次仍犯"的**最后一次尝试**用（`02` §11.0.2 口径）：结构错（空结果 / 形状）
    照旧判死，只有"编号不认识"这一类可降级——正文还在、依据摘掉，留痕由调用方记
    （席位写进 `Round.dropped`，模拟调研写进 `ref_drops`）。

    与 `resolve_evidence_refs` 的分工：那个是"照旧判死"的严格版，负责第一次尝试与
    重装的报错文案；这个是"最后兜底"的宽松版。两版都走同一套 `normalize_ref`——
    归一化只有一处，别在这里另写一套。
    """

    refs = tuple(EvidenceId(normalize_ref(ref, known=known)) for ref in raw_refs)
    kept = tuple(ref for ref in refs if ref in known)
    dropped = tuple(str(ref) for ref in refs if ref not in known)
    return ResolvedRefs(kept=kept, dropped=dropped)
