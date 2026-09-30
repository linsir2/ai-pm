"""联网搜索：Tavily 的 REST 接口（直接用 httpx，不引 SDK）。

**每条结果 → 一个信息包**（`origin="web_search"`，`source_ref` 是 URL，
`parser_id="tavily"`），这样下游（存储、编号、渲染、检索、引用）与上传材料**走同一条路**。

三条规矩：
① key 只进请求体，**绝不进信息包**（也不进 warnings）；
② 单条 `content` 截到 `WEB_SNIPPET_CHARS`（实测 Tavily 单条 ≈1.1k 字符，不截会撑爆提示词预算）；
③ 失败抛 `ToolCallFailed`：超时 / 非 2xx / 非 JSON / 缺 results——**不吞、不假装搜过**。
"""

import json

import httpx

from pmstudio.application.context import WEB_SNIPPET_CHARS
from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.application.errors import ToolCallFailed

TOOL_ID = "tool.web_search"
PARSER_ID = "tavily"
PARSER_VERSION = "tavily-rest-v1"


class TavilySearchGateway:
    ENDPOINT = "https://api.tavily.com/search"

    def __init__(
        self,
        *,
        api_key: str,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            # 没配密钥就不该有这一步（工具目录已经把它过滤掉了）；真走到这儿也明确报错
            raise ToolCallFailed("没有配置 Tavily 密钥，联网搜索不可用", tool_id=TOOL_ID)
        self._api_key = api_key.strip()
        self._timeout = timeout
        self._client = client

    async def search(self, *, query: str, limit: int) -> tuple[ParsedMaterial, ...]:
        payload = {
            "api_key": self._api_key,
            "query": query,
            "max_results": limit,
            "search_depth": "basic",
        }
        try:
            if self._client is not None:
                response = await self._client.post(self.ENDPOINT, json=payload)
            else:  # pragma: no cover - 真跑走这条（测试都注入 client）
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(self.ENDPOINT, json=payload)
        except httpx.HTTPError as error:
            raise ToolCallFailed(
                f"联网搜索失败：{type(error).__name__}", tool_id=TOOL_ID
            ) from error

        if response.status_code != 200:
            raise ToolCallFailed(
                f"联网搜索失败：HTTP {response.status_code}",
                tool_id=TOOL_ID,
                status=response.status_code,
            )
        try:
            body = json.loads(response.text)
        except json.JSONDecodeError as error:
            raise ToolCallFailed("联网搜索结果不是 JSON", tool_id=TOOL_ID) from error
        results = body.get("results")
        if not isinstance(results, list):
            raise ToolCallFailed("联网搜索结果里没有 results", tool_id=TOOL_ID)

        return tuple(_packet(item, query=query) for item in results if isinstance(item, dict))


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
