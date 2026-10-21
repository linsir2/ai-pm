"""向量**覆盖**扫描：块宽 × 每份材料索引多少**字**。

## 为什么要有这个脚本（2026-10-21，批 11）

批 10 把块宽从 400 降到 200，但**向量预算还按「块数」定**（`WEB_VECTOR_BLOCKS = 24`）——
而「块数」是实现细节：块宽一改，**语义覆盖的字数**就跟着腰斩。

实测（C 口径语料 15 份真实网页整页，162238 字）：

| 块宽 | 预算「前 24 块」→ 覆盖 |
|---|---|
| 400 | 41% |
| 200 | **27%** ← 批 10 之后 |

而网页的**尾部不是低密度内容**（实测 55–85% 区间全是实质段落）→ 覆盖不足 = 检索漏。

所以本脚本把预算的量纲从**块数**换成**字数**，量出「覆盖 ↔ 召回 ↔ 成本」这条曲线，
让「每份联网材料索引多少字」这个数**由数据定**。

## 判据

- **材料级命中**：`frozen_queries.json`（3 条查询 × 15 份材料 = 45 条）——
  该材料含该关键词时，命中它的 top5 算命中。
- **改述命中**：`paraphrases.json`（6 份材料 × 3 条手写改述）——刻意换词，测语义召回。
- 两套都报 **FTS5**（纯词面基线）与**混合**（词面 + 向量）——**差值才是向量的贡献**。

## 复跑

    cd backend && uv run python -u scripts/vector_coverage_bench.py

语料与查询集在 `../.codex/runs/2026-10-19/tavily_raw/`（gitignored，需真 Tavily 跑出来的原始语料）。
"""

import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")

import jieba

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
from pmstudio.infrastructure.sqlite.materials import (
    SqliteFtsRetriever,
    SqliteHybridRetriever,
    SqliteMaterialStore,
)
from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
from pmstudio.infrastructure.tools.extract import chunk_text
from pmstudio.infrastructure.tools.webclean import normalize_web_text

OUT = Path("../.codex/runs/2026-10-19/tavily_raw")
PROJECT, OWNER = "p1", "o1"
#: 实测每块向量推理 49ms（`scripts/vector_budget_bench.py` 的量法）
EMBED_SECONDS_PER_BLOCK = 0.049

#: (块宽, 每份索引的字数上限)。`None` = 全量。
#: 4800 ≈ 现状（块宽 200 + 前 24 块 ≈ 2900 字）与旧口径（400 宽 × 24 块 ≈ 4800 字）。
GRID = (
    (200, 4800),
    (200, 9600),
    (200, 16000),
    (200, None),
    (400, 4800),
    (400, 9600),
    (400, 16000),
    (400, None),
)


def tokens(text: str) -> list[str]:
    words: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"^\s*#{1,6}\s*", "", raw)
        line = re.sub(r"^\s*[-*+]\s+", "", line)
        line = re.sub(r"^\s*>\s*", "", line)
        for word in jieba.cut(line):
            word = word.strip()
            if len(word) >= 2 and re.match(r"^[\w一-鿿]+$", word):
                words.append(word)
    return words


def blocks_within(chunks, char_cap: int | None) -> int:
    """前多少个块的累计字数不超 `char_cap`（`None` = 全部）。"""
    if char_cap is None:
        return len(chunks)
    used = 0
    count = 0
    for chunk in chunks:
        if used + len(chunk.text) > char_cap:
            break
        used += len(chunk.text)
        count += 1
    return count


def build(items, embedder, width: int, char_cap: int | None):
    """建库 + 按字数预算写向量。返回 (连接, url→material_id, 覆盖统计)。"""
    db = OUT / f"coverage_w{width}_c{char_cap or 'all'}.db"
    if db.exists():
        db.unlink()
    conn = connect(db, embedder=embedder)
    store = SqliteMaterialStore(conn, ids=UuidIdGenerator())
    vector_index = SqliteVectorIndex(conn, embedder=embedder)
    url2mid: dict[str, str] = {}
    total_chars = 0
    covered_chars = 0
    vector_blocks = 0
    for item in items:
        clean = normalize_web_text(item["text"])
        chunks = chunk_text(clean, parser_id="tavily", max_chars=width)
        total_chars += sum(len(chunk.text) for chunk in chunks)
        keep = blocks_within(chunks, char_cap)
        covered_chars += sum(len(chunk.text) for chunk in chunks[:keep])
        parsed = ParsedMaterial(
            origin="web_search",
            title=item["title"],
            source_ref=item["url"],
            media_type="text/html",
            bytes_len=len(item["text"].encode()),
            chunks=tuple(
                MaterialChunk(
                    chunk_id=f"c{index:02d}",
                    ordinal=index,
                    text=chunk.text,
                    heading_path=chunk.heading_path,
                    anchor=chunk.anchor,
                )
                for index, chunk in enumerate(chunks)
            ),
            parser_id="tavily",
            parser_version="bench",
        )
        packet = store.put(
            project_id=PROJECT,
            parsed=parsed,
            sha256=hashlib.sha256(item["text"].encode()).hexdigest(),
            content=item["text"].encode(),
            now=datetime(2026, 10, 19, tzinfo=UTC),
            owner=OWNER,
        )
        vector_index.write(packet.material_id, max_blocks=keep)
        vector_blocks += keep
        url2mid[item["url"]] = packet.material_id
    conn.commit()
    stats = {
        "total_chars": total_chars,
        "covered_chars": covered_chars,
        "vector_blocks": vector_blocks,
    }
    return conn, url2mid, stats


def judge(retriever, cases, url2mid, have, *, require_keyword: bool = True):
    """返回 (命中数, 有效数, 名次均, 第1名数)。

    `require_keyword=False` 用于**改述**：改述刻意换掉了原文的词，
    拿"关键词必须在原文里"去过滤会把改述用例全滤掉（那是判据写错，不是数据问题）。
    """
    hits = 0
    ranks: list[int] = []
    for keyword, query, url in cases:
        mid = url2mid.get(url)
        if mid is None:
            continue
        if require_keyword and keyword not in have.get(mid, ()):
            continue
        results = retriever.search(project_id=PROJECT, query=query, limit=5, owner=OWNER)
        ids = [hit.material_id for hit in results]
        if mid in ids:
            hits += 1
            ranks.append(ids.index(mid) + 1)
    average = sum(ranks) / len(ranks) if ranks else 0.0
    return hits, len(ranks), average, sum(1 for rank in ranks if rank == 1)


def main() -> None:
    items = json.loads((OUT / "C raw_content-净.json").read_text(encoding="utf-8"))
    frozen = json.loads((OUT / "frozen_queries.json").read_text(encoding="utf-8"))
    keyword_cases = [tuple(item) for item in frozen["items"]]
    paraphrases = json.loads((OUT / "paraphrases.json").read_text(encoding="utf-8"))["paraphrases"]

    embedder = FastEmbedEmbedder()
    print(f"语料 {len(items)} 份材料 · 关键词查询 {len(keyword_cases)} 条 · 改述 "
          f"{sum(len(v) for v in paraphrases.values())} 条\n")
    print(
        "块宽  字数预算  块/份  覆盖    向量块  嵌入  │ FTS5 命中/名次  │"
        " 混合 命中/名次 │ 改述命中(混合)"
    )
    for width, char_cap in GRID:
        conn, url2mid, stats = build(items, embedder, width, char_cap)
        have = {
            mid: set(tokens(item["text"]))
            for item, mid in zip(items, url2mid.values(), strict=True)
        }
        # 改述判据：索引 = 语料条号（0 起）
        para_cases = [
            (phrase, phrase, items[int(index)]["url"])
            for index, phrases in paraphrases.items()
            for phrase in phrases
        ]
        # 改述判据不按关键词过滤（见 `judge` 的说明）
        fts = SqliteFtsRetriever(conn)
        hybrid = SqliteHybridRetriever(conn, embedder=embedder)
        fts_hit, fts_n, fts_mr, fts_top1 = judge(fts, keyword_cases, url2mid, have)
        hyb_hit, hyb_n, hyb_mr, hyb_top1 = judge(hybrid, keyword_cases, url2mid, have)
        para_hit, para_n, para_mr, para_top1 = judge(
            hybrid, para_cases, url2mid, have, require_keyword=False
        )
        per_material = stats["vector_blocks"] / len(items)
        coverage = stats["covered_chars"] / stats["total_chars"]
        cap_label = f"{char_cap}" if char_cap else "全量"
        print(
            f"{width:>4}  {cap_label:>7}  {per_material:>5.0f}  {coverage:>5.0%}"
            f"  {stats['vector_blocks']:>6}"
            f"  {stats['vector_blocks'] * EMBED_SECONDS_PER_BLOCK:>4.1f}s"
            f"  │ {fts_hit:>2}/{fts_n:<2} {fts_mr:.2f}       │"
            f" {hyb_hit:>2}/{hyb_n:<2} {hyb_mr:.2f}   │ {para_hit:>2}/{para_n:<2} {para_mr:.2f}"
        )
        conn.close()


if __name__ == "__main__":
    main()
