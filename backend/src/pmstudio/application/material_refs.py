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
