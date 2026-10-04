"""回包的两个前置判定：剥围栏 + 是不是 JSON。

搬自 `infrastructure/llm/litellm_gateway.py:307-322`（2026-10-04）。
**当前只有网关一个调用方**（回包出口判一次）；解析那条路走的是 `normalize.load_shell`
的 `json.loads`，不再经这里——所以这里现在只服务"网关该不该把这一包当 JSON 交给下游"。

**这两个函数只判形状，不判内容**：内容对不对是 pydantic 与归一化引擎的事。
"""

import json


def strip_fence(text: str) -> str:
    """模型经常把 JSON 包在 ```json 围栏里——围栏剥掉，别让下游在围栏上炸。"""

    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.rsplit("```", 1)[0]
    return body.strip()


def is_json(text: str) -> bool:
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return False
    return True
