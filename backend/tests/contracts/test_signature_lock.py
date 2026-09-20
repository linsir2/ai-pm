"""端口签名锁测试：协议与实现必须形状一致，漂移即失败。

P0 核心防御机制。负例测试保证「故意引入漂移必须被抓」——
这正是 D1（write_document 实现比协议多 6 个参数）教训的回归防线。
"""

import inspect
from typing import Protocol, runtime_checkable

import pytest

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import VersionTrigger
from pmstudio.contracts.models.document import WritePayload
from pmstudio.contracts.models.results import WriteDocumentResult
from pmstudio.contracts.signature_lock import assert_port_implementation


@runtime_checkable
class _AppServicesPort(Protocol):
    async def write_document(
        self, trigger: VersionTrigger, payload: WritePayload
    ) -> WriteDocumentResult: ...


class _CorrectImpl:
    async def write_document(
        self, trigger: VersionTrigger, payload: WritePayload
    ) -> WriteDocumentResult:
        raise NotImplementedError


class _ExtraParamImpl:
    """D1 式漂移：实现多了 keyword-only 参数。签名锁必须抓住它。"""

    async def write_document(
        self,
        trigger: VersionTrigger,
        payload: WritePayload,
        *,
        group: object | None = None,
        answers: list | None = None,
    ) -> WriteDocumentResult:
        raise NotImplementedError


class _MissingMethodImpl:
    async def other(self) -> None:
        raise NotImplementedError


class _SyncImpl:
    """async 性质不一致。"""

    def write_document(
        self, trigger: VersionTrigger, payload: WritePayload
    ) -> WriteDocumentResult:
        raise NotImplementedError


def test_correct_implementation_passes() -> None:
    assert_port_implementation(_AppServicesPort, _CorrectImpl)


def test_extra_keyword_only_param_is_rejected() -> None:
    """D1 回归：实现多参数必须被抓。"""
    with pytest.raises(ContractViolation, match="参数数量不一致"):
        assert_port_implementation(_AppServicesPort, _ExtraParamImpl)


def test_missing_method_is_rejected() -> None:
    with pytest.raises(ContractViolation, match="缺少端口"):
        assert_port_implementation(_AppServicesPort, _MissingMethodImpl)


def test_async_mismatch_is_rejected() -> None:
    with pytest.raises(ContractViolation, match="async 性质不一致"):
        assert_port_implementation(_AppServicesPort, _SyncImpl)


def test_default_value_mismatch_is_rejected() -> None:
    @runtime_checkable
    class _Port(Protocol):
        async def trim(
            self, blocks: tuple[object, ...], project_id: str, model_ref: str
        ) -> object: ...

    class _Impl:
        async def trim(
            self, blocks: tuple[object, ...], project_id: str, model_ref: str = "x"
        ) -> object:
            raise NotImplementedError

    with pytest.raises(ContractViolation, match="默认值不一致"):
        assert_port_implementation(_Port, _Impl)


def test_return_type_mismatch_is_rejected() -> None:
    @runtime_checkable
    class _Port(Protocol):
        async def read(self) -> str: ...

    class _Impl:
        async def read(self) -> int:
            return 1

    with pytest.raises(ContractViolation, match="返回类型不一致"):
        assert_port_implementation(_Port, _Impl)


def test_subclass_return_type_is_allowed() -> None:
    class _Base:
        pass

    class _Sub(_Base):
        pass

    @runtime_checkable
    class _Port(Protocol):
        async def get(self) -> _Base: ...

    class _Impl:
        async def get(self) -> _Sub:
            return _Sub()

    assert_port_implementation(_Port, _Impl)  # 不抛
