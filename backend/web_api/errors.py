"""API 层异常映射。

`GenerationFailure`（模型不可用 / 密钥缺失 / 输出落不了位）→ **503**：可恢复，界面要能说清
"失败在哪一步、人话的原因、能不能重试"（AC12；docs/redesign/02-contracts.md §2.5）。
"""

from fastapi import HTTPException

from pmstudio.common.errors import ContractViolation, GenerationFailure


def map_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, ContractViolation):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, GenerationFailure):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="Internal server error")
