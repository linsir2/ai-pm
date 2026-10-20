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
from dataclasses import replace

import httpx

from pmstudio.application.dto import ParsedMaterial
from pmstudio.harness.errors import ToolCallFailed
from pmstudio.infrastructure.tools.extract import chunk_text
from pmstudio.infrastructure.tools.fetch import _backoff
from pmstudio.infrastructure.tools.limits import WEB_CHUNK_CHARS
from pmstudio.infrastructure.tools.webclean import normalize_web_text
from pmstudio.infrastructure.tools.webdedup import dedupe_by_content

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
        # 注意：`timeout` 只作用于**本类自建**的 client（下面 `search` 里那条）。
        # 注入 `client` 时（测试用）超时**由调用方负责**——我们不该去改别人给的 client。
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
        packets = tuple(
            _packet(item, query=query) for item in results if isinstance(item, dict)
        )
        # **批内去重**（两层：字节级 + 近重复 5-gram Jaccard ≥0.2，阈值是实测定的）。
        # 为什么在网关这一层：同一批结果里常有几条说的是同一件事（多家站转载同一条
        # 法条），它们会白占预算与提示词——"这次搜索返回了这些结果"本来就该是去重后的。
        # 去重保留**第一条**（Tavily 的顺序就是相关序，不能打乱）。
        # 实测（2026-10-19，50 条真结果）：precision 1.000、recall 0.159——
        # 它抓的是"抽到了同一段"，不是"同一主题"（同主题不同内容**必须**留着）。
        # 跨查询的重复由 URL 级去重兜着（`SubmitOpening._keep` 按 `web:{url}` 找已有材料）。
        return tuple(dedupe_by_content(packets))


def _packet(item: dict, *, query: str) -> ParsedMaterial:
    """一条 Tavily 结果 → 一个信息包（**整段清洗后切块**，不截断）。

    ## 为什么不再截到 300 字（2026-10-19 实测定的）

    真跑量过：Tavily 的 `content` 实给 **568–2302 字**（basic / advanced 两档），
    而旧实现截到 300 字——**扔掉 50–78% 的内容**。那 300 字本来是想控制"进提示词
    的量"，但那是**预算层**该管的事（`WEB_BUDGET` + `select_chunks`），
    入库层管它就是**职责混淆**：库里只有前 300 字，F6 就搜不到后面的，
    向量也只覆盖前 512 token。

    ## 切块而不是整段一块

    整段一块（1400 字）会超出向量模型的 512-token 上限（中文约 1 字 1 token），
    实测**尾部对向量完全不可见**（embed(700字) 与 embed(前510字) 余弦 1.000000）。
    按 `WEB_CHUNK_CHARS`（400）切开后，每块的向量都覆盖得到自己那段。

    顺带白捡：`content` 里的 `## 标题` 会被 `chunk_text` 认成标题 →
    **`heading_path` 不再恒空**（章节链进索引源，"只在这一节出现的内容"才搜得到）。
    """

    title = str(item.get("title") or "（没有标题）").strip()
    url = str(item.get("url") or "").strip()
    body = str(item.get("content") or "")
    warnings = [f"来自检索：{query}"]
    if not url:
        # 没有链接的结果**照样收**（内容可能有用），但要说清"它没有出处可点"
        warnings.append("这条结果没有链接")
    cleaned = normalize_web_text(body)
    raw_chunks = chunk_text(cleaned, parser_id=PARSER_ID, max_chars=WEB_CHUNK_CHARS)
    # 锚点用"第 N 段"而不是 `chunk_text` 给的"第 N–M 行"：联网摘要没有"行号"
    # 这回事，用户跳不到网页的第 5 行。
    chunks = tuple(
        replace(chunk, anchor=f"网页摘要 · 第 {index} 段")
        for index, chunk in enumerate(raw_chunks, start=1)
    )
    return ParsedMaterial(
        origin="web_search",
        title=title,
        source_ref=url or "（没有链接）",
        media_type="text/html",
        bytes_len=len(body.encode("utf-8")),
        chunks=chunks,
        parser_id=PARSER_ID,
        parser_version=PARSER_VERSION,
        truncated=False,
        warnings=tuple(warnings),
    )
