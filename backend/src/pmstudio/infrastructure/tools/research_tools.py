"""有界研究工具（P3b-1）：把搜索 / 抓页 / 解析包成 `ToolInvoker`。

**它是 `run_tool_loop` 的第一个生产消费者**，所以三条纪律必须落在这里：

1. **只碰网络，不碰 DB**：材料入库在用例的最后一个 UoW（`StartWebResearch.execute`）。
   工具里一旦写库，"全有或全无"就不可能成立——搜到第 3 条失败时前 2 条已经落库了。
2. **来源上限要照实说**：收满 5 条之后，多出来的结果与抓取都要在**回灌给模型的文本里**
   写清"没收录、为什么"，不许静默截断（静默截断会让模型以为已经收下了）。
3. **失败抛 `ToolCallFailed`**（循环会把它回灌给模型，让它自己换一条路）；
   **取消原样抛**——`CancelledError` 不是工具失败，吞掉它等于让放弃按钮失效。

出处三件套（origin / source_ref / title）在这里补齐，与 `FetchMaterial` 同一条分工：
fetcher 管网络、parser 管字节→文字、"这份材料从哪来"由调用方盖。研究来源的 origin
是 `web_research`（P3a 的联网申请是 `web_search`，两者不同命名空间、各自去重）。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import replace

from pmstudio.application.dto import ParsedMaterial, ResearchHit
from pmstudio.application.material_refs import ORIGIN_WEB_RESEARCH
from pmstudio.application.ports import FetchedPage, MaterialParser, WebFetcher, WebSearchGateway
from pmstudio.domain.exploration.research import (
    MAX_RESEARCH_SOURCES,
    RESEARCH_SOURCE_CARD_CHARS,
)
from pmstudio.harness.errors import ToolCallFailed
from pmstudio.harness.tools.spec import ToolCall, ToolSpec
from pmstudio.infrastructure.tools.limits import (
    MAX_RESULTS_PER_SEARCH,
    RESEARCH_SNIPPET_CHARS,
    RESEARCH_TOOL_TEXT_CHARS,
)
from pmstudio.infrastructure.tools.webclean import normalize_web_text

#: 工具名。**冻结**：提示词（`research_loop.md`）与模型回包都按这两个词对号。
SEARCH_TOOL = "web_search"
FETCH_TOOL = "fetch_url"

#: 每条来源在**上下文/账本**里那 800 字正文的截断标记。与 `context` 里材料的省略号同形。
ELLIPSIS = "…"


class ResearchToolbox:
    """一次研究一个实例：`sources` 与 `attempts` 都是 per-run 状态。

    **没有"attempts 上限"**：调用次数由 `run_tool_loop` 管（单轮 5 / 全程 12），
    这里再立一个 12 只会让"第 13 次为什么没跑"有两套解释。`attempts` 只作留痕数字。
    """

    def __init__(
        self,
        *,
        search: WebSearchGateway | None,
        fetcher: WebFetcher | None,
        parser: MaterialParser,
    ) -> None:
        self._search = search
        self._fetcher = fetcher
        self._parser = parser
        self._sources: list[ResearchHit] = []
        self._seen: set[str] = set()
        self._attempts = 0
        self._overflowed = 0

    # —— 只读 ——

    @property
    def sources(self) -> tuple[ResearchHit, ...]:
        return tuple(self._sources)

    @property
    def attempts(self) -> int:
        """这一趟调了几次工具（**含失败**，用于留痕；不是限额）。"""

        return self._attempts

    @property
    def ready(self) -> bool:
        """搜索与抓取都接上了吗：研究两样都要（先搜、再读）。"""

        return self._search is not None and self._fetcher is not None

    @property
    def overflowed(self) -> int:
        """因为"来源已满"而没被收下的搜索结果条数（0 = 没发生）。

        消费者是探索 payload 的 `notes` 与界面：用户要能看出"这次其实搜到了更多"。
        """

        return self._overflowed

    def specs(self) -> tuple[ToolSpec, ...]:
        """给模型的两个工具。参数 schema 是模型读的说明，别写成 Python 签名。"""

        return (
            ToolSpec(
                name=SEARCH_TOOL,
                description=(
                    "联网搜索。返回若干条结果的标题、网址与摘要。"
                    "先用它找线索，再用 fetch_url 读真正需要的那几页。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索词"},
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_RESULTS_PER_SEARCH,
                            "description": "要几条结果（默认 5）",
                        },
                    },
                    "required": ["query"],
                },
            ),
            ToolSpec(
                name=FETCH_TOOL,
                description=(
                    "按网址抓取一个页面的正文。只抓搜索里真的需要的那几页；"
                    f"这一趟最多收 {MAX_RESEARCH_SOURCES} 条来源。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要抓的网址"},
                    },
                    "required": ["url"],
                },
            ),
        )

    # —— 执行 ——

    async def invoke(self, spec: ToolSpec, call: ToolCall) -> str:
        """执行一次工具调用。`spec` 只用来确认"这个工具我们给过"，分派看 `call.name`。"""

        if call.name == SEARCH_TOOL:
            return await self._search_tool(call.arguments)
        if call.name == FETCH_TOOL:
            return await self._fetch_tool(call.arguments)
        raise ToolCallFailed("没有这个工具", tool_id=call.name or "unknown")

    async def _search_tool(self, arguments: Mapping[str, object]) -> str:
        query = _text_argument(arguments, "query", tool=SEARCH_TOOL)
        wanted = _int_argument(arguments, "max_results", tool=SEARCH_TOOL)
        limit = MAX_RESEARCH_SOURCES if wanted is None else max(
            1, min(wanted, MAX_RESULTS_PER_SEARCH)
        )
        if self._search is None:
            raise ToolCallFailed(
                "服务端没有配置联网搜索：这一步先用已有材料判断", tool_id=SEARCH_TOOL
            )

        self._attempts += 1
        try:
            found = await self._search.search(query=query, limit=limit)
        except ToolCallFailed:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 —— 网络/供应商错一律翻成工具失败
            raise ToolCallFailed(
                f"搜索失败：{type(error).__name__}: {error}", tool_id=SEARCH_TOOL
            ) from error

        if not found:
            return f"查询「{query}」没有搜到结果。"

        lines: list[str] = []
        added = 0
        skipped_url = 0
        duplicate = 0
        overflow = 0
        oversized = 0
        for item in found:
            url = (item.source_ref or "").strip()
            if not url:
                skipped_url += 1
                continue
            if url in self._seen:
                duplicate += 1
                continue
            if len(self._sources) >= MAX_RESEARCH_SOURCES:
                overflow += 1
                continue
            hit = self._accept_search_hit(url, item)
            if hit is None:
                oversized += 1
                continue
            self._sources.append(hit)
            added += 1
            lines.append(f"[{len(self._sources)}] {hit.title}\n{hit.url}\n{_snippet(hit.excerpt)}")

        self._overflowed += overflow
        parts = [
            f"查询「{query}」返回 {len(found)} 条；本次收录 {added} 条"
            f"（累计 {len(self._sources)}/{MAX_RESEARCH_SOURCES}）。"
        ]
        if lines:
            parts.append("\n\n".join(lines))
        if overflow:
            parts.append(f"（另有 {overflow} 条未收：已达来源上限 {MAX_RESEARCH_SOURCES} 条）")
        if skipped_url:
            parts.append(f"（另有 {skipped_url} 条没有网址，无法引用，未收）")
        if duplicate:
            parts.append(f"（另有 {duplicate} 条是重复网址，未重复收录）")
        if oversized:
            parts.append(
                f"（另有 {oversized} 条标题或网址太长，装不进来源卡（≤"
                f"{RESEARCH_SOURCE_CARD_CHARS} 字），未收）"
            )
        return "\n".join(parts)

    async def _fetch_tool(self, arguments: Mapping[str, object]) -> str:
        url = _text_argument(arguments, "url", tool=FETCH_TOOL)
        if not url.startswith(("http://", "https://")):
            raise ToolCallFailed(f"只能抓 http/https 网址：{url}", tool_id=FETCH_TOOL)
        if url in self._seen:
            return f"这条网址已经收过了，不重复收：{url}"
        if len(self._sources) >= MAX_RESEARCH_SOURCES:
            raise ToolCallFailed(
                f"已达来源上限（{MAX_RESEARCH_SOURCES} 条），本次抓取未执行：{url}",
                tool_id=FETCH_TOOL,
            )
        if self._fetcher is None:
            raise ToolCallFailed(
                "服务端没有配置网页抓取：这一步先用搜索结果判断", tool_id=FETCH_TOOL
            )

        self._attempts += 1
        try:
            page = await self._fetcher.fetch(url=url)
        except ToolCallFailed:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            raise ToolCallFailed(
                f"抓取失败：{url}（{type(error).__name__}: {error}）", tool_id=FETCH_TOOL
            ) from error
        hit = self._accept_page(url, page)
        self._sources.append(hit)
        # 回灌给模型的是**正文**（≤4000 字），不是 800 字的来源卡：
        # 卡是给上下文与账本用的，工具这一趟要够模型判断"这页是不是我要的"。
        body = parsed_text(hit.parsed)
        return _clipped_text(body, len(body))

    # —— 内部 ——

    def _accept_search_hit(self, url: str, item: ParsedMaterial) -> ResearchHit | None:
        body = parsed_text(item)
        title = " ".join((item.title or url).split())
        excerpt = _card_excerpt(title=title, url=url, text=body)
        if excerpt is None:
            return None
        self._seen.add(url)
        return ResearchHit(
            url=url,
            title=title,
            excerpt=excerpt,
            # 搜索结果没有原始字节：入库按"清洗后的正文"存（P3a 同口径）。
            parsed=replace(item, origin=ORIGIN_WEB_RESEARCH, source_ref=url),
            content=body.encode("utf-8"),
        )

    def _accept_page(self, url: str, page: FetchedPage) -> ResearchHit:
        try:
            drafted = self._parser.parse(
                filename=page.title or page.final_url,
                media_type=page.media_type,
                data=page.content,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 —— 抽不出字也是"这个网址读不了"
            raise ToolCallFailed(
                f"这个网址读不出正文：{page.final_url}（{type(error).__name__}: {error}）",
                tool_id=FETCH_TOOL,
            ) from error
        # 出处三件套由调用方盖（与 `FetchMaterial` 同一条分工）；去重记最终地址，
        # 否则 http→https 的跳转会存出两份材料、两个 id。
        parsed = replace(
            drafted,
            origin=ORIGIN_WEB_RESEARCH,
            source_ref=page.final_url,
            title=" ".join((page.title or page.final_url).split()),
        )
        body = parsed_text(parsed)
        excerpt = _card_excerpt(title=parsed.title, url=page.final_url, text=body)
        if excerpt is None:
            # **先判后记**：装不下就什么都没收下，`_seen` 里不能留痕——
            # 否则模型换个说法再抓，会得到"这条网址已经收过了"（一句谎话）。
            raise ToolCallFailed(
                f"这个网址太长，装不进来源卡（≤{RESEARCH_SOURCE_CARD_CHARS} 字）："
                f"{page.final_url}",
                tool_id=FETCH_TOOL,
            )
        self._seen.add(url)
        self._seen.add(page.final_url)
        return ResearchHit(
            url=page.final_url,
            title=parsed.title,
            excerpt=excerpt,
            parsed=parsed,
            content=page.content,
        )


def parsed_text(parsed: ParsedMaterial) -> str:
    """一份材料抽出来的全文（块按文档序拼）。"""

    return "\n".join(chunk.text for chunk in parsed.chunks)


#: 摘要至少留这么多字，否则这条来源等于没摘要（宁可丢掉它、照实说，也不给一张空卡）。
MIN_EXCERPT_CHARS = 40

#: 网页里的内联媒体（图片 / data URI）：卡面与账本都不该带它们。
#: 真跑实测（2026-10-29）：某张卡的 800 字全是 `[![Xappex](data:image/svg+xml;base64,…)`。
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")
_DATA_URI = re.compile(r"data:[a-z/+.-]+;base64,[A-Za-z0-9+/=]{40,}")
#: 去掉链接壳之后还剩"字"吗（字母/数字/CJK）。一个都没有 → 这行是导航/装饰。
_HAS_WORD = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")


def _card_source_text(text: str) -> str:
    """卡面的正文：剥内联媒体与导航行，再过一遍 `webclean`。

    为什么要多做两步（真跑实测 2026-10-29）：某张卡的 800 字是
    `[![Xappex](data:image/svg+xml;base64,…)`，另一张卡开头是
    `* [](/ "数数学堂") * [首页](https://…)` —— `webclean` 只剔"整行只有一个链接"
    的行，剥完链接壳还剩 `*  * ` 这种行它不是链接行、但也不是内容。

    这一步只作用于**来源卡**（账本里那条 E 的正文），不改 Tavily 入库的清洗口径。
    """

    stripped = _DATA_URI.sub("", _MARKDOWN_IMAGE.sub("", text))
    kept: list[str] = []
    for line in stripped.split("\n"):
        bare = _MARKDOWN_LINK.sub("", line)
        if line.strip() and not _HAS_WORD.search(bare):
            continue
        kept.append(line)
    return normalize_web_text("\n".join(kept))


def _card_excerpt(*, title: str, url: str, text: str) -> str | None:
    """按**整卡**（标题 + 网址 + 摘要 ≤ `RESEARCH_SOURCE_CARD_CHARS`）裁摘要。

    标题与网址原样保留（前者是身份、后者是链接，截了都不如不给），
    所以留不出 40 字摘要时返回 `None`——调用方把这条当作"装不下"丢掉并计数。
    """

    title = " ".join(title.split())  # 标题里的换行会把"卡前一行是编号"的形状打散
    url = url.strip()
    room = RESEARCH_SOURCE_CARD_CHARS - len(title) - len(url) - 2  # 两个换行
    if room < MIN_EXCERPT_CHARS:
        return None
    flat = _card_source_text(text).strip()
    if len(flat) <= room:
        return flat
    return flat[: room - 1] + ELLIPSIS


def _snippet(text: str) -> str:
    flat = " ".join(text.split())
    if len(flat) <= RESEARCH_SNIPPET_CHARS:
        return flat
    return flat[: RESEARCH_SNIPPET_CHARS - 1] + ELLIPSIS


def _clipped_text(text: str, total: int) -> str:
    """回灌给模型的正文：超过工具预算就截断，**并把截断这件事说出来**。

    说明那几行**算在预算里**：否则"4000 字预算"实际是 4000 + 说明，量出来就比写的大。
    """

    if len(text) <= RESEARCH_TOOL_TEXT_CHARS:
        return text
    room = RESEARCH_TOOL_TEXT_CHARS
    for _ in range(3):  # 说明里带 room 本身：迭代到长度不再变
        note = (
            f"\n（已截断：正文共 {total} 字，只给了前 {room} 字；"
            "全文已入库（供人查看），本次不再重复抓取）"
        )
        room = RESEARCH_TOOL_TEXT_CHARS - len(note)
    return text[:room] + note


def _text_argument(arguments: Mapping[str, object], name: str, *, tool: str) -> str:
    value = dict(arguments).get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolCallFailed(f"参数 {name} 必须是非空字符串", tool_id=tool)
    return value.strip()


def _int_argument(arguments: Mapping[str, object], name: str, *, tool: str) -> int | None:
    value = dict(arguments).get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolCallFailed(f"参数 {name} 必须是整数", tool_id=tool)
    return value
