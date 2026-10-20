#!/usr/bin/env python3
"""批 6c · 决定性实验：主内容抽取对**检索**到底有多少可测收益？

## 为什么要有这个实验

仓库的先例（`extract.py:71-77`）：`readability-lxml` 三个真实页面增益只 **1–6%**，
而它会静默丢内容 → 结论是"**该撤的是工具，不是继续加判据**"。

trafilatura 该受**同一把尺子**：它的可测收益是多少？收益是个位数百分比 → 不做。

## 量什么（机制来自业界说法）

> "Boilerplate is embedded once per page. A 300 page site embeds its own footer 300 times,
> and those chunks match queries they should never match. [...] Those chunks are textually
> similar to every other page on the same site, so they surface for queries that have nothing
> to do with them, and they **push a genuinely relevant passage out of your top-k**."

于是**两类查询**：

| 查询类 | 从哪取词 | 期望 |
|---|---|---|
| **正文查询** | 抽取结果里的词（= 真正文） | 两套索引都该命中该页 |
| **样板查询** | **被抽取删掉**的词（= 只在样板里） | 现状会**假命中**（那词在全文里）；抽取后不该命中 |

**样板查询的假命中率 = 稀释的规模 = 抽取的收益。**

## 跑法

```bash
cd backend && HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \\
  set -a && . ./.env && set +a && \\
  uv run --with trafilatura python scripts/web_retrieval_bench.py
```
"""

from __future__ import annotations

import hashlib
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(BACKEND))

PAGES_DIR = BACKEND.parent / ".codex" / "runs" / "2026-10-19" / "pages"
PROJECT = "p-web-bench"
OWNER = "u-web"


def _log(text: str) -> None:
    print(text, flush=True)


def _now() -> datetime:
    return datetime.now(UTC)


def _tokens(text: str) -> list[str]:
    """中文词（jieba），只留 ≥2 字的词。

    **先逐行去 markdown 前缀再分词**——否则现状的 `### 标题` 与抽取的 `标题`
    会被当成两个不同的词，量出"抽取删了正文"的假象（前两版都栽在这上面）。
    """

    import jieba

    words = []
    for raw in text.splitlines():
        line = re.sub(r"^\s*#{1,6}\s*", "", raw)
        line = re.sub(r"^\s*[-*+]\s+", "", line)
        line = re.sub(r"^\s*\d+\.\s+", "", line)
        line = re.sub(r"^\s*>\s*", "", line)
        for word in jieba.cut(line):
            word = word.strip()
            if len(word) >= 2 and re.match(r"^[\w一-鿿]+$", word):
                words.append(word)
    return words


def _split(text: str) -> list[str]:
    """按行累积切块（与生产 `chunk_text` 同一把尺子；这里不认标题，只量检索）。"""

    from pmstudio.infrastructure.tools.limits import CHUNK_CHARS

    chunks: list[str] = []
    buffer: list[str] = []
    width = 0
    for line in text.splitlines():
        if buffer and width + len(line) > CHUNK_CHARS:
            chunks.append("\n".join(buffer))
            buffer, width = [], 0
        buffer.append(line)
        width += len(line) + 1
    if buffer:
        chunks.append("\n".join(buffer))
    return [chunk for chunk in chunks if chunk.strip()]


def _build_corpus() -> list[dict]:
    """每个页面切两套块：现状口径 / 主内容抽取。"""

    from pmstudio.infrastructure.tools.extract import html_text

    import trafilatura

    pages = []
    for html_path in sorted(PAGES_DIR.glob("*.html")):
        html = html_path.read_bytes()
        current, _ = html_text(html)
        extracted = trafilatura.extract(
            html, output_format="markdown", fast=True,
            include_comments=False, include_tables=True,
        ) or ""
        pages.append({"name": html_path.stem, "current": current, "extracted": extracted})
    return pages


def _index(connection, pages, key: str, vectors) -> dict[str, str]:
    """把一套块灌进真库（真材料仓 + 真 FTS + 真向量），返回 材料名 → material_id。"""

    from pmstudio.application.dto import MaterialChunk, ParsedMaterial
    from pmstudio.infrastructure.sqlite.materials import SqliteMaterialStore
    from tests.support.doubles import SeqIds

    store = SqliteMaterialStore(connection, ids=SeqIds())
    by_name: dict[str, str] = {}
    for page in pages:
        text = page[key]
        if not text.strip():
            continue
        parsed = ParsedMaterial(
            origin="upload",
            title=page["name"],
            source_ref=f"page:{page['name']}",
            media_type="text/html",
            bytes_len=len(text.encode("utf-8")),
            chunks=tuple(
                MaterialChunk(
                    chunk_id=f"local#c{index:02d}", ordinal=index,
                    text=chunk, heading_path=(), anchor=f"第 {index + 1} 段",
                )
                for index, chunk in enumerate(_split(text))
            ),
            parser_id="local-text",
            parser_version="v1",
        )
        packet = store.put(
            project_id=PROJECT, parsed=parsed,
            sha256=hashlib.sha256(f"{page['name']}-{key}".encode()).hexdigest(),
            content=text.encode("utf-8"), now=_now(),
        )
        vectors.write(packet.material_id)
        by_name[page["name"]] = packet.material_id
    return by_name


def _queries(pages) -> tuple[list, list]:
    """两类查询，**查询词只从「两边都有」的词里取**（避免循环论证）。

    上一版把正文查询的词从**抽取结果**里取 → 抽取口径天然占便宜（那不是收益，
    是循环论证）。这一版：

    - **正文查询**：词在「现状 ∩ 抽取」里 → 两边都有，公平。量：目标页在 top5 吗？
    - **样板查询**：词**只在现状里**（被抽取删掉的）→ 这些词是"每页都有的噪音"。
      量：一条查询命中**多少个块**（越多 = 稀释越严重）。
    """

    body_queries: list[tuple[str, str]] = []
    noise_queries: list[tuple[str, str]] = []
    for page in pages:
        current_words = set(_tokens(page["current"]))
        extracted_words = set(_tokens(page["extracted"]))
        shared = current_words & extracted_words
        for word in sorted(shared)[:3]:
            body_queries.append((word, page["name"]))
        for word in sorted(current_words - extracted_words)[:3]:
            noise_queries.append((word, page["name"]))
    return body_queries, noise_queries


def _hit_count(retriever, queries) -> tuple[int, int]:
    """样板查询平均命中多少个块 —— **稀释的直接度量**。"""

    total = 0
    for query, _ in queries:
        results = retriever.search(
            project_id=PROJECT, query=query, limit=5, owner=OWNER
        )
        total += len(results)
    return total, len(queries)


def _hit_rate(retriever, queries, by_name) -> tuple[int, int, list[int]]:
    """查询里有多少条的目标材料出现在 top5；以及**它排第几**（1=最好）。

    「排第几」才是用户看见的东西：命中块数少不等于对的内容更靠前。
    """

    hits = 0
    ranks: list[int] = []
    for query, name in queries:
        target = by_name.get(name)
        if target is None:
            continue
        results = retriever.search(
            project_id=PROJECT, query=query, limit=5, owner=OWNER
        )
        ids = [hit.material_id for hit in results]
        if target in ids:
            hits += 1
            ranks.append(ids.index(target) + 1)
    return hits, len(queries), ranks


def main() -> int:
    from pmstudio.domain.shared.ids import UserId
    from pmstudio.infrastructure.sqlite import database
    from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
    from pmstudio.infrastructure.sqlite.materials import (
        SqliteFtsRetriever,
        SqliteHybridRetriever,
    )
    from pmstudio.infrastructure.sqlite.owner_scope import set_owner_scope
    from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex

    set_owner_scope(UserId(OWNER))
    pages = _build_corpus()
    _log(f"语料：{len(pages)} 个真实页面")
    body_queries, noise_queries = _queries(pages)
    _log(f"查询：正文 {len(body_queries)} 条、样板 {len(noise_queries)} 条")

    embedder = FastEmbedEmbedder()
    report: dict[str, tuple[int, int, int, int]] = {}
    for key, label in (("current", "现状(bs4剥噪音+拍平)"), ("extracted", "主内容抽取")):
        with tempfile.TemporaryDirectory() as tmp:
            connection = database.connect(Path(tmp) / "b.db")
            connection.execute(
                "INSERT OR IGNORE INTO users (user_id, password_hash, created_at)"
                f" VALUES ('{OWNER}', 'x', '2026-10-19T00:00:00+00:00')")
            vectors = SqliteVectorIndex(connection, embedder=embedder)
            by_name = _index(connection, pages, key, vectors)
            for kind, retriever in (
                ("FTS5(词面)", SqliteFtsRetriever(connection)),
                ("混合(生产)", SqliteHybridRetriever(connection, embedder=embedder)),
            ):
                body_hits, body_total, ranks = _hit_rate(retriever, body_queries, by_name)
                noise_blocks, noise_total = _hit_count(retriever, noise_queries)
                mean_rank = sum(ranks) / len(ranks) if ranks else 0
                top1 = sum(1 for r in ranks if r == 1)
                report[f"{label}·{kind}"] = (body_hits, body_total,
                                            noise_blocks, noise_total, ranks)
                _log(f"  {label:22}·{kind:10} 正文命中 {body_hits}/{body_total}"
                     f" · 名次均 {mean_rank:.2f} / 第1名 {top1}"
                     f" · 样板块 {noise_blocks/max(1,noise_total):.1f}")
            connection.close()

    _log("\n=== 结论表（FTS5 词面路）===")
    _log(f"{'口径':24} {'正文查询命中':>14} {'样板查询平均命中块':>18}")
    for label, (bh, bt, nh, nt, ranks) in report.items():
        mean_rank = sum(ranks) / len(ranks) if ranks else 0
        _log(f"{label:24} {bh:>6}/{bt:<7} 名次均 {mean_rank:.2f}  样板块 {nh/max(1,nt):.1f}")
    _log("\n读法：**样板假命中越低越好**（那是噪音在抢 top-k）；"
         "**正文命中不能掉**（抽取不能丢我们要的内容）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
