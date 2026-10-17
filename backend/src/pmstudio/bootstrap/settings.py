"""配置只从环境变量读。缺模型配置就报错——不写"无密钥降级"分支。"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

MODEL_ENV = "PMSTUDIO_MODEL"
KEY_ENV = "PMSTUDIO_API_KEY"
BASE_ENV = "PMSTUDIO_API_BASE"
DB_ENV = "PMSTUDIO_DB"
TIMEOUT_ENV = "PMSTUDIO_MODEL_TIMEOUT"
TRACE_ENV = "PMSTUDIO_TRACE_MODEL"
PARAMS_ENV = "PMSTUDIO_MODEL_PARAMS"
TAVILY_ENV = "PMSTUDIO_TAVILY_API_KEY"
SECRET_ENV = "PMSTUDIO_SECRET_KEY"
FIRST_ADMIN_USER_ENV = "PMSTUDIO_FIRST_ADMIN_USER"
FIRST_ADMIN_PASSWORD_ENV = "PMSTUDIO_FIRST_ADMIN_PASSWORD"
SESSION_TTL_ENV = "PMSTUDIO_SESSION_TTL_DAYS"
SESSION_ABSOLUTE_ENV = "PMSTUDIO_SESSION_ABSOLUTE_DAYS"
LOGIN_MAX_FAILS_ENV = "PMSTUDIO_LOGIN_MAX_FAILS"
LOGIN_LOCK_ENV = "PMSTUDIO_LOGIN_LOCK_MINUTES"
REDIS_URL_ENV = "PMSTUDIO_REDIS_URL"
REDIS_TIMEOUT_ENV = "PMSTUDIO_REDIS_TIMEOUT"
REDIS_MAX_CONNECTIONS_ENV = "PMSTUDIO_REDIS_MAX_CONNECTIONS"
LOGIN_IP_MAX_ENV = "PMSTUDIO_LOGIN_IP_MAX_ATTEMPTS"
LOGIN_USER_MAX_ENV = "PMSTUDIO_LOGIN_USER_MAX_ATTEMPTS"
LOGIN_ATTEMPT_WINDOW_ENV = "PMSTUDIO_LOGIN_ATTEMPT_WINDOW_SECONDS"

#: 会话滑动 / 绝对上限（天）与登录防爆阈值。多用户改造 §0.10/§0.11。
SESSION_TTL_DAYS = 30
SESSION_ABSOLUTE_DAYS = 90
LOGIN_MAX_FAILS = 5

#: 登录哈希隔离舱容量。默认 `min(4, CPU)`——4 个并发 argon2 约占 256MB，
#: 再多对单事件循环里的登录吞吐没有额外收益，只挤占内存与 CPU。
#: 与 `@fontsource` 无关、与密码强度无关：**它是内存与延迟的旋钮，不是安全参数**，
#: 调小不会让密码更好猜，调大也不会（argon2 的 cost 参数才是那个旋钮）。
LOGIN_HASH_CONCURRENCY_ENV = "PMSTUDIO_LOGIN_HASH_CONCURRENCY"
LOGIN_HASH_CONCURRENCY = max(1, min(4, os.cpu_count() or 1))
LOGIN_LOCK_MINUTES = 15

#: 登录**尝试**速率闸（per-IP / per-username），与上面的失败锁是两个维度：
#: 失败锁只记失败，所以"单 IP 喷 10000 个用户名"（每个只错 1 次）和
#: "1000 个 IP 撞一个账号"（每个只错 1 次）都能绕过它。
LOGIN_IP_MAX_ATTEMPTS = 20
LOGIN_USERNAME_MAX_ATTEMPTS = 10
LOGIN_ATTEMPT_WINDOW_SECONDS = 60

#: Redis（登录限流的跨 worker 共享计数）。**留空 = 用进程内计数**：
#: 单 worker 开发够用，但多 worker 下锁不住——`dev.py` 会就此打警告。
REDIS_TIMEOUT = 0.3
#: 连接池上界。redis-py 的 asyncio 池**默认 100**，而我们的并发目标就是 200+，
#: 实测 200 并发会直接 `MaxConnectionsError`。每次 Redis 调用约 1.3ms，
#: 所以 256 足够 200 并发同时各占一条连接。
REDIS_MAX_CONNECTIONS = 256

#: 每次模型调用的墙钟上限（秒）。**按次算**，不是按一次业务动作算：
#: 一次业务调用最多 3 次尝试 ⇒ 最坏 3 × 150 + 退避(1+3) ≈ 7.6 分钟。
#: 为什么是 150：**按 167 次真实调用的耗时定的**——p50 18.5s / p95 89.9s /
#: p99 131.3s / 最大 143.0s。90 秒会把最慢那几条**本来能成功**的调用判死；
#: 300 秒则只是在多等 150 秒去看一个必然超时的结果（失败调用的耗时中位恰好
#: 300.2s、最大 900.5s = 300 × 3 次尝试），而且占住隔离舱的槽位。
#: 病态挂起由熔断兜底，不靠拉长单次超时。
DEFAULT_MODEL_TIMEOUT = 150.0


class ConfigError(RuntimeError):
    pass


def _positive_float(
    source: Mapping[str, str], name: str, default: float, *, minimum: float = 0.0
) -> float:
    raw = source.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise ConfigError(f"{name} 要是秒数，现在是 {raw!r}") from error
    if value <= minimum:
        raise ConfigError(f"{name} 必须大于 {minimum}，现在是 {value}")
    return value


def _positive_int(source: Mapping[str, str], name: str, default: int) -> int:
    raw = source.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigError(f"{name} 要是整数，现在是 {raw!r}") from error
    if value <= 0:
        raise ConfigError(f"{name} 必须大于 0，现在是 {value}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    db_path: Path
    model: str
    api_key: str | None
    api_base: str | None
    model_timeout: float = DEFAULT_MODEL_TIMEOUT
    #: 把每次模型调用（提示词原文 + 回复）写进 `model_calls`：默认关（库会长）。
    trace_model: bool = False
    #: 供应商特有的调用参数（JSON 对象），透传给 LiteLLM。
    #: 典型用法（DashScope 关思考）：`{"extra_body": {"enable_thinking": false}}`。
    #: 我们自己的键（model / messages / timeout / num_retries）永远覆盖它。
    model_params: Mapping[str, object] = field(default_factory=dict)
    #: 联网检索（工具层 C-2）：Tavily 的 key。**可缺省**——缺了 `tool.web_search` 不可用
    #: （复述卡不出现那一栏），而不是"假成功"。
    tavily_api_key: str | None = None
    #: 认证（多用户改造）：只从 env 读，真值不进库。`secret_key` 留给后续签名/CSRF 用。
    secret_key: str = ""
    #: 首管理员种子：两个都给且 users 表为空时建一个 active admin；否则不建（幂等）。
    first_admin_username: str | None = None
    first_admin_password: str | None = None
    session_ttl_days: int = SESSION_TTL_DAYS
    session_absolute_days: int = SESSION_ABSOLUTE_DAYS
    login_max_fails: int = LOGIN_MAX_FAILS
    #: 登录哈希隔离舱容量（高并发 P1）。argon2 每次约 64MB，这个数同时是内存上限。
    login_hash_concurrency: int = LOGIN_HASH_CONCURRENCY
    login_lock_minutes: int = LOGIN_LOCK_MINUTES
    #: 登录尝试速率闸（高并发 P3）。见模块常量处的"两个维度"说明。
    login_ip_max_attempts: int = LOGIN_IP_MAX_ATTEMPTS
    login_username_max_attempts: int = LOGIN_USERNAME_MAX_ATTEMPTS
    login_attempt_window_seconds: int = LOGIN_ATTEMPT_WINDOW_SECONDS
    #: 登录限流的跨 worker 共享计数（Redis）。**空串 = 只用进程内计数**。
    redis_url: str = ""
    redis_timeout: float = REDIS_TIMEOUT
    redis_max_connections: int = REDIS_MAX_CONNECTIONS

    def available_tool_keys(self) -> frozenset[str]:
        """当前环境**配好**了哪些工具的依赖（工具目录按它过滤）。"""

        keys: set[str] = set()
        if self.tavily_api_key is not None:
            keys.add("tavily_api_key")
        return frozenset(keys)

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, default_db: Path | None = None
    ) -> "Settings":
        source = dict(os.environ if env is None else env)
        model = source.get(MODEL_ENV, "").strip()
        if not model:
            raise ConfigError(
                f"没有配置模型：请设置 {MODEL_ENV}（LiteLLM 的模型名，如 openai/gpt-4o-mini）"
            )
        api_key = source.get(KEY_ENV, "").strip()
        if not api_key:
            raise ConfigError(f"没有配置密钥：请设置 {KEY_ENV}")
        db_path = Path(
            source.get(DB_ENV, "").strip()
            or str(default_db or Path.home() / ".pmstudio" / "pmstudio.db")
        )
        raw_timeout = source.get(TIMEOUT_ENV, "").strip()
        try:
            model_timeout = float(raw_timeout) if raw_timeout else DEFAULT_MODEL_TIMEOUT
        except ValueError as error:
            raise ConfigError(f"{TIMEOUT_ENV} 要是秒数，现在是 {raw_timeout!r}") from error
        if model_timeout <= 0:
            raise ConfigError(f"{TIMEOUT_ENV} 必须大于 0，现在是 {model_timeout}")
        trace_model = source.get(TRACE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
        raw_params = source.get(PARAMS_ENV, "").strip()
        model_params: dict[str, object] = {}
        if raw_params:
            try:
                parsed = json.loads(raw_params)
            except json.JSONDecodeError as error:
                raise ConfigError(f"{PARAMS_ENV} 要是 JSON 对象：{error.msg}") from error
            if not isinstance(parsed, dict):
                raise ConfigError(
                    f"{PARAMS_ENV} 要是 JSON 对象，现在给的是 {type(parsed).__name__}"
                )
            model_params = parsed
        return cls(
            db_path=db_path,
            model=model,
            api_key=api_key,
            api_base=source.get(BASE_ENV, "").strip() or None,
            model_timeout=model_timeout,
            trace_model=trace_model,
            model_params=model_params,
            tavily_api_key=source.get(TAVILY_ENV, "").strip() or None,
            secret_key=source.get(SECRET_ENV, "").strip(),
            first_admin_username=source.get(FIRST_ADMIN_USER_ENV, "").strip() or None,
            first_admin_password=source.get(FIRST_ADMIN_PASSWORD_ENV, "").strip() or None,
            session_ttl_days=_positive_int(source, SESSION_TTL_ENV, SESSION_TTL_DAYS),
            session_absolute_days=_positive_int(
                source, SESSION_ABSOLUTE_ENV, SESSION_ABSOLUTE_DAYS
            ),
            login_max_fails=_positive_int(source, LOGIN_MAX_FAILS_ENV, LOGIN_MAX_FAILS),
            login_hash_concurrency=_positive_int(
                source, LOGIN_HASH_CONCURRENCY_ENV, LOGIN_HASH_CONCURRENCY
            ),
            login_lock_minutes=_positive_int(
                source, LOGIN_LOCK_ENV, LOGIN_LOCK_MINUTES
            ),
            login_ip_max_attempts=_positive_int(
                source, LOGIN_IP_MAX_ENV, LOGIN_IP_MAX_ATTEMPTS
            ),
            login_username_max_attempts=_positive_int(
                source, LOGIN_USER_MAX_ENV, LOGIN_USERNAME_MAX_ATTEMPTS
            ),
            login_attempt_window_seconds=_positive_int(
                source, LOGIN_ATTEMPT_WINDOW_ENV, LOGIN_ATTEMPT_WINDOW_SECONDS
            ),
            redis_url=source.get(REDIS_URL_ENV, "").strip(),
            redis_max_connections=_positive_int(
                source, REDIS_MAX_CONNECTIONS_ENV, REDIS_MAX_CONNECTIONS
            ),
            redis_timeout=_positive_float(
                source, REDIS_TIMEOUT_ENV, REDIS_TIMEOUT, minimum=0.001
            ),
        )
