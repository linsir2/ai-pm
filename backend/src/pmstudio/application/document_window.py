"""上下文窗口：**一处决定「谁看到什么」，两件事同源**（`02` §11.0.61）。

这个模块只为一件事存在——**一条不变量**：

    模型看不见的编号，校验就不该放行。

改造前 5 个调用点全拿「全账本」当 `known_eids`，于是「账本里有、提示词里没有」
的编号一律放行。真跑实测 955 个席位引用里 **8 个是纯幻觉**（整个提示词里根本
没有该编号，模型在编号空间上外推），全部静默通过。窄化之后这条路必须堵死，
否则窄化就等于悄悄拆掉引用底座。

三条不许破的规矩：

1. **`full` lens 的输出与改造前逐字相同**——零回归是验收线；
2. **无相关性信号不猜**——§J-A 的教训（无信号 + 暗示该归零 = 归零失败）。
   所以只有两种信号才切片：`features`（走查对象）与 `topic`（线程主题），
   其余一律全文；
3. **窄化必须照实说**——骨架 + `NARROWED_NOTE` + skipped 计数。骨架是模型能
   诚实说"那部分没给我看"的唯一依据。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from pmstudio.application import context
from pmstudio.domain.authoring.views import BlockView, DocumentView
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.shared.ids import EvidenceId

#: 扫描一段文本里出现过哪些编号。**与 `evidence_refs.EID` 是同一套写法**，
#: 那边带首尾锚（校验单个串），这边不带（扫一整段）。别新增第三种写法。
_SCAN_EID = re.compile(r"[EeＥｅ]\s*0*(\d+)")


@dataclass(frozen=True, slots=True)
class DocumentWindow:
    """一次调用里这个智能体看到的文档，**以及它可以引用的编号**。

    这两件事必须同源，所以绑在一个对象里——分开传就一定会漂。
    """

    text: str
    visible_eids: frozenset[EvidenceId]

    def known_eids(self, ledger: EvidenceLedger) -> frozenset[EvidenceId]:
        """账本 ∩ 看得见 = 它**可以**引用的编号。

        交集而不是并集：账本里有但没渲染的，模型没看见，引了就是编。
        """

        return frozenset(e for e in self.visible_eids if e in self.ledger_ids(ledger))

    @staticmethod
    def ledger_ids(ledger: EvidenceLedger) -> frozenset[EvidenceId]:
        return frozenset(item.eid for item in ledger.evidences())


def visible_eids(*texts: str) -> frozenset[EvidenceId]:
    """这些文本里**出现过**哪些编号。

    只认文本，不碰账本：账本交集是 `known_eids()` 的职责（那里才知道账本）。
    分开是因为这两件事的答案会不一样——账本里有而文本里没有的，正是幻觉，
    而文本里有而账本里没有的，根本不该算数（`resolve_evidence_refs` 会先拒）。
    """

    found: set[EvidenceId] = set()
    for text in texts:
        for digits in _SCAN_EID.findall(text):
            found.add(EvidenceId(f"E{int(digits)}"))
    return frozenset(found)


def _entries(block: BlockView) -> list[object]:
    return list(context._entries_of(block))


def entries_matching_topic(view: DocumentView, topic: str) -> frozenset[str]:
    """主题命中的条目 id 集合；**命中 0 条时给空集**（由调用方回落全文）。

    怎么算命中：主题是"话题词"，它几乎不会整句出现在某条条目里。所以先试
    最长的一段前缀逐级缩短——真库实测「选区原文」能逐字命中，「用户手打的主题」
    命中 0 条并回落全文。
    """

    probe = " ".join(topic.split())
    if not probe:
        return frozenset()
    all_entries = [
        entry for section in view.sections for block in section.blocks
        for entry in context._entries_of(block)
    ]
    if not all_entries:
        return frozenset()
    # 从长到短试：整句 → 前 20 字 → 前 10 字。太短会命中一堆无关条目。
    probes = [probe, probe[:20], probe[:10]]
    best: frozenset[str] = frozenset()
    for candidate in probes:
        if len(candidate) < 6:
            break
        hit = frozenset(
            str(e.entry_id) for e in all_entries if candidate in e.text
        )
        if hit:
            return hit
        best = best or hit
    return best


def document_window(
    view: DocumentView,
    *,
    readiness: Sequence[object] = (),
    feature_slugs: frozenset[str] | None = None,
    topic: str | None = None,
) -> DocumentWindow:
    """这个智能体这一趟看到的文档 + 它可以引用的编号。

    `feature_slugs` 与 `topic` 都是相关性信号；**都没有就是全文**，不猜。
    """

    keep: frozenset[str] | None = None
    if feature_slugs is None and topic:
        hits = entries_matching_topic(view, topic)
        # 命中 0 条 → 回落全文：拿不到收益，但绝不能因此丢依据。
        if hits:
            keep = hits
    text = context.render_document_state(
        view, readiness=readiness, feature_slugs=feature_slugs, keep_entry_ids=keep
    )
    return DocumentWindow(text=text, visible_eids=visible_eids(text))
