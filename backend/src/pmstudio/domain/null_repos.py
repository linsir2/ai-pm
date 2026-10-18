"""仓储协议的空对象实现：什么都不存，读什么都空。

用于替代 Optional[Repo] + if repo is not None 守卫模式，
让用例签名从 "可选能力" 变成 "必有依赖（可为空操作）"。
"""


class NoOpObservationRepository:
    """L2 观察仓储的空实现。"""

    def save(self, observation) -> None:
        pass

    def save_batch(self, observations) -> None:
        pass

    def recent(self, project_id: str, limit: int | None = None):
        return ()

    def increment_relevance(self, obs_id: str) -> None:
        pass

    def evict_least_relevant(self, project_id: str, keep: int = 200) -> None:
        pass


class NoOpDigestRepository:
    """L4 摘要仓储的空实现。"""

    def save(self, digest) -> None:
        pass

    def recent(self, project_id: str, limit: int | None = None):
        return ()


class NoOpInspirationRepository:
    """L3 灵感仓储的空实现。"""

    def save(self, item) -> None:
        pass

    def save_batch(self, items) -> None:
        pass

    def recent(self, project_id: str, limit: int | None = None):
        return ()


# 模块级单例：用例默认值引用这些实例，不在参数里调构造函数。
NO_OBSERVATIONS = NoOpObservationRepository()
NO_DIGESTS = NoOpDigestRepository()
NO_INSPIRATIONS = NoOpInspirationRepository()
