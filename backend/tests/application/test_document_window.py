"""上下文窗口（2026-10-10，`02 §11.0.61`）：一处决定「谁看到什么」，两件事同源。

这个模块存在的理由是一条**不变量**：

    模型看不见的编号，校验就不该放行。

现状违反它——5 个调用点全拿「全账本」当 `known_eids`。真跑实测 955 个席位引用里
**9 个是纯幻觉**（整个提示词里根本没有该编号，模型在编号空间上外推），
现在全部静默通过。

三条不许破的规矩：

1. **`full` lens 的输出与改造前逐字相同**——零回归是验收线，不是"差不多"；
2. **无相关性信号不猜**——§J-A 的教训：无信号 + 暗示该归零 = 归零失败。
   所以只有 `features`（走查对象）与 `topic`（线程主题）这两种信号才切片；
3. **窄化必须照实说**——骨架 + `NARROWED_NOTE` + skipped 计数，不许让模型以为看全了。
"""

import dataclasses
from datetime import datetime

import pytest

from pmstudio.application import context
from pmstudio.application.document_window import (
    DocumentWindow,
    document_window,
    visible_eids,
)
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.authoring.views import DocumentView
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.shared.ids import (
    DocumentId,
    EntryId,
    EvidenceId,
    FeatureSlug,
    ProjectId,
    RoundId,
)
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp

NOW = datetime(2026, 9, 22, 10, 0)
PROJECT = ProjectId("p1")


def _add(
    entry_id: str, text: str, *, refs: tuple[str, ...] = (), section: str = "s4"
) -> WriteInstruction:
    return WriteInstruction(
        instruction_id=f"i-{entry_id}",
        entry_id=EntryId(entry_id),
        op=WriteOp.ADD,
        target=Target.body(section),
        text=text,
        refs=tuple(EvidenceId(r) for r in refs),
        kind=BlockKind.TEXT,
    )


def _ledger_with(*eids: str) -> EvidenceLedger:
    ledger = EvidenceLedger.empty(PROJECT)
    for n, eid in enumerate(eids, 1):
        ledger.record(
            kind=EvidenceKind.USER_INPUT,
            text=f"第{n}条输入",
            source=EvidenceSource(round_id=RoundId("r1"), raw_span=f"第{n}条输入"),
            now=NOW,
            eid=EvidenceId(eid),
        )
    return ledger


def doc_with_many_chapters() -> Document:
    """八章各一条共含一个宽泛词（真库 `数据` 命中 29 条、横跨 9 章的缩影）+
    s11 三条同域条目（单章条目多、不该被误杀）——测章数熔断。"""

    document = Document.new(
        document_id=DocumentId("doc1"), project_id=PROJECT, title="订单中台", now=NOW
    )
    document.apply(
        [
            _add("en-1", "同时出现在八章里的宽泛词 s1", refs=("E1",), section="s1"),
            _add("en-2", "同时出现在八章里的宽泛词 s2", refs=("E2",), section="s2"),
            _add("en-3", "同时出现在八章里的宽泛词 s3", refs=("E3",), section="s3"),
            _add("en-4", "同时出现在八章里的宽泛词 s4", refs=("E4",), section="s4"),
            _add("en-5", "同时出现在八章里的宽泛词 s5", refs=("E5",), section="s5"),
            _add("en-9", "同时出现在八章里的宽泛词 s6", refs=("E9",), section="s6"),
            _add("en-10", "同时出现在八章里的宽泛词 s7", refs=("E10",), section="s7"),
            _add("en-12", "同时出现在八章里的宽泛词 s8", refs=("E12",), section="s8"),
            # 第六章：条目多但不含那个宽泛词——按章数判时它不该被牵连
            _add("en-6", "未决问题域里的第一条", refs=("E6",), section="s11"),
            _add("en-7", "未决问题域里的第二条", refs=("E7",), section="s11"),
            _add("en-8", "未决问题域里的第三条", refs=("E8",), section="s11"),
        ],
        now=NOW,
        summary="干跑",
        authorized_new_features=frozenset(),
    )
    return document


def _all_eids(view: DocumentView) -> frozenset[str]:
    return visible_eids(context.render_document(view))


def doc_with_three_sections() -> Document:
    """三条分居 **三章**：s4 一条、s11 两条、s12 一条。

    s11 故意放两条（真库 s11「未决问题」也是这个形状）——章节级窄化要能把
    它们一起展开；三条挤在一章里就测不出"扩张到章"与"只给命中那条"的区别。
    """

    document = Document.new(
        document_id=DocumentId("doc1"), project_id=PROJECT, title="订单中台", now=NOW
    )
    document.apply(
        [
            _add("en-a", "退款要能按客户名找单", refs=("E1",)),
            _add("en-b", "告警阈值还没定", refs=("E2",), section="s11"),
            _add("en-b2", "告警触达渠道也没定", refs=("E3",), section="s11"),
            _add("en-c", "日报每天早上九点前发出", refs=("E4",), section="s12"),
        ],
        now=NOW,
        summary="干跑",
        authorized_new_features=frozenset(),
    )
    return document


# ── 规矩 1：`full` 逐字不变 ────────────────────────────────────────────


def test_full_lens_is_byte_identical_to_render_document_state() -> None:
    """零回归是验收线：`full` lens 必须与 `render_document_state` 逐字相同。

    比对同一个 view 的两次渲染，而不是"看起来差不多"。
    """

    view = doc_with_three_sections().view()
    from pmstudio.application import context

    assert (
        document_window(view).text == context.render_document_state(view)
    ), "full lens 改了既有输出——这是零回归线，不许破"


# ── 不变量：known == 账本 ∩ 看得见的 ───────────────────────────────────


def test_visible_eids_reads_only_what_the_text_shows() -> None:
    """只认文本里真出现的；账本交集是 `known_eids()` 的职责，不压在这里。"""

    seen = visible_eids("（E1 E2）", "另一段没有编号的话")

    assert {str(e) for e in seen} == {"E1", "E2"}


def test_known_eids_intersects_with_the_ledger() -> None:
    """**不变量落点**：账本里有、但没渲染出来的编号，不许进 `known`。"""

    ledger = _ledger_with("E1", "E2")
    window = DocumentWindow(text="（E1）", visible_eids=visible_eids("（E1）"))

    assert {str(e) for e in window.known_eids(ledger)} == {"E1"}


def test_window_reports_only_the_numbers_it_rendered() -> None:
    """不变量：`window.visible_eids` 必须是 `window.text` 里真出现过的编号。"""

    window = document_window(doc_with_three_sections().view())

    for eid in window.visible_eids:
        assert str(eid) in window.text, f"{eid} 不在渲染文本里"


# ── 规矩 3：topic 切片照实说 ───────────────────────────────────────────


def test_topic_lens_keeps_the_section_that_contains_the_topic() -> None:
    view = doc_with_three_sections().view()
    window = document_window(view, topic="日报每天早上九点前发出")

    assert "日报每天早上九点前发出" in window.text, "命中的那一条必须在"
    assert "退款要能按客户名找单" not in window.text, "没命中的不该在"


def test_topic_lens_says_out_loud_that_it_is_a_slice() -> None:
    """不许让模型以为看全了（`NARROWED_NOTE` + 骨架）。"""

    window = document_window(doc_with_three_sections().view(), topic="日报每天早上九点前发出")

    assert "不是全文" in window.text


def test_topic_lens_keeps_the_skeleton_so_the_model_can_say_it_was_not_shown() -> None:
    """骨架是模型能诚实说"那部分没给我看"的唯一依据。"""

    window = document_window(doc_with_three_sections().view(), topic="日报每天早上九点前发出")

    assert "## s4 范围与边界" in window.text, "章节标题必须在，即使它没被展开"


def test_topic_lens_widens_to_the_chapter_so_citations_are_not_starved() -> None:
    """窄化到**章**，不是命中的那一条（2026-10-10 §11.0.61 补）。

    真库实测：s11「未决问题」13 条，命中 1 条时条目级只给 **1 个**可引编号——
    讨论一涉及该章就只能说"没给我看"，真跑里卡片因此掉成 `invented`
    （而 s5 里其实有 `exceptions` 字段的条目，条目级看不见它）。
    s5 的账是：条目级 15 个可引 → 章节级 71 个。

    所以命中后要扩张到**它所在的章**：章是文档自己的组织单位，
    代价有上界（一章最多占全文 28%），收益是引用不再被饿死。
    """

    view = doc_with_three_sections().view()

    window = document_window(view, topic="告警阈值还没定")

    # 同章的另一条也必须进——否则这一章只有 1 个可引编号
    assert "告警触达渠道也没定" in window.text
    assert {"E2", "E3"} <= {str(e) for e in window.visible_eids}


def test_kept_entries_keep_their_field_header() -> None:
    """子块里的条目被留下时，**它所属字段的抬头必须一起留**。

    真库形状：s5 的功能块下挂着 `field=name` 这类子块，条目住在子块里。
    旧的 `_kept_block_lines` 把子块条目拍平到父标题下，`- name：` 丢了——
    模型就分不清这条是 name 还是 acceptance。引用不断（E 编号还在），
    但"这条是干什么的"丢了。
    """

    from pmstudio.domain.shared.target import Target

    document = Document.new(
        document_id=DocumentId("doc1"), project_id=PROJECT, title="订单中台", now=NOW
    )
    document.apply(
        [
            WriteInstruction(
                instruction_id="i-f",
                entry_id=EntryId("en-f1"),
                op=WriteOp.ADD,
                target=Target.feature_field(FeatureSlug("order-export"), "name"),
                text="把订单导出成表格",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.TEXT,
            ),
            WriteInstruction(
                instruction_id="i-f2",
                entry_id=EntryId("en-f2"),
                op=WriteOp.ADD,
                target=Target.feature_field(FeatureSlug("order-export"), "acceptance"),
                text="60 秒内完成",
                refs=(EvidenceId("E2"),),
                kind=BlockKind.TEXT,
            ),
            # s12 放一条毫不相关的——章节级扩张只扩命中条目的章，它不该被带进来
            _add("en-far", "验收标准：日报准时发送", refs=("E9",), section="s12"),
        ],
        now=NOW,
        summary="干跑",
        authorized_new_features=frozenset({FeatureSlug("order-export")}),
    )
    view = document.view()

    window = document_window(view, topic="把订单导出成表格")

    assert "把订单导出成表格" in window.text
    assert "- name" in window.text, "字段头必须留，否则分不清这是 name 还是 acceptance"
    # s12 那条字面不相关——章节级扩张只扩命中条目的章
    assert "验收标准：日报准时发送" not in window.text


def test_topic_lens_still_excludes_other_chapters() -> None:
    """扩张到章为止，不等于摊开全文档——跨章的编号仍然不许出现。"""

    view = doc_with_three_sections().view()

    window = document_window(view, topic="告警阈值还没定")

    assert "退款要能按客户名找单" not in window.text, "s4 的不该在"
    assert "日报每天早上九点前发出" not in window.text, "s12 的不该在"
    assert "E1" not in {str(e) for e in window.visible_eids}


def test_topic_lens_breaks_when_the_widening_gets_too_wide() -> None:
    """太宽就回落全文——**不许假装窄化**（2026-10-10 §11.0.63）。

    真库实测：宽泛词当 topic 会让章节级扩张退化到接近全文——
    `数据` 命中 29 条、横跨 9 章，窄化后仍是 **95%**。那种"窄化"是负收益：
    既没省 token，又让模型以为只看到了局部。

    按**章数**设熔断，不按条目数：s11 一个章就能有 13 条（真库），按条数会误杀。
    """

    view = doc_with_many_chapters().view()

    window = document_window(view, topic="同时出现在八章里的宽泛词")

    assert window.text == context.render_document_state(view), "太宽就回落全文，不是 95% 的假窄化"
    assert len(window.visible_eids) == len(_all_eids(view)), "回落时可引编号回到全量"


def test_topic_lens_breaks_on_more_than_the_threshold_of_entries() -> None:
    """门限按**章数**判：命中一条很长的章（s11 那类）不该被误杀。"""

    view = doc_with_many_chapters().view()

    # 只命中一章、但那章条目很多 → 照窄
    window = document_window(view, topic="未决问题域里的第一条")

    assert "未决问题域里的第一条" in window.text
    assert window.text != context.render_document_state(view)


def test_topic_matching_uses_overlap_not_verbatim_so_short_topics_hit() -> None:
    """**短 topic 必须能命中**——原来的探针只取前缀且短于 6 字就短路（`04` §L）。

    真库后果：`订单`（2 字）命中 0 条、永远回落全文，本该窄化到 45%。
    项目为材料挑块早解决过同一件事（`context._block_score` 的 docstring：
    「要求整句命中等于永远不命中」）——**这次修法就是复用它**。

    语义换成：**相关 = 字面重合度达到最高分一半以上的条目**。
    相对阈值自我标定，不需要任何魔法绝对阈值。
    """

    view = doc_with_three_sections().view()

    # 「退款」两字出现在 en-a 里，也出现在 en-b2 的「退款」…不，en-b2 是告警；
    # 用只出现在一条里的短词，钉住"短词也能命中"
    window = document_window(view, topic="退款")

    assert "退款要能按客户名找单" in window.text
    assert "不是全文" in window.text, "命中就该窄化，不是回落"


def test_topic_matching_relative_threshold_reproduces_verbatim_behavior() -> None:
    """长 topic 的行为**逐字不变**——相对阈值在长文本上等价于逐字命中（零回归）。

    真库实测：选区整条当 topic 时，相对阈值与旧的逐字匹配给出**同一个**结果
    （1 章 / 42%）。
    """

    view = doc_with_three_sections().view()

    window = document_window(view, topic="日报每天早上九点前发出")

    # 同章另两条（en-b/en-b2 在 s11）不该进——它们的字面重合度远低于一半
    assert "告警阈值还没定" not in window.text
    assert "E2" not in {str(e) for e in window.visible_eids}


def test_topic_matching_gives_nothing_for_a_single_character() -> None:
    """一个字构不成 2-gram → 空集 → 回落全文（`_query_grams` 的既有口径）。"""

    view = doc_with_three_sections().view()

    window = document_window(view, topic="款")

    assert window.text == context.render_document_state(view)


def test_topic_lens_falls_back_to_the_full_text_when_the_topic_is_nowhere() -> None:
    """匹配不上就回落全文——拿不到收益，但绝不能因此丢依据。"""

    view = doc_with_three_sections().view()
    from pmstudio.application import context

    window = document_window(view, topic="自由开聊时随手起的主题")

    assert window.text == context.render_document_state(view)


def test_topic_lens_never_lets_a_citation_outside_the_slice_pass() -> None:
    """**这是整个模块存在的那条不变量。**

    真跑实测：955 个席位引用里 9 个是幻觉——账本里有、提示词里没有，
    现在的校验全部放行。切片之后这条路必须被堵死。
    """

    window = document_window(doc_with_three_sections().view(), topic="日报每天早上九点前发出")

    assert "E1" not in {str(e) for e in window.visible_eids}, "E1 那条没渲染，就不该可引"
    assert "E4" in {str(e) for e in window.visible_eids}, "E4 那条渲染了，就该可引"


def test_window_is_immutable() -> None:
    """它会被到处传——可变就意味着某个调用方能悄悄换掉模型看到的东西。"""

    window = document_window(doc_with_three_sections().view())

    with pytest.raises(dataclasses.FrozenInstanceError):
        window.text = "改它"  # type: ignore[misc]
