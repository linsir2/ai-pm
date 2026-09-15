"""C5 Material Packet（材料包）。

进入生成链路的外部信息统一成这一种形态，让 M13 组装上下文时不必区分来源。
来源只有三个：检索命中自己的资料、联网搜到的、讨论区聊出来的。

**上传解析不是来源，是准备动作**：上传 → 解析 → 进资料库，之后被检索命中时才成为材料包。
"""

from collections.abc import Mapping

from pydantic import model_validator

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import Credibility, MaterialSourceType
from pmstudio.contracts.models.base import ContractModel

# 可信度由来源推断，不由调用方填（CONTRACTS C5 字段表）。
CREDIBILITY_BY_SOURCE: Mapping[MaterialSourceType, Credibility] = {
    MaterialSourceType.RETRIEVAL: Credibility.HIGH,
    MaterialSourceType.WEB: Credibility.MEDIUM,
    MaterialSourceType.DISCUSSION: Credibility.LOW,
}


class MaterialPacket(ContractModel):
    """一条材料。没有 `ref` 的材料不许进上下文（I4）。"""

    material_id: str
    source_type: MaterialSourceType
    content: str
    ref: str
    credibility: Credibility

    @model_validator(mode="after")
    def _check(self) -> "MaterialPacket":
        if not self.material_id:
            raise ContractViolation("material_id 不能为空")
        if not self.ref.strip():
            raise ContractViolation("材料必须带 ref——无来源不得进上下文（I4）")
        if not self.content.strip():
            raise ContractViolation("材料正文不能为空")

        expected = CREDIBILITY_BY_SOURCE[self.source_type]
        if self.credibility is not expected:
            raise ContractViolation(
                f"可信度由来源推断：{self.source_type.value} 只能是 {expected.value}，"
                f"不能填 {self.credibility.value}"
            )
        return self
