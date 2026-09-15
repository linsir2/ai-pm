"""契约模型的公共基类。"""

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """所有契约对象的基类。

    - `frozen=True`：I2"确认即冻结"变成类型事实；改动靠产出新实例
    - `extra="forbid"`：字段名写错当场炸，而不是静默丢字段
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
