"""错误基类。"""


class PmStudioError(Exception):
    """本系统所有异常的根。"""


class ContractViolation(PmStudioError, ValueError):
    """契约被违反：构不出合法对象（字段组合不合法、引用了不存在的目标……）。

    同时继承 `ValueError`：在 pydantic 校验器里抛出时会被包成 `ValidationError`
    （构造对象时的统一出口），在普通函数里抛出时就是它自己。
    """


class GenerationFailure(PmStudioError):
    """一次生成失败。

    C13 要求 `generation.failed` 必须带三样：失败在哪一步、人话的原因、能不能重试。
    """

    def __init__(self, step: str, reason: str, retryable: bool) -> None:
        self.step = step
        self.reason = reason
        self.retryable = retryable
        super().__init__(f"[{step}] {reason} (retryable={retryable})")


class VersionConflict(ContractViolation):
    """写入时目标块的版本对不上——用户手改过，不许静默覆盖（I10）。"""


class BlockNotFound(PmStudioError):
    """要写的块不存在。"""


class EventDispatchOverflow(PmStudioError):
    """一次发布引发的分发条数超过上限——多半是订阅者之间形成了环。

    与其把进程挂死，不如当场炸在调用方，并且流水里留着"发过哪些"。
    """


class SingleInstanceViolation(PmStudioError):
    """同一个库已经有一个进程打开着。

    两个进程各写一半，比开不起来更糟；所以启动时拿锁，拿不到就说清是谁占着。
    """
