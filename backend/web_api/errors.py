"""API 层异常映射。"""

from fastapi import HTTPException

from pmstudio.common.errors import ContractViolation


def map_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, ContractViolation):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="Internal server error")
