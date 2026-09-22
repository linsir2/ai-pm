"""领域/应用错误 → HTTP 状态码。只有这一处做这个映射。"""

from pmstudio.application.errors import ApplicationError, ModelCallFailed, PacketParseError
from pmstudio.domain.shared.errors import DomainError, ShapeError

CODE_STATUS = {
    "unknown_project": 404,
    "unknown_round": 404,
    "model_call_failed": 502,
    "model_packet_unparsable": 502,
}


def status_for(error: DomainError | ApplicationError) -> int:
    if error.code in CODE_STATUS:
        return CODE_STATUS[error.code]
    if isinstance(error, (PacketParseError, ModelCallFailed)):
        return 502
    if isinstance(error, ShapeError):
        return 422
    if isinstance(error, DomainError) and error.code.startswith("invariant:"):
        return 409
    return 500


def body_for(error: DomainError | ApplicationError) -> dict[str, object]:
    return {"code": error.code, "message": error.message, "details": error.details}
