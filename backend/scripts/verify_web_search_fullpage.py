"""核验批 6c 的**联网检索整页链路**：真 Tavily → 真入库 → 真检索。

**这不是测试替身**：真 Tavily REST（真 key）、真 `TavilySearchGateway`（真重试/退避/
去重/建包）、真 `SqliteMaterialStore`、真 `SqliteVectorIndex`（真 ONNX 嵌入模型）、
真 `SqliteHybridRetriever`（真 FTS5 + 真 sqlite-vec KNN）。

要证明的是**两件事**（方向相反，缺一不可）：

- **正向**：一个**只存在于整页、不在 Tavily 片段里**的词，入库后**必须搜得到**
  ——这证明"库里存的是整页"；
- **反向**：一个**只存在于片段、不在（清洗后的）整页里**的词，入库后**必须搜不到**
  ——这排除"库里其实还是片段"（万一是片段，这个词就会命中）。

外加：向量预算（`web_search` 限前 N 块）、`parser_version` 档位、`truncated`/`warnings`。

**原始响应怎么拿**：给网关注入一个**记录用的 client 包装**（真 httpx，只多记一份
响应体）——这样同一次调用既走了生产网关，又留住了 Tavily 的 `content`（片段）原文，
两边严格同源，对照才有意义。

跑法（需要 backend/.env 里的 Tavily key）：
    cd backend && uv run python -u scripts/verify_web_search_fullpage.py
"""

import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")

import httpx

from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
from pmstudio.infrastructure.sqlite.materials import (
    SqliteFtsRetriever,
    SqliteHybridRetriever,
    SqliteMaterialStore,
)
from pmstudio.infrastructure.sqlite.tokenize import index_tokens
from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
from pmstudio.infrastructure.tools.limits import WEB_VECTOR_CHARS
from pmstudio.infrastructure.tools.tavily import PARSER_VERSION, TavilySearchGateway

OUT = Path("../.codex/runs/2026-10-20/verify")
DB = OUT / "web_search.db"
PROJECT, OWNER = "p-web", "owner-web"
QUERY = "向量检索 企业知识库 召回效果"


class _RecordingClient:
    """真 httpx client 的薄包装：多记一份**原始响应体**（同源对照用）。"""

    def __init__(self, inner: httpx.AsyncClient) -> None:
        self._inner = inner
        self.raw_bodies: list[dict] = []

    async def post(self, url: str, *, json: dict) -> httpx.Response:  # noqa: A002
        response = await self._inner.post(url, json=json)
        try:
            self.raw_bodies.append(response.json())
        except Exception:  # noqa: BLE001 —— 记不下来就记不下来，不影响主流程
            pass
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def api_key() -> str:
    for line in Path(".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.strip().split("=", 1)
            if key == "PMSTUDIO_TAVILY_API_KEY":
                return value
    raise SystemExit("backend/.env 里没有 PMSTUDIO_TAVILY_API_KEY")


def _tokens(text: str) -> set[str]:
    return set(index_tokens(text).split())


def _probe_word(want_in: str, want_not_in: list[str], minimum: int = 3) -> str | None:
    """从 `want_in` 里挑一个词：长 ≥ `minimum`、含汉字/字母、且不在 `want_not_in` 里。

    **不挑纯数字串**（页面上到处是 ID 与时间戳，那种词命中了也说明不了什么）。
    """

    banned: set[str] = set()
    for text in want_not_in:
        banned |= _tokens(text)
    for word in sorted(_tokens(want_in)):
        if len(word) < minimum:
            continue
        if not re.search(r"[一-鿿A-Za-z]", word):
            continue
        if re.fullmatch(r"[0-9A-Za-z]{12,}", word):
            continue  # 长 ID / 时间戳：不是有意义的探针
        if word in banned:
            continue
        return word
    return None


def _probe_word_not_substring(want_in: str, absent_raw: list[str]) -> str | None:
    """反向探针：挑一个词，它在 `absent_raw` 里**连原始子串都不是**。

    为什么比"不是 token"更严：混合检索有**子串兜底**（`material_chunks.text LIKE ?`），
    它匹配的是**原始文本**。像 `logo` 这种词在整页里只以 `logo.png` 的子串形式出现——
    分词后不是独立 token，但 LIKE 会命中。反向判据必须按**原始子串**排除，
    否则会把兜底路径的宽松误判成"库里存的是片段"。
    """

    haystacks = [text.casefold() for text in absent_raw]
    for word in sorted(_tokens(want_in)):
        if len(word) < 3:
            continue
        if not re.search(r"[一-鿿A-Za-z]", word):
            continue
        if re.fullmatch(r"[0-9A-Za-z]{12,}", word):
            continue
        if any(word.casefold() in hay for hay in haystacks):
            continue
        return word
    return None



async def main() -> None:  # type: ignore[no-untyped-def]
    OUT.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()

    recorder = _RecordingClient(httpx.AsyncClient(timeout=60.0))
    gateway = TavilySearchGateway(api_key=api_key(), client=recorder)
    packets = await gateway.search(query=QUERY, limit=5)
    await recorder.aclose()
    print(f"真 Tavily 回了 {len(packets)} 份材料（解析器档位 {PARSER_VERSION}）")

    # 原始响应 → Tavily 自己的「片段版」正文（`content`），按 URL 对上
    fragment_by_url: dict[str, str] = {}
    for body in recorder.raw_bodies:
        for item in body.get("results", []) or []:
            if not isinstance(item, dict):
                continue
            fragment_by_url.setdefault(
                str(item.get("url") or ""), str(item.get("content") or "")
            )

    embedder = FastEmbedEmbedder()
    connection = connect(DB, embedder=embedder)
    store = SqliteMaterialStore(connection, ids=UuidIdGenerator())
    vectors = SqliteVectorIndex(connection, embedder=embedder)

    stored = []
    for packet in packets:
        digest = hashlib.sha256(f"web:{packet.source_ref}".encode()).hexdigest()
        packet_id = store.put(
            project_id=PROJECT,
            parsed=packet,
            sha256=digest,
            content="\n".join(chunk.text for chunk in packet.chunks).encode("utf-8"),
            now=datetime(2026, 10, 20, tzinfo=UTC),
            owner=OWNER,
        )
        vectors.write(packet_id.material_id)
        stored.append((packet, packet_id))
    connection.commit()

    fts = SqliteFtsRetriever(connection)
    hybrid = SqliteHybridRetriever(connection, embedder=embedder)

    report: dict[str, object] = {
        "query": QUERY,
        "parser_version": PARSER_VERSION,
        "web_vector_chars": WEB_VECTOR_CHARS,
        "materials": [],
        "summary": {},
    }

    page_only_hits = 0
    page_only_total = 0
    fragment_only_found = 0
    fragment_only_total = 0
    budget_ok = True

    for packet, packet_id in stored:
        stored_text = "\n".join(chunk.text for chunk in packet.chunks)
        fragment = fragment_by_url.get(packet.source_ref, "")
        page_only = _probe_word(stored_text, [fragment, packet.title])
        # 反向：词必须**连原始子串**都不在整页里（否则子串兜底会合法地命中）
        fragment_only = _probe_word_not_substring(
            fragment, [stored_text, packet.title]
        )
        vec_rows = connection.execute(
            "SELECT count(*) FROM material_chunks_vec v JOIN material_chunks c"
            " ON c.chunk_id = v.chunk_id WHERE c.material_id = ?",
            (packet_id.material_id,),
        ).fetchone()[0]
        chunk_rows = connection.execute(
            "SELECT count(*) FROM material_chunks WHERE material_id = ?",
            (packet_id.material_id,),
        ).fetchone()[0]
        vec_chars = connection.execute(
            "SELECT coalesce(sum(length(c.text)), 0) FROM material_chunks_vec v"
            " JOIN material_chunks c ON c.chunk_id = v.chunk_id"
            " WHERE c.material_id = ?",
            (packet_id.material_id,),
        ).fetchone()[0]
        entry: dict[str, object] = {
            "url": packet.source_ref,
            "title": packet.title,
            "material_id": packet_id.material_id,
            "stored_chars": len(stored_text),
            "fragment_chars": len(fragment),
            "chunks": chunk_rows,
            "vectors": int(vec_rows),
            # 预算是**字数**（批 11）→ 判据看"被索引了多少字"，不是"多少块"
            "vector_budget_ok": int(vec_chars) <= WEB_VECTOR_CHARS,
            "truncated": packet.truncated,
            "warnings": list(packet.warnings),
            "anchor_head": packet.chunks[0].anchor if packet.chunks else "",
            "heading_chunks": sum(1 for c in packet.chunks if c.heading_path),
            "page_only_word": page_only,
            "fragment_only_word": fragment_only,
        }
        if page_only:
            page_only_total += 1
            fts_hits = fts.search(project_id=PROJECT, query=page_only, limit=5, owner=OWNER)
            hybrid_hits = hybrid.search(
                project_id=PROJECT, query=page_only, limit=5, owner=OWNER
            )
            entry["page_only_fts_hit"] = any(
                h.material_id == packet_id.material_id for h in fts_hits
            )
            entry["page_only_hybrid_hit"] = any(
                h.material_id == packet_id.material_id for h in hybrid_hits
            )
            if entry["page_only_hybrid_hit"]:
                page_only_hits += 1
        if fragment_only:
            fragment_only_total += 1
            fts_hits = fts.search(
                project_id=PROJECT, query=fragment_only, limit=5, owner=OWNER
            )
            hybrid_hits = hybrid.search(
                project_id=PROJECT, query=fragment_only, limit=5, owner=OWNER
            )
            fts_found = any(h.material_id == packet_id.material_id for h in fts_hits)
            hybrid_found = any(h.material_id == packet_id.material_id for h in hybrid_hits)
            entry["fragment_only_fts_hit"] = fts_found
            entry["fragment_only_hybrid_hit"] = hybrid_found
            # 命中了就说清是哪条路：词面（FTS 索引）或语义（向量余弦）。
            # 语义路命中**不是缺陷**——那正是"换个说法也能搜到"的代价：无意义词也有余弦。
            entry["fragment_only_cause"] = (
                "fts"
                if fts_found
                else ("semantic" if hybrid_found else "none")
            )
            entry["fragment_only_in_stored_text"] = (
                fragment_only.casefold() in stored_text.casefold()
            )
            if hybrid_found:
                fragment_only_found += 1
        budget_ok = budget_ok and bool(entry["vector_budget_ok"])
        report["materials"].append(entry)

    report["summary"] = {
        "materials": len(stored),
        "page_only_words": page_only_total,
        "page_only_found": page_only_hits,
        "fragment_only_words": fragment_only_total,
        "fragment_only_found": fragment_only_found,
        "vector_budget_ok": budget_ok,
    }
    (OUT / "web_search_fullpage.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    connection.close()

    print("\n=== 逐份材料 ===")
    for item in report["materials"]:
        print(
            f"  {str(item['title'])[:26]:28} 存 {item['stored_chars']:>6} 字 / 片段 "
            f"{item['fragment_chars']:>5} 字 · {item['chunks']:>3} 块 · 向量 "
            f"{item['vectors']:>3} · 锚点 {item['anchor_head']!r}"
        )
        if item["page_only_word"]:
            print(
                f"      只在整页的词 {item['page_only_word']!r}："
                f"FTS {item['page_only_fts_hit']} · 混合 {item['page_only_hybrid_hit']}"
            )
        if item["fragment_only_word"]:
            print(
                f"      只在片段的词 {item['fragment_only_word']!r}："
                f"词面 {item['fragment_only_fts_hit']} · 混合 {item['fragment_only_hybrid_hit']}"
                f"（成因 {item['fragment_only_cause']}）· 库里含此词 "
                f"{item['fragment_only_in_stored_text']}"
            )
    print("\n=== 汇总 ===")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=1))
    print(f"\n证据：{OUT / 'web_search_fullpage.json'}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
