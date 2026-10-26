"""用户手动跳过席位的**控制行**（P3b Task 4）。

它不属于 `Round` 聚合的 payload：跑中跳过的写入必须能与在飞席位交回并发，
而写整份 Round 会在两者之间产生丢更新。控制行只有一行、按 `(round_id, seat_id)`
latest-wins；`RoundRepository.get` 每次读时把它并进 `skipped_seats`，DTO 再合成
`USER_SKIPPED` 留痕——所以它既不需要写 `Round.dropped`，也不会被 `get → save → get`
复制成两条。
"""

from dataclasses import dataclass

from pmstudio.domain.shared.errors import ShapeError

USER_SKIP_SOURCE = "user_skipped"


@dataclass(frozen=True, slots=True)
class RoundSkip:
    """一次用户跳过：`pass_index` 是当时第几趟席位轮（与 `DroppedItem` 同一条理由）。"""

    seat_id: str
    pass_index: int
    reason: str
    requested_at_ms: int
    source: str = USER_SKIP_SOURCE

    def __post_init__(self) -> None:
        if not self.seat_id.strip():
            raise ShapeError("跳过席位必须写清 seat_id")
        if not self.reason.strip():
            raise ShapeError("跳过席位必须写清原因", seat_id=self.seat_id)
        if self.pass_index < 1:
            raise ShapeError("跳过席位必须说明属于哪一趟席位轮", seat_id=self.seat_id)
        if self.source != USER_SKIP_SOURCE:
            raise ShapeError(
                "控制行只承载用户手动跳过；编排层自动跳过仍写 Round.dropped",
                seat_id=self.seat_id,
            )


def user_skip_reason(seat_id: str) -> str:
    """控制行与 DTO 合成留痕共用的一句话（只写一次，两处不许漂）。"""

    return f"用户跳过了席位 {seat_id}，本轮不再请它"
