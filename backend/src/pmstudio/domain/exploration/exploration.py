"""探索运行：与 Round 平级的聚合。M8 只长「调研」（inquiry）一种能力。

两条口径写死在这里：

1. **产物不是候选之前先跑完**：只有 `done` 的探索才有可过目的候选（X3）；
2. **候选是单向的**：`new → kept | dismissed`，重复动作幂等，但**不许改去向**——
   "先收下为种子、再改成材料"说不清语义（种子已经进了输入框），直接拒。
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pmstudio.domain.exploration.item import (
    MAX_INQUIRY_BLIND_SPOTS,
    MAX_INQUIRY_HYPOTHESES,
    BlindSpot,
    Candidate,
    CandidateState,
    Hypothesis,
)
from pmstudio.domain.exploration.research import (
    MAX_RESEARCH_TOPIC_CHARS,
    ResearchReport,
)
from pmstudio.domain.shared.errors import (
    CandidateNotReviewable,
    DuplicateCandidateId,
    IllegalExplorationTransition,
    ShapeError,
    TooManyInquiryItems,
)
from pmstudio.domain.shared.ids import ExplorationId, ProjectId


class ExplorationKind(StrEnum):
    INQUIRY = "inquiry"  # M8 的调研：假设 + 盲区，产物要过人过一次目台
    #: P3b-1 的项目级研究：`run_tool_loop` 自己搜、自己抓，产物是**只读**的结论 + 来源。
    #: 与 INQUIRY 分开是两件事：入口（topic）、产物（报告）、过目方式（没有候选）都不同，
    #: 合成一种会让 `candidates()` 对研究返回一个假的空过目台。
    WEB_RESEARCH = "web_research"


class ExplorationStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    ABANDONED = "abandoned"


class ScopeKind(StrEnum):
    ENTRY = "entry"
    DOCUMENT = "document"
    #: 项目级研究只有一个主题，不锚定条目也不要求文档有内容（2026-10-29 P3b-1）。
    TOPIC = "topic"


@dataclass(frozen=True, slots=True)
class ExplorationScope:
    """调研范围。`entry` 必须带 entry_id；`topic` 必须有 topic 文本、不许带条目。"""

    kind: ScopeKind
    ref: str | None = None
    text: str | None = None

    def __post_init__(self) -> None:
        if self.kind is ScopeKind.ENTRY and not (self.ref or "").strip():
            raise ShapeError("从条目发起调研必须带条目标识（entry_id）")
        if self.kind is ScopeKind.TOPIC:
            topic = (self.text or "").strip()
            if (self.ref or "").strip():
                raise ShapeError("主题型研究不锚定条目：`ref` 必须为空")
            if not topic:
                raise ShapeError("主题型研究必须写清研究什么（topic）")
            if len(topic) > MAX_RESEARCH_TOPIC_CHARS:
                raise ShapeError(
                    f"研究主题最多 {MAX_RESEARCH_TOPIC_CHARS} 字，这条 {len(topic)} 字",
                    chars=len(topic),
                    limit=MAX_RESEARCH_TOPIC_CHARS,
                )


@dataclass(frozen=True, slots=True)
class ExplorationFailure:
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ShapeError("失败必须写清原因")


class Exploration:
    def __init__(
        self,
        *,
        exploration_id: ExplorationId,
        project_id: ProjectId,
        scope: ExplorationScope,
        created_at: datetime,
        kind: ExplorationKind = ExplorationKind.INQUIRY,
        status: ExplorationStatus = ExplorationStatus.RUNNING,
        hypotheses: tuple[Hypothesis, ...] = (),
        blind_spots: tuple[BlindSpot, ...] = (),
        states: dict[str, CandidateState] | None = None,
        kept: dict[str, str] | None = None,
        kept_ref: dict[str, str] | None = None,
        failure: ExplorationFailure | None = None,
        abandon_reason: str | None = None,
        overflow_count: int = 0,
        research: ResearchReport | None = None,
    ) -> None:
        self._exploration_id = exploration_id
        self._project_id = project_id
        self._kind = kind
        self._scope = scope
        self._created_at = created_at
        self._status = status
        self._hypotheses = tuple(hypotheses)
        self._blind_spots = tuple(blind_spots)
        self._states = dict(states or {})
        self._kept = dict(kept or {})
        self._kept_ref = dict(kept_ref or {})
        self._failure = failure
        self._abandon_reason = abandon_reason
        #: 报文超限被截断的条数——**不能默默丢**：界面要把这个数照实说出来
        self._overflow_count = overflow_count
        #: 研究产物（结论 + 来源）。只有 `WEB_RESEARCH` 会有；调研那一路恒为 None。
        self._research = research
        self._check_kind_consistency()

    def _check_kind_consistency(self) -> None:
        """三条只有聚合知道的关系，读盘与新建一视同仁：

        1. 研究只锚主题、调研不认主题（`ScopeKind` 与 `ExplorationKind` 双向绑定）；
        2. 只有研究能带报告；
        3. **跑完的研究必须有报告**——"done 但没有报告"是一份被读丢的数据，
           不许它安静地出现在列表里。
        """

        is_research = self._kind is ExplorationKind.WEB_RESEARCH
        is_topic = self._scope.kind is ScopeKind.TOPIC
        if is_research != is_topic:
            raise ShapeError(
                "研究的范围必须是主题、调研的范围不能是主题",
                kind=self._kind.value,
                scope_kind=self._scope.kind.value,
            )
        if self._research is not None and not is_research:
            raise ShapeError(
                "只有研究才有报告", kind=self._kind.value
            )
        if is_research and self._status is ExplorationStatus.DONE and self._research is None:
            raise ShapeError(
                "这一趟研究已经是 done，但没有报告（payload 被读丢了？）",
                exploration_id=str(self._exploration_id),
            )

    @classmethod
    def start(
        cls,
        *,
        exploration_id: ExplorationId,
        project_id: ProjectId,
        scope: ExplorationScope,
        now: datetime,
        kind: ExplorationKind = ExplorationKind.INQUIRY,
    ) -> "Exploration":
        return cls(
            exploration_id=exploration_id,
            project_id=project_id,
            scope=scope,
            created_at=now,
            kind=kind,
        )

    # —— 只读 ——
    @property
    def exploration_id(self) -> ExplorationId:
        return self._exploration_id

    @property
    def project_id(self) -> ProjectId:
        return self._project_id

    @property
    def kind(self) -> ExplorationKind:
        return self._kind

    @property
    def scope(self) -> ExplorationScope:
        return self._scope

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def status(self) -> ExplorationStatus:
        return self._status

    @property
    def hypotheses(self) -> tuple[Hypothesis, ...]:
        return self._hypotheses

    @property
    def blind_spots(self) -> tuple[BlindSpot, ...]:
        return self._blind_spots

    @property
    def failure(self) -> ExplorationFailure | None:
        return self._failure

    @property
    def abandon_reason(self) -> str | None:
        return self._abandon_reason

    @property
    def overflow_count(self) -> int:
        """这一趟报文超限被截断了几条（0 = 没有超）。"""

        return self._overflow_count

    @property
    def research(self) -> ResearchReport | None:
        """研究产物；调研那一路恒为 `None`（不是"空报告"，是没有这回事）。"""

        return self._research

    def candidates(self) -> tuple[Candidate, ...]:
        """过目台/详情看到的统一候选列表（假设在前、盲区在后，顺序稳定）。

        研究**没有候选**：它的产物是只读的结论与来源，过目台不该出现一个假入口。
        """

        if self._kind is ExplorationKind.WEB_RESEARCH:
            return ()
        found: list[Candidate] = []
        for item in self._hypotheses:
            found.append(
                Candidate(
                    candidate_id=item.local_id,
                    kind="hypothesis",
                    statement=item.statement,
                    state=self._states.get(item.local_id, CandidateState.NEW),
                    kept_as=self._kept.get(item.local_id),
                    kept_ref=self._kept_ref.get(item.local_id),
                    grounding=item.grounding,
                    based_on=item.based_on,
                    falsification=item.falsification,
                    evidence_needed=item.evidence_needed,
                )
            )
        for spot in self._blind_spots:
            found.append(
                Candidate(
                    candidate_id=spot.local_id,
                    kind="blind_spot",
                    statement=spot.statement,
                    state=self._states.get(spot.local_id, CandidateState.NEW),
                    kept_as=self._kept.get(spot.local_id),
                    kept_ref=self._kept_ref.get(spot.local_id),
                    next_step=spot.next_step,
                )
            )
        return tuple(found)

    # —— 推进 ——
    def complete(
        self,
        *,
        hypotheses: tuple[Hypothesis, ...] | list[Hypothesis],
        blind_spots: tuple[BlindSpot, ...] | list[BlindSpot],
        overflow_count: int = 0,
    ) -> None:
        """跑完：挂产物。上限与 id 唯一在这里拦（报文层截断后的第二道）。"""

        if self._kind is not ExplorationKind.INQUIRY:
            raise ShapeError(
                f"这一趟不是调研（kind={self._kind.value}）：研究报告走 complete_research",
                kind=self._kind.value,
            )
        if self._status is not ExplorationStatus.RUNNING:
            raise IllegalExplorationTransition(
                current=self._status, target=ExplorationStatus.DONE
            )
        item_ids = [item.local_id for item in hypotheses]
        item_ids += [item.local_id for item in blind_spots]
        if len(set(item_ids)) != len(item_ids):
            duplicates = sorted({x for x in item_ids if item_ids.count(x) > 1})
            raise DuplicateCandidateId(local_id=duplicates[0])
        if len(hypotheses) > MAX_INQUIRY_HYPOTHESES:
            raise TooManyInquiryItems(
                kind="假设", count=len(hypotheses), limit=MAX_INQUIRY_HYPOTHESES
            )
        if len(blind_spots) > MAX_INQUIRY_BLIND_SPOTS:
            raise TooManyInquiryItems(
                kind="盲区", count=len(blind_spots), limit=MAX_INQUIRY_BLIND_SPOTS
            )
        self._hypotheses = tuple(hypotheses)
        self._blind_spots = tuple(blind_spots)
        self._overflow_count = overflow_count
        self._status = ExplorationStatus.DONE

    def complete_research(self, report: ResearchReport) -> None:
        """研究跑完：挂报告。与 `complete` 的分派互斥（kind 决定走哪一条）。

        上限与 refs 归属在 `ResearchReport.__post_init__` 里判——这里只判"该不该在这里完成"。
        """

        if self._kind is not ExplorationKind.WEB_RESEARCH:
            raise ShapeError(
                f"这一趟不是研究（kind={self._kind.value}）：调研产物走 complete",
                kind=self._kind.value,
            )
        if self._status is not ExplorationStatus.RUNNING:
            raise IllegalExplorationTransition(
                current=self._status, target=ExplorationStatus.DONE
            )
        self._research = report
        self._status = ExplorationStatus.DONE

    def fail(self, reason: str) -> None:
        """记失败：**只记第一次**（与 `Round.fail` 同一条理由，`04` C9）。终态不可改。"""

        if self._status in (ExplorationStatus.DONE, ExplorationStatus.ABANDONED):
            raise IllegalExplorationTransition(
                current=self._status, target=ExplorationStatus.FAILED
            )
        if self._status is ExplorationStatus.FAILED:
            return
        self._failure = ExplorationFailure(reason=reason)
        self._status = ExplorationStatus.FAILED

    def resume(self) -> None:
        if self._status is not ExplorationStatus.FAILED:
            raise IllegalExplorationTransition(
                current=self._status, target=ExplorationStatus.RUNNING
            )
        self._status = ExplorationStatus.RUNNING
        self._failure = None

    def abandon(self, reason: str) -> None:
        """放弃：幂等、终点，只记第一次原因（照 `Round.abandon` 的语义）。"""

        if self._status in (
            ExplorationStatus.DONE,
            ExplorationStatus.FAILED,
            ExplorationStatus.ABANDONED,
        ):
            return
        if not reason.strip():
            raise ShapeError("放弃要写清原因")
        self._abandon_reason = reason.strip()
        self._status = ExplorationStatus.ABANDONED

    def keep(
        self, candidate_id: str, *, as_: str, material_id: str | None = None
    ) -> None:
        """收下一条候选：幂等（同去向重复收下什么都不做），但**不许改去向**。

        `as_` 决定回流路径：`seed` = 预填下一轮输入；`material` = 落成材料（必须带 id，
        因为材料已经先落库了才盖这个章）。
        """

        self._require_inquiry_action(candidate_id, action="收下")
        self._require_done(candidate_id)
        self._known(candidate_id)
        if as_ not in ("seed", "material"):
            raise ShapeError("收下的去向只能是 seed 或 material", as_=as_)
        state = self._states.get(candidate_id, CandidateState.NEW)
        if state is CandidateState.DISMISSED:
            raise CandidateNotReviewable(
                candidate_id=candidate_id, state="已删掉", action="收下"
            )
        if state is CandidateState.KEPT:
            if self._kept.get(candidate_id) == as_:
                return
            raise CandidateNotReviewable(
                candidate_id=candidate_id,
                state=f"已收下为{self._kept.get(candidate_id)}",
                action=f"改成{as_}",
            )
        if as_ == "material" and not (material_id or "").strip():
            raise ShapeError("收下为材料必须带 material_id", candidate_id=candidate_id)
        self._states[candidate_id] = CandidateState.KEPT
        self._kept[candidate_id] = as_
        if as_ == "material" and material_id is not None:
            self._kept_ref[candidate_id] = material_id

    def dismiss(self, candidate_id: str) -> None:
        self._require_inquiry_action(candidate_id, action="删掉")
        self._require_done(candidate_id)
        self._known(candidate_id)
        state = self._states.get(candidate_id, CandidateState.NEW)
        if state is CandidateState.KEPT:
            raise CandidateNotReviewable(
                candidate_id=candidate_id, state="已收下", action="删掉"
            )
        self._states[candidate_id] = CandidateState.DISMISSED

    # —— 内部 ——
    def _require_inquiry_action(self, candidate_id: str, *, action: str) -> None:
        """研究没有候选可过目：直接说清，别让它落到"未知候选"那种错里。"""

        if self._kind is not ExplorationKind.INQUIRY:
            raise ShapeError(
                f"这一趟是研究，没有可过目的候选（不能{action}）",
                candidate_id=candidate_id,
                kind=self._kind.value,
            )

    def _require_done(self, candidate_id: str) -> None:
        if self._status is not ExplorationStatus.DONE:
            raise CandidateNotReviewable(
                candidate_id=candidate_id,
                state=f"这一趟还没跑完（{self._status}）",
                action="过目",
            )

    def _known(self, candidate_id: str) -> None:
        known = {item.local_id for item in self._hypotheses}
        known |= {item.local_id for item in self._blind_spots}
        if candidate_id not in known:
            raise ShapeError(f"这一趟没有这条候选：{candidate_id}", candidate_id=candidate_id)
