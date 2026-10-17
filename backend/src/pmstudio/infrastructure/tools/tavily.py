"""联网搜索：Tavily 的 REST 接口（直接用 httpx，不引 SDK）。

**每条结果 → 一个信息包**（`origin="web_search"`，`source_ref` 是 URL，
`parser_id="tavily"`），这样下游（存储、编号、渲染、检索、引用）与上传材料**走同一条路**。

三条规矩：
① key 只进请求体，**绝不进信息包**（也不进 warnings）；
② 单条 `content` 截到 `WEB_SNIPPET_CHARS`（实测 Tavily 单条 ≈1.1k 字符，不截会撑爆提示词预算）；
③ 失败抛 `ToolCallFailed`：超时 / 非 2xx / 非 JSON / 缺 results——**不吞、不假装搜过**。
"""

import json
from collections.abc import Awaitable, Callable

import httpx

from pmstudio.application.context import WEB_SNIPPET_CHARS
from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.harness.errors import ToolCallFailed
from pmstudio.infrastructure.tools.fetch import _backoff

TOOL_ID = "tool.web_search"
PARSER_ID = "tavily"
PARSER_VERSION = "tavily-rest-v1"

#: Tavily 的 query 长度上限。**真跑实测**（2026-10-17）：2040 字回 400
#: （`Query is too long. Max query length is 1500 characters.`），1501 字回 200
#: ——墙是 1500（文档旧版写 400 已过期，真跑 > 文档）。漏斗在网关入口，截了才发。
MAX_QUERY_CHARS = 1500

#: 重试次数，与 `fetch.FETCH_ATTEMPTS` 同形。
MAX_ATTEMPTS = 3

#: 内含 500（Tavily 官方文档：500 是瞬时错，过几分钟重试）。
#: fetch 不含 500 是因为它防的是**用户粘来的任意网址**（500 常是站点自己坏了、持久）；
#: Tavily 是**固定可靠端点**，500 是瞬时。这个 delta 是有意的。
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

#: 瞬时异常：全套超时 + 连接错 + 协议错。fetch 只重试 `ReadTimeout`/`RemoteProtocolError`
#: （它要防证书错等"连不上就是连不上"），固定端点不必那么保守。
RETRYABLE_ERRORS = (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)


class TavilySearchGateway:
    ENDPOINT = "https://api.tavily.com/search"

    def __init__(
        self,
        *,
        api_key: str,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if not api_key.strip():
            # 没配密钥就不该有这一步（工具目录已经把它过滤掉了）；真走到这儿也明确报错
            raise ToolCallFailed("没有配置 Tavily 密钥，联网搜索不可用", tool_id=TOOL_ID)
        self._api_key = api_key.strip()
        self._timeout = timeout
        self._client = client
        #: 退避睡眠可注入（默认真睡）：测试传即时替身，重试用例才不慢。
        self._sleeper = sleeper

    async def search(self, *, query: str, limit: int) -> tuple[ParsedMaterial, ...]:
        query = query[:MAX_QUERY_CHARS]
        payload = {
            "api_key": self._api_key,
            "query": query,
            "max_results": limit,
            "search_depth": "basic",
        }
        if self._client is not None:
            return await self._search_with(self._client, payload, query=query)
        # 真跑走这条（测试都注入 client）
        async with httpx.AsyncClient(timeout=self._timeout) as client:  # pragma: no cover
            return await self._search_with(client, payload, query=query)

    async def _search_with(
        self, client: httpx.AsyncClient, payload: dict, *, query: str
    ) -> tuple[ParsedMaterial, ...]:
        """有界重试：超时 / 连接错 / 429 / 5xx 退避重试，其余立刻失败。"""

        for attempt in range(MAX_ATTEMPTS):
            last = attempt + 1 == MAX_ATTEMPTS
            try:
                response = await client.post(self.ENDPOINT, json=payload)
            except httpx.HTTPError as error:
                if not isinstance(error, RETRYABLE_ERRORS):
                    raise ToolCallFailed(
                        f"联网搜索失败：{type(error).__name__}", tool_id=TOOL_ID
                    ) from error
                if last:
                    raise ToolCallFailed(
                        f"联网搜索失败（试了 {MAX_ATTEMPTS} 次都没成）：{type(error).__name__}",
                        tool_id=TOOL_ID,
                    ) from error
                await self._sleep(attempt)
                continue
            if response.status_code == 200:
                return self._parse(response, query=query)
            if response.status_code not in RETRYABLE_STATUS:
                raise ToolCallFailed(
                    f"联网搜索失败：HTTP {response.status_code}",
                    tool_id=TOOL_ID,
                    status=response.status_code,
                )
            if last:
                raise ToolCallFailed(
                    f"联网搜索失败（试了 {MAX_ATTEMPTS} 次都没成）：HTTP {response.status_code}",
                    tool_id=TOOL_ID,
                    status=response.status_code,
                )
            await self._sleep(attempt, response.headers.get("retry-after"))
        raise AssertionError("不可能走到这里：循环里每条路都返回或抛出了")

    async def _sleep(self, attempt: int, retry_after: str | None = None) -> None:
        """退避，复用 fetch 的公式（`Retry-After` 优先、封顶 5s + full jitter）。"""

        if self._sleeper is None:
            await _backoff(attempt, retry_after)
        else:
            await _backoff(attempt, retry_after, sleep=self._sleeper)

    def _parse(self, response: httpx.Response, *, query: str) -> tuple[ParsedMaterial, ...]:
        try:
            body = json.loads(response.text)
        except json.JSONDecodeError as error:
            raise ToolCallFailed("联网搜索结果不是 JSON", tool_id=TOOL_ID) from error
        results = body.get("results")
        if not isinstance(results, list):
            raise ToolCallFailed("联网搜索结果里没有 results", tool_id=TOOL_ID)
        return tuple(
            _packet(item, query=query) for item in results if isinstance(item, dict)
        )


def _packet(item: dict, *, query: str) -> ParsedMaterial:
    """一条 Tavily 结果 → 一个信息包（单块：标题 + URL + 摘要）。"""

    title = str(item.get("title") or "（没有标题）").strip()
    url = str(item.get("url") or "").strip()
    body = str(item.get("content") or "").strip()
    warnings = [f"来自检索：{query}"]
    if not url:
        # 没有链接的结果**照样收**（内容可能有用），但要说清"它没有出处可点"
        warnings.append("这条结果没有链接")
    truncated = len(body) > WEB_SNIPPET_CHARS
    snippet = body[:WEB_SNIPPET_CHARS]
    if truncated:
        warnings.append("摘要过长（已截断）")
    return ParsedMaterial(
        origin="web_search",
        title=title,
        source_ref=url or "（没有链接）",
        media_type="text/html",
        bytes_len=len(body.encode("utf-8")),
        chunks=(
            MaterialChunk(
                chunk_id=f"{PARSER_ID}#c00",
                ordinal=0,
                text=snippet,
                heading_path=(),
                anchor="网页摘要",
            ),
        ),
        parser_id=PARSER_ID,
        parser_version=PARSER_VERSION,
        truncated=truncated,
        warnings=tuple(warnings),
    )
