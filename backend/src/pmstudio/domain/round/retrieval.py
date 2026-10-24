"""检索 trace：一轮里每一步"查了什么、命中什么、最后选中什么"。

它是**证据池的来源**（`Round.retrievals`），不是第二份材料：
- `hits` 是这一步检索到的候选块（可能 50 条，只有 chunk_id/score，不含正文）；
- `selected` 是这一步真正进入提示词的块（应用层会对它们发 E 号、写 `Round.materials`）。

**不存 eid**：编号的真相源是 `Round.materials` 与账本。trace 再存一份就是两个真相源
（`02` §11.0.92）。

**`selected` 不要求是 `hits` 的子集**：同一趟投影可能选中上一趟留在池里的块，
或用户手动带进的块（`ManualChunk`）。要求子集等于把"池"错当成"本趟命中"。
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pmstudio.domain.shared.errors import ShapeError


class RetrievalStage(StrEnum):
    """哪一步发起的检索。

    没有 `OPENING`：`opening → seat_round` 之间没有模型调用，首趟 `SEAT`
    已经用 claims + 席位职责构造 query；保留 OPENING 只会连着查两次
    （实施计划 v2 §11-C10）。
    """

    SEED = "seed"
    SEAT = "seat"
    INTEGRATE = "integrate"


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    """一个候选块：属于哪份材料、块 id、检索分数。

    `score` 对**没有检索分的块**（手动带进、上一趟留在池里）用 **`0.0` 哨兵**。
    不要随手编一个正数——那会让"按分数降序"把非命中块排到命中块前面。
    """

    material_id: str
    chunk_id: str
    score: float = 0.0

    def __post_init__(self) -> None:
        if not self.material_id.strip():
            raise ShapeError("检索命中必须带材料 id")
        if not self.chunk_id.strip():
            raise ShapeError("检索命中必须带块 id", material_id=self.material_id)


@dataclass(frozen=True, slots=True)
class ManualChunk:
    """用户在搜索框里点「带进本轮」的一块。

    与 `RetrievalHit` 分开，是因为它**没有分数、也不来自检索**：
    它是"人明确要的"，在投影里排最前（实施计划 §4 Task 2.2）。
    """

    material_id: str
    chunk_id: str

    def __post_init__(self) -> None:
        if not self.material_id.strip():
            raise ShapeError("手动带进的块必须带材料 id")
        if not self.chunk_id.strip():
            raise ShapeError("手动带进的块必须带块 id", material_id=self.material_id)


@dataclass(frozen=True, slots=True)
class RetrievalTrace:
    """一步检索的完整轨迹。

    `ok=False` 必须配非空 `error`；`ok=True` 必须 `error is None`。
    零命中是**成功**（`ok=True, hits=()`）——它和"检索器坏了"是两件事，
    采集与诊断要分得开。
    """

    stage: RetrievalStage
    #: stage 内的幂等键：SEAT=`seat_round_count`、INTEGRATE=`integration_seq`、SEED=0。
    pass_index: int
    query: str
    hits: tuple[RetrievalHit, ...] = ()
    selected: tuple[RetrievalHit, ...] = ()
    ok: bool = True
    error: str | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.stage, RetrievalStage):
            raise ShapeError("检索阶段必须是 RetrievalStage", stage=str(self.stage))
        if self.pass_index < 0:
            raise ShapeError("pass_index 不能为负", pass_index=self.pass_index)

        # 调用方可能传 list；值对象内统一成 tuple（frozen 只能这样改）。
        object.__setattr__(self, "hits", tuple(self.hits))
        object.__setattr__(self, "selected", tuple(self.selected))

        for label, items in (("hits", self.hits), ("selected", self.selected)):
            for item in items:
                if not isinstance(item, RetrievalHit):
                    raise ShapeError(
                        f"{label} 里必须都是 RetrievalHit",
                        index=items.index(item),
                    )
            ids = [item.chunk_id for item in items]
            if len(ids) != len(set(ids)):
                raise ShapeError(
                    f"{label} 里同一个块出现了两次",
                    stage=str(self.stage),
                    pass_index=self.pass_index,
                )

        if self.ok and self.error is not None:
            raise ShapeError("ok=True 时不能带 error", error=self.error)
        if not self.ok and (self.error is None or not self.error.strip()):
            raise ShapeError("ok=False 必须写清失败原因")
