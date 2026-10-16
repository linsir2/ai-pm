"""全局测试夹具：给"真 SQLite"用例装一个默认 owner 上下文（多用户改造 Task 5 / D2）。

D2 的取舍：owner 可以由仓储方法**显式传**，也可以从**环境上下文**取（`owner_scope`）。
生产里由 `require_user`（Task 6）在每个请求里设置上下文；测试里由这里装一个默认值，
让既有仓储 / e2e 用例不必逐处传 owner 就能保持绿。

想验证"缺上下文即抛 `missing_owner_scope`"的用例，自己 `clear_owner_scope()` 即可
（`tests/infrastructure/test_scoped_reads.py`）。
"""

import pytest

from pmstudio.domain.shared.ids import UserId
from pmstudio.infrastructure.sqlite import owner_scope

#: 测试默认 owner。真值不重要，只要跨调用稳定——写入盖章与读取过滤用同一个值。
TEST_OWNER = UserId("u-owner-scope-test")


@pytest.fixture(autouse=True)
def default_owner_scope():  # type: ignore[no-untyped-def]
    token = owner_scope.set_owner_scope(TEST_OWNER)
    yield TEST_OWNER
    owner_scope.clear_owner_scope(token)
