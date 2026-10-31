"""项目级研究（P3b-1）的产物：来源卡、结论、报告。

三条口径写在这里，因为它们都是**机器判得了**的：

1. **一条来源 = 一个出处编号**：`sources[].eid` 是账本里的 E，不是第二个编号空间；
2. **必须有答案**：`answer` 是一段非空的结论，`answer_refs` 只能引用这一趟的来源；
   `findings` 可以空——答案可以不拆成证据点，但整份报告不能没有答案；
3. **结论只能引用来源**：`findings[].refs ⊆ {source.eid}`——结论不是出处，
   它说的是"根据这几条来源，我判断……"；
4. **上限是产物上限，不是调用上限**：跑几次工具由 `run_tool_loop` 管（6 轮 / 12 次），
   这里管的是最后交回来几条。

**零来源不是领域错误**：搜不到东西是运行结果（用例判失败），不是数据不自洽。
所以 `ResearchReport` 允许空 `sources`，只判形状与上限。

`refs` 里存 `EvidenceId` 而不是临时的 `S1..S5`：S 号只活在模型报文里，
由 application 映射成 E 之后再进领域——**这里不引入第三套编号**。
"""

from dataclasses import dataclass

from pmstudio.domain.shared.errors import ShapeError, TooManyInquiryItems
from pmstudio.domain.shared.ids import EvidenceId

# 上限与来源卡格式住在 `shared/`（round 与 exploration 两个上下文共用），
# 这里再导出一次，让"研究产物"这个概念只有一个入口（与 `provenance/evidence.py`
# 再导出 `EvidenceKind` 同一条做法）。
from pmstudio.domain.shared.research import (
    MAX_RESEARCH_ANSWER_CHARS,
    MAX_RESEARCH_FINDINGS,
    MAX_RESEARCH_OPEN_QUESTIONS,
    MAX_RESEARCH_SOURCES,
    MAX_RESEARCH_TOPIC_CHARS,
    RESEARCH_SOURCE_CARD_CHARS,
    source_card_text,
)

__all__ = [
    "MAX_RESEARCH_ANSWER_CHARS",
    "MAX_RESEARCH_FINDINGS",
    "MAX_RESEARCH_OPEN_QUESTIONS",
    "MAX_RESEARCH_SOURCES",
    "MAX_RESEARCH_TOPIC_CHARS",
    "RESEARCH_SOURCE_CARD_CHARS",
    "ResearchFinding",
    "ResearchReport",
    "ResearchSource",
    "source_card_text",
]


@dataclass(frozen=True, slots=True)
class ResearchSource:
    """一条来源：账本编号 + 材料 id + 给人看的三件套。

    `eid` 在**研究落库那一刻**就发好了（一条来源一个号），挂到轮次只是引用它。
    所以这里存的是账本身份，不是"待发号"的占位。
    """

    eid: EvidenceId
    material_id: str
    title: str
    url: str
    excerpt: str

    def __post_init__(self) -> None:
        for field, value in (
            ("eid", str(self.eid)),
            ("material_id", self.material_id),
            ("title", self.title),
            ("url", self.url),
            ("excerpt", self.excerpt),
        ):
            if not value.strip():
                raise ShapeError(
                    "来源的每一项都不能为空", field=field, eid=str(self.eid)
                )
        card = source_card_text(title=self.title, url=self.url, excerpt=self.excerpt)
        if len(card) > RESEARCH_SOURCE_CARD_CHARS:
            raise ShapeError(
                f"一张来源卡最多 {RESEARCH_SOURCE_CARD_CHARS} 字，这条 {len(card)} 字",
                eid=str(self.eid),
                chars=len(card),
                limit=RESEARCH_SOURCE_CARD_CHARS,
            )


@dataclass(frozen=True, slots=True)
class ResearchFinding:
    """一条结论。`finding_id` 只在趟内唯一（`F1`、`F2`…），不进账本。"""

    finding_id: str
    statement: str
    refs: tuple[EvidenceId, ...]

    def __post_init__(self) -> None:
        if not self.finding_id.strip():
            raise ShapeError("结论必须有 finding_id")
        if not self.statement.strip():
            raise ShapeError("结论必须写清内容", finding_id=self.finding_id)
        if not self.refs:
            raise ShapeError(
                "结论必须引用至少一条来源（没有来源的判断应写成开放问题）",
                finding_id=self.finding_id,
            )


@dataclass(frozen=True, slots=True)
class ResearchReport:
    """一趟研究交回的东西。**只读**：结论不进文档、不进上下文；挂轮挂的是来源。"""

    findings: tuple[ResearchFinding, ...]
    sources: tuple[ResearchSource, ...]
    answer: str
    answer_refs: tuple[EvidenceId, ...]
    open_questions: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.sources) > MAX_RESEARCH_SOURCES:
            raise TooManyInquiryItems(
                kind="来源",
                count=len(self.sources),
                limit=MAX_RESEARCH_SOURCES,
                run_label="研究",
            )
        if len(self.findings) > MAX_RESEARCH_FINDINGS:
            raise TooManyInquiryItems(
                kind="结论",
                count=len(self.findings),
                limit=MAX_RESEARCH_FINDINGS,
                run_label="研究",
            )
        if len(self.open_questions) > MAX_RESEARCH_OPEN_QUESTIONS:
            raise TooManyInquiryItems(
                kind="开放问题",
                count=len(self.open_questions),
                limit=MAX_RESEARCH_OPEN_QUESTIONS,
                run_label="研究",
            )
        if not self.answer.strip():
            raise ShapeError("研究必须给出一段答案", field="answer")
        if len(self.answer) > MAX_RESEARCH_ANSWER_CHARS:
            raise ShapeError(
                f"研究答案最多 {MAX_RESEARCH_ANSWER_CHARS} 字，这段 {len(self.answer)} 字",
                field="answer",
                chars=len(self.answer),
                limit=MAX_RESEARCH_ANSWER_CHARS,
            )
        if not self.answer_refs:
            raise ShapeError("研究答案必须引用至少一条来源", field="answer_refs")
        known_eids = {source.eid for source in self.sources}
        if len(known_eids) != len(self.sources):
            raise ShapeError(
                "同一条来源不能占两个编号",
                eids=[str(item.eid) for item in self.sources],
            )
        material_ids = [source.material_id for source in self.sources]
        if len(set(material_ids)) != len(material_ids):
            raise ShapeError("同一份材料不能算两条来源", material_ids=material_ids)
        finding_ids = [item.finding_id for item in self.findings]
        if len(set(finding_ids)) != len(finding_ids):
            raise ShapeError("结论的 finding_id 重复", finding_ids=finding_ids)
        unknown_answer_refs = [
            str(ref) for ref in self.answer_refs if ref not in known_eids
        ]
        if unknown_answer_refs:
            raise ShapeError(
                "答案引用了这一趟没有的来源：" + "、".join(unknown_answer_refs),
                field="answer_refs",
            )
        for item in self.findings:
            unknown = [str(ref) for ref in item.refs if ref not in known_eids]
            if unknown:
                raise ShapeError(
                    "结论引用了这一趟没有的来源：" + "、".join(unknown),
                    finding_id=item.finding_id,
                )
        for question in self.open_questions:
            if not question.strip():
                raise ShapeError("开放问题不能是空字符串")
