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

#: 每次模型调用的墙钟上限（秒）。**按次算**，不是按一次业务动作算：
#: 一次业务调用最多 3 次尝试 ⇒ 最坏 3 × 90 + 退避(1+3) ≈ 4.6 分钟。
#: 为什么不是 120：P5c 实测（`docs/plans/2026-09-28-P5c-*.md` §0.1）只传 `timeout` 会被
#: SDK 的内部重试放大到约 4 倍；网关同时传 `num_retries=0` 之后，墙钟才等于这个数。
DEFAULT_MODEL_TIMEOUT = 90.0


class ConfigError(RuntimeError):
    pass


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
        )
