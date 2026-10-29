"""材料块进账本的公共口径：**kind** 与**外部指称**。

两处发号（起轮带料 / 每步投影）必须共用这一份——各写一遍的话，
"探索材料被当成外部事实"这种错只会错一半。

放这里而不是 `use_cases.py`：`round_context.py` 也要用，而它不能反向 import
用例模块（会形成环）。
"""

from pmstudio.application.discussion import ORIGIN_DISCUSSION
from pmstudio.application.dto import MaterialChunk, MaterialPacket
from pmstudio.application.exploration import ORIGIN_EXPLORATION
from pmstudio.application.sim_inquiry import ORIGIN_SIMULATION
from pmstudio.domain.provenance.evidence import EvidenceKind

#: **一条结果一个编号**的联网材料（不是块级）。两个生产者、两个命名空间：
#: P3a 的联网申请（`TavilySearchGateway` 盖 `web_search`）与 P3b-1 的项目级研究
#: （`ResearchToolbox` 盖 `web_research`）。
#:
#: 它们**不进块级材料池、不接受普通带料**：混进去会破"编号 == 进提示词的块"
#: ——这条不变量是 P3a 的 `AttachMaterials` 当初为 `web_search` 立的规矩，
#: 研究来源只是第二个例子，不是新规矩。
ORIGIN_WEB_SEARCH = "web_search"
ORIGIN_WEB_RESEARCH = "web_research"


def is_web_result(origin: str) -> bool:
    """这份材料是不是"一条结果一个编号"（见上面两个 origin）。"""

    return origin in (ORIGIN_WEB_SEARCH, ORIGIN_WEB_RESEARCH)


def external_ref(packet: MaterialPacket, chunk: MaterialChunk) -> str:
    """材料块在账本里的"外部指称"：`attachment:需求.md#c00` / URL（不加后缀）。"""

    source_ref = packet.parsed.source_ref
    if source_ref.startswith("http"):
        return source_ref
    return f"{source_ref}#c{chunk.ordinal:02d}"


def material_kind(packet: MaterialPacket) -> EvidenceKind:
    """材料块进账本的 kind：探索/讨论/模拟产物是 `simulated`，其余 `external`。"""

    if packet.parsed.origin in (ORIGIN_EXPLORATION, ORIGIN_DISCUSSION, ORIGIN_SIMULATION):
        return EvidenceKind.SIMULATED
    return EvidenceKind.EXTERNAL
