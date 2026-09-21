"""API 层错误映射：契约违规 400、生成失败 503、参数形状 422、其余 500。

口径在 `docs/redesign/02-contracts.md` §2.5：`GenerationFailure` 是**可恢复**的失败
（模型不可用 / 密钥缺失 / 输出落不了位），界面要能展示"哪一步、人话的原因、能不能重试"（AC12），
所以它必须是 503——归进 500 就等于把它当成程序缺陷，用户看到的是"内部错误"。
"""

from pmstudio.common.errors import ContractViolation, GenerationFailure, VersionConflict
from web_api.errors import map_exception


def test_contract_violation_is_400() -> None:
    assert map_exception(ContractViolation("写范围外的字段")).status_code == 400


def test_generation_failure_is_503() -> None:
    mapped = map_exception(
        GenerationFailure("模型返回的 JSON 一条提案都落不了位", retryable=True, step="drafting")
    )

    assert mapped.status_code == 503
    assert "落不了位" in mapped.detail


def test_value_error_is_422() -> None:
    assert map_exception(ValueError("参数形状错")).status_code == 422


def test_unknown_exception_is_500() -> None:
    assert map_exception(RuntimeError("boom")).status_code == 500


def test_version_conflict_is_still_a_contract_violation() -> None:
    """I10 的版本冲突是 `ContractViolation` 的子类 → 400，别被 503 抢走。"""
    conflict = VersionConflict("版本对不上，可能被手改过")

    assert isinstance(conflict, ContractViolation)
    assert map_exception(conflict).status_code == 400
