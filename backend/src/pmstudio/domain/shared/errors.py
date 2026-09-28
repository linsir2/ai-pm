"""领域错误。InvariantViolation 带 invariant 码，测试据此断言"是哪条规则拦下来的"。"""

from pmstudio.domain.shared.invariants import (
    AgentInvariant,
    DInvariant,
    EInvariant,
    GlobalInvariant,
    LabelInvariant,
    RInvariant,
)


class DomainError(Exception):
    def __init__(self, message: str, *, code: str = "domain_error", **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details


class InvariantViolation(DomainError):
    def __init__(self, message: str, *, invariant: str, **details: object) -> None:
        super().__init__(message, code=f"invariant:{invariant}", **details)
        self.invariant = invariant


class ShapeError(DomainError):
    """结构或用法错误（不是不变量违规），比如 target 形状不对、条目不存在。"""

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message, code="shape_error", **details)


# —— authoring ——
class UnknownSection(InvariantViolation):
    def __init__(self, section_id: object) -> None:
        super().__init__(
            f"未知章节：{section_id}", invariant=DInvariant.D1, section_id=str(section_id)
        )


class SectionSetViolation(InvariantViolation):
    def __init__(self, **details: object) -> None:
        super().__init__("13 章骨架必须完整且不多不少", invariant=DInvariant.D1, **details)


class SectionOrderViolation(InvariantViolation):
    def __init__(self, **details: object) -> None:
        super().__init__("13 章顺序不可变", invariant=DInvariant.D1, **details)


class FeatureNotAllowedHere(InvariantViolation):
    def __init__(self, *, section_id: object, feature_slug: object) -> None:
        super().__init__(
            f"功能子树只能长在 s5，{section_id} 不行",
            invariant=DInvariant.D3,
            section_id=str(section_id),
            feature_slug=str(feature_slug),
        )


class FeatureRequiresUserNaming(InvariantViolation):
    def __init__(self, *, feature_slug: object) -> None:
        super().__init__(
            f"新功能 {feature_slug} 没有得到用户授权，席位不能自己开子树",
            invariant=DInvariant.D3,
            feature_slug=str(feature_slug),
        )


class FeatureAlreadyAuthorized(InvariantViolation):
    def __init__(self, *, feature_slug: object) -> None:
        super().__init__(
            f"功能 {feature_slug} 本轮已经授权过",
            invariant=DInvariant.D3,
            feature_slug=str(feature_slug),
        )


class DuplicateFieldName(InvariantViolation):
    def __init__(self, **details: object) -> None:
        super().__init__("同一父节点下字段名必须唯一", invariant=DInvariant.D4, **details)


class BlockManuallyEdited(InvariantViolation):
    def __init__(self, *, block_id: object) -> None:
        super().__init__(
            f"块 {block_id} 被手工改过，不能被静默覆盖",
            invariant=DInvariant.D5,
            block_id=str(block_id),
        )


class EmptyApply(InvariantViolation):
    def __init__(self) -> None:
        super().__init__("没有要写入的条目，不产生版本", invariant=DInvariant.D6)


class DuplicateBlockId(ShapeError):
    def __init__(self, **details: object) -> None:
        super().__init__("block id 在文档内必须唯一", **details)


class UnknownBlock(ShapeError):
    def __init__(self, block_id: object) -> None:
        super().__init__(f"找不到块 {block_id}", block_id=str(block_id))


class UnknownEntry(ShapeError):
    def __init__(self, entry_id: object) -> None:
        super().__init__(f"找不到条目 {entry_id}", entry_id=str(entry_id))


class UnknownVersion(DomainError):
    """找不到这个版本号。

    以前它继承 `ShapeError`（→ 422）；但"读一个不存在的版本"是资源不在，不是形状写错，
    所以给它自己的 code，由 `api/errors.py` 映射成 404（P5b 拍板点 5）。
    """

    def __init__(self, version_no: object) -> None:
        super().__init__(
            f"找不到版本 v{version_no}", code="unknown_version", version_no=str(version_no)
        )


class UnknownFeatureField(ShapeError):
    def __init__(self, field: object) -> None:
        super().__init__(f"功能字段表里没有 {field}", field=str(field))


class TargetShapeError(ShapeError):
    pass


# —— provenance ——
class DuplicateEvidenceId(InvariantViolation):
    def __init__(self, *, eid: object) -> None:
        super().__init__(f"出处编号 {eid} 已存在", invariant=EInvariant.E1, eid=str(eid))


class UnknownEvidence(InvariantViolation):
    def __init__(self, *, eid: object) -> None:
        super().__init__(f"引用了不存在的出处编号 {eid}", invariant=EInvariant.E3, eid=str(eid))


class DerivedFromRequired(InvariantViolation):
    def __init__(self) -> None:
        super().__init__("agent_derived 必须带 derived_from", invariant=EInvariant.E3)


class SelfDerivation(InvariantViolation):
    def __init__(self, *, eid: object) -> None:
        super().__init__(f"出处 {eid} 不能以自己为依据", invariant=EInvariant.E3, eid=str(eid))


class EvidenceSourceShape(ShapeError):
    pass


# —— round ——
class EmptyUserInput(InvariantViolation):
    def __init__(self) -> None:
        super().__init__("一轮必须绑定一次非空用户输入", invariant=RInvariant.R1)


class IllegalStepTransition(InvariantViolation):
    def __init__(self, *, current: object, target: object) -> None:
        super().__init__(
            f"步骤不能从 {current} 直接到 {target}",
            invariant=RInvariant.R2,
            current=str(current),
            target=str(target),
        )


class WaitingOnHuman(InvariantViolation):
    def __init__(self, *, waiting_on: object) -> None:
        super().__init__(
            f"正在等 {waiting_on} 的输入，不能推进",
            invariant=RInvariant.R2,
            waiting_on=str(waiting_on),
        )


class SeatRoundLimitReached(InvariantViolation):
    def __init__(self, *, limit: int) -> None:
        super().__init__(
            f"一轮最多 {limit} 次席位轮，到上限要把缺口显式化",
            invariant=RInvariant.R3,
            limit=limit,
        )


class TooManyQuestions(InvariantViolation):
    def __init__(self, *, count: int, limit: int) -> None:
        super().__init__(
            f"一批最多 {limit} 题，收到 {count} 题，超出的必须先降级成清单项",
            invariant=RInvariant.R4,
            count=count,
            limit=limit,
        )


class ProposalWithoutRefs(InvariantViolation):
    def __init__(self, *, proposal_id: object) -> None:
        super().__init__(
            f"提案 {proposal_id} 没有出处编号",
            invariant=RInvariant.R5,
            proposal_id=str(proposal_id),
        )


class UnknownQuestion(InvariantViolation):
    def __init__(self, *, question_id: object) -> None:
        super().__init__(
            f"本轮问卷里没有题目 {question_id}",
            invariant=RInvariant.R6,
            question_id=str(question_id),
        )


class NotAwaitingAnswers(InvariantViolation):
    def __init__(self, *, step: object) -> None:
        super().__init__(f"当前步骤 {step} 不在等问卷答案", invariant=RInvariant.R6, step=str(step))


class AlreadyAnswered(InvariantViolation):
    def __init__(self, *, form_id: object) -> None:
        super().__init__(
            f"问卷 {form_id} 已经交过卷了", invariant=RInvariant.R6, form_id=str(form_id)
        )


class UnknownSeat(InvariantViolation):
    def __init__(self, *, seat_id: object) -> None:
        super().__init__(
            f"席位 {seat_id} 不在本轮编制里",
            invariant=RInvariant.R7,
            seat_id=str(seat_id),
        )


class LabelZoneViolation(InvariantViolation):
    """标签与去处对不上：偏好不能进正文，正文标签也不能塞进偏好区。"""

    def __init__(self, *, label: object, expected: object, actual: object) -> None:
        super().__init__(
            f"{label} 的出处置不对：应在 {expected}，实际是 {actual}",
            invariant=LabelInvariant.LB1,
            label=str(label),
            expected=str(expected),
            actual=str(actual),
        )


class GuessWithoutAssumption(InvariantViolation):
    def __init__(self, *, claim_id: object) -> None:
        super().__init__(
            f"猜测 {claim_id} 必须升格为假设（带失败后果与验证方式）",
            invariant=LabelInvariant.LB2,
            claim_id=str(claim_id),
        )


class DecisionWithoutDecider(InvariantViolation):
    def __init__(self, *, claim_id: object) -> None:
        super().__init__(
            f"决定 {claim_id} 必须记决策者",
            invariant=LabelInvariant.LB3,
            claim_id=str(claim_id),
        )


class EmptyBasis(InvariantViolation):
    def __init__(self, *, subject: object) -> None:
        super().__init__(
            f"{subject} 没有依据：必须写清读了什么、为什么没必要补充",
            invariant=AgentInvariant.AG1,
            subject=str(subject),
        )


class ContentWithoutEvidence(InvariantViolation):
    def __init__(self, *, what: object) -> None:
        super().__init__(
            f"{what} 没有出处编号：没有编号的内容只能进开放问题",
            invariant=GlobalInvariant.GB1,
            what=str(what),
        )


class DuplicateLocalId(ShapeError):
    def __init__(self, *, seat_id: object, local_id: object) -> None:
        super().__init__(
            f"席位 {seat_id} 交回的意见里 local_id {local_id} 重复",
            seat_id=str(seat_id),
            local_id=str(local_id),
        )


class DuplicateProposalId(ShapeError):
    def __init__(self, *, proposal_id: object) -> None:
        super().__init__(f"提案 id {proposal_id} 重复", proposal_id=str(proposal_id))


class DuplicateQuestionId(ShapeError):
    def __init__(self, *, question_id: object) -> None:
        super().__init__(f"题目 id {question_id} 重复", question_id=str(question_id))


# —— services ——
class UnknownProposal(ShapeError):
    def __init__(self, *, proposal_id: object) -> None:
        super().__init__(f"本轮草案里没有提案 {proposal_id}", proposal_id=str(proposal_id))


class NothingToCommit(ShapeError):
    def __init__(self) -> None:
        super().__init__("一条都没勾选，不产生版本")


class UntraceableContent(InvariantViolation):
    def __init__(self, *, proposal_id: object, reason: str) -> None:
        super().__init__(
            f"提案 {proposal_id} 回溯不到用户输入或问卷答案：{reason}",
            invariant=EInvariant.E4,
            proposal_id=str(proposal_id),
            reason=reason,
        )
