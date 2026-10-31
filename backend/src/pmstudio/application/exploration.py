"""探索的纯函数：接地检查、条目定位、产物校验、卡片渲染、材料合成。

它们不碰存储、不碰模型：用例层把读到的 `DocumentView` 与模型产物交给这些函数。
校验失败抛 `ShapeError`（重装那一次拿它当 `validation_error`）；
用户指的条目不存在抛 `UnknownEntryId`（资源不在 = 404）。
"""

from pmstudio.application.deliverable import iter_document_entries
from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.application.errors import UnknownEntryId
from pmstudio.application.material_synthesis import material_sha as _namespaced_sha
from pmstudio.domain.authoring.views import DocumentView
from pmstudio.domain.exploration.exploration import ExplorationScope, ScopeKind
from pmstudio.domain.exploration.item import BlindSpot, Candidate, Hypothesis
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import EvidenceId
from pmstudio.domain.shared.numeric_assertion import find_numeric_assertion

#: 材料库里的来源标签（与 `context.ORIGIN_TITLES` / 前端 ORIGIN_TEXT 三处同步）
ORIGIN_EXPLORATION = "exploration"
PARSER_ID_EXPLORATION = "exploration"
PARSER_VERSION_EXPLORATION = "1"

_SCOPE_LABELS = {ScopeKind.ENTRY: "某一条内容", ScopeKind.DOCUMENT: "整份文档"}


def document_has_content(view: DocumentView) -> bool:
    """接地判据：文档里有没有可锚定的内容（偏好区也算）。"""

    if view.preference_entries:
        return True
    return next(iter_document_entries(view), None) is not None


def entry_focus(view: DocumentView, entry_id: str) -> str:
    """把 entry scope 解析成"这一条说的是什么"；找不到就是用户指错了（404 语义）。"""

    for entry in iter_document_entries(view):
        if str(entry.entry_id) == entry_id:
            return entry.text
    raise UnknownEntryId(entry_id)


def scope_line(scope: ExplorationScope, *, focus: str | None = None) -> str:
    parts = [f"调研范围：{_SCOPE_LABELS.get(scope.kind, scope.kind.value)}"]
    if focus:
        parts.append(f"焦点：{focus}")
    if scope.text and scope.text.strip():
        parts.append(f"补充说明：{scope.text.strip()}")
    return "\n".join(parts)


def validate_inquiry(
    hypotheses: tuple[Hypothesis, ...] | list[Hypothesis],
    blind_spots: tuple[BlindSpot, ...] | list[BlindSpot],
    *,
    known_eids: frozenset[EvidenceId],
) -> None:
    """机械校验（领域构造之外的第二道，给重装当 `validation_error`）。

    只做两件机器判得了的事：`based_on` 必须落在账本里；不许出数量断言。
    空字段与接地一致性由领域构造器拦（它们先于这里跑）。
    """

    for item in (*hypotheses, *blind_spots):
        if isinstance(item, Hypothesis):
            unknown = [str(ref) for ref in item.based_on if ref not in known_eids]
            if unknown:
                raise ShapeError(
                    "假设引用了账本里没有的编号：" + "、".join(unknown),
                    local_id=item.local_id,
                )
        if find_numeric_assertion(item.statement) is not None:
            raise ShapeError(
                "模拟推演不许出数量断言（比例 / 百分比 / 调查显示）："
                "请改成可证伪的定性假设"
                f"（字段 statement，条目 {item.local_id}）",
                local_id=item.local_id,
            )


def candidate_card(candidate: Candidate) -> str:
    """一条候选渲染成给人/给材料看的卡片文本（进提示词与材料库的是同一份）。"""

    if candidate.kind == "hypothesis":
        basis = "、".join(str(ref) for ref in candidate.based_on) or "凭空推演（无据）"
        return "\n".join(
            (
                f"假设：{candidate.statement}",
                f"依据：{basis}",
                f"怎么被推翻：{candidate.falsification}",
                f"需要什么真实证据：{candidate.evidence_needed}",
            )
        )
    return "\n".join((f"盲区：{candidate.statement}", f"下一步：{candidate.next_step}"))


def candidate_material(
    candidate: Candidate, *, exploration_id: str
) -> tuple[ParsedMaterial, bytes]:
    """把一条"收下为材料"的候选合成一份材料草稿（不经过文件解析器）。

    去重键由 `material_sha(source_ref)` 给（变体键，与文件哈希隔离）——
    同一条候选重复收下在领域层就幂等，这里只保证万一进了 put 也不会翻倍。
    """

    text = candidate_card(candidate)
    content = text.encode("utf-8")
    kind_label = "假设" if candidate.kind == "hypothesis" else "盲区"
    source_ref = f"exploration:{exploration_id}#{candidate.candidate_id}"
    parsed = ParsedMaterial(
        origin=ORIGIN_EXPLORATION,
        title=f"调研{kind_label}：{_clip(candidate.statement)}",
        source_ref=source_ref,
        media_type="text/plain",
        bytes_len=len(content),
        chunks=(
            MaterialChunk(
                chunk_id=source_ref,
                ordinal=0,
                text=text,
                heading_path=(),
                anchor="调研产物",
            ),
        ),
        parser_id=PARSER_ID_EXPLORATION,
        parser_version=PARSER_VERSION_EXPLORATION,
    )
    return parsed, content


def material_sha(source_ref: str) -> str:
    """材料去重的**变体键**：与真实文件的 sha256 隔离（同文不同源不互相顶掉）。

    单参签名是冻结口径（`test_exploration.py:148-150` 钉着旧值）：实现走
    `material_synthesis.material_sha("exploration-material", …)`，输出逐字同。
    """

    return _namespaced_sha("exploration-material", source_ref)


def _clip(text: str, limit: int = 24) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
