"""M11 黑板 —— 这一轮任务的共享状态。

它做三件事：**存**（五个区块各有各的家）、**写即广播**（按 `REGION_BROADCAST` 映射发事件）、
**先提交后广播**（事务提交之后才调总线；提交已经完成，广播失败不回滚）。

三个类：`Blackboard` 是本体，`BoardReader` 是只读句柄，`BoardEditor` 是写句柄。拆成两个句柄是 I19
（区块单写者）的类型表达——装配时只把 `BoardWriter` 交给编排层。

事件的 payload 由黑板**自己组**：`round.updated` 的 `from_phase`、`card_group.updated` 的
`from_state` 都来自"写之前那一份"，调用方填不了（填错就是脏数据）。
"""

from pmstudio.common.clock import Clock
from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import (
    CardKind,
    EventType,
    ProducerIdentity,
    RegionName,
    RoundEndReason,
    RoundPhase,
)
from pmstudio.contracts.interfaces.communication import EventBus
from pmstudio.contracts.interfaces.storage import BoardStorePort
from pmstudio.contracts.models.card_group import CardGroup
from pmstudio.contracts.skeleton.board import (
    REGION_BROADCAST,
    RegionValue,
    RoundRegion,
    assert_region_value,
)
from pmstudio.contracts.skeleton.events import (
    CardGroupUpdatedPayload,
    Event,
    RoundUpdatedPayload,
)


class Blackboard:
    """黑板本体：持有"当前这一轮"，读写都从这里走。"""

    def __init__(self, store: BoardStorePort, bus: EventBus, clock: Clock) -> None:
        self._store = store
        self._bus = bus
        self._clock = clock
        self._round_id: str | None = None

    @property
    def current_round_id(self) -> str | None:
        return self._round_id

    async def open_round(self, round_id: str) -> None:
        """设成当前轮，并清掉别的轮残留的区块（C12：用户开新一轮时再删）。"""
        if not round_id:
            raise ContractViolation("round_id 不能为空")
        await self._store.drop_other_regions(round_id)
        self._round_id = round_id

    async def read(self, region: RegionName) -> RegionValue | None:
        """读当前轮的一个区块；没开轮、或这个区块还没写过，都是 `None`。"""
        if self._round_id is None:
            return None
        if region is RegionName.ROUND:
            return await self._store.read_round(self._round_id)
        return await self._store.read_region(self._round_id, region)

    async def write(self, region: RegionName, value: RegionValue) -> None:
        """写一个区块：校验 → 落库（提交）→ 广播。"""
        round_id = self._require_round()
        assert_region_value(region, value)
        self._reject_foreign_round(round_id, value)

        previous = await self.read(region)
        if region is RegionName.ROUND:
            await self._store.write_round(value)  # type: ignore[arg-type]
        else:
            await self._store.write_region(round_id, region, value)

        await self._broadcast(region, value, previous)

    async def drop_round(self) -> None:
        """轮末清理：删掉当前轮的区块；`rounds` 那一行长期保留。"""
        round_id = self._require_round()
        await self._store.drop_regions(round_id)
        self._round_id = None

    async def recover_unfinished_rounds(self) -> tuple[str, ...]:
        """启动时把上次没跑完的轮标成 `failed`（C12 的黑板生命周期）。

        它**不走 `open_round`**：恢复不是"开新一轮"，不该触发那里的清理——失败轮的五个区块
        要留着给人看"跑到哪了"（用户开新一轮时才删）。
        """
        recovered: list[str] = []
        for round_id in await self._store.unfinished_round_ids():
            previous = await self._store.read_round(round_id)
            if previous is None:
                continue
            failed = previous.model_copy(
                update={
                    "phase": RoundPhase.FAILED,
                    "ended_at": self._clock.now(),
                    "end_reason": RoundEndReason.PROCESS_RESTART,
                }
            )
            await self._store.write_round(failed)
            await self._broadcast(RegionName.ROUND, failed, previous)
            recovered.append(round_id)
        return tuple(recovered)

    # ── 内部 ────────────────────────────────────────────────

    def _require_round(self) -> str:
        if self._round_id is None:
            raise ContractViolation("还没有开轮——先 open_round 再碰区块")
        return self._round_id

    @staticmethod
    def _reject_foreign_round(round_id: str, value: RegionValue) -> None:
        owned = getattr(value, "round_id", None)
        if owned is not None and owned != round_id:
            raise ContractViolation(f"这一轮是 {round_id}，写进来的却是 {owned} 的东西")

    async def _broadcast(self, region: RegionName, value: RegionValue, previous: RegionValue | None) -> None:
        event_type = REGION_BROADCAST.get(region)
        if event_type is None:
            return  # context / claims / confirmed 不发

        payload = (
            self._round_payload(value, previous)
            if event_type is EventType.ROUND_UPDATED
            else self._card_group_payload(value, previous)
        )
        await self._bus.publish(
            Event(type=event_type, payload=payload, at=self._clock.now()),
            producer=ProducerIdentity.ORCHESTRATION,
        )

    @staticmethod
    def _round_payload(value: RegionValue, previous: RegionValue | None) -> RoundUpdatedPayload:
        region = value
        assert isinstance(region, RoundRegion)
        return RoundUpdatedPayload(
            round_id=region.round_id,
            project_id=region.project_id,
            entry=region.entry,
            phase=region.phase,
            from_phase=previous.phase if isinstance(previous, RoundRegion) else None,
            end_reason=region.end_reason,
        )

    @staticmethod
    def _card_group_payload(value: RegionValue, previous: RegionValue | None) -> CardGroupUpdatedPayload:
        group = value
        assert isinstance(group, CardGroup)
        return CardGroupUpdatedPayload(
            group_id=group.group_id,
            round_id=group.round_id,
            state=group.state,
            from_state=previous.state if isinstance(previous, CardGroup) else None,
            card_count=len(group.cards),
            has_fill_card=any(card.kind is CardKind.FILL for card in group.cards),
        )


class BoardReader:
    """黑板只读句柄——所有层都能拿它读当前轮。"""

    def __init__(self, blackboard: Blackboard) -> None:
        self._blackboard = blackboard

    async def read(self, region: RegionName) -> RegionValue | None:
        return await self._blackboard.read(region)


class BoardEditor:
    """黑板写句柄——只有编排层拿得到（I19）。"""

    def __init__(self, blackboard: Blackboard) -> None:
        self._blackboard = blackboard

    async def open_round(self, round_id: str) -> None:
        await self._blackboard.open_round(round_id)

    async def write(self, region: RegionName, value: RegionValue) -> None:
        await self._blackboard.write(region, value)

    async def drop_round(self) -> None:
        await self._blackboard.drop_round()
