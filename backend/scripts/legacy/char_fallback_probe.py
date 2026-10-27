"""字级兜底的可行性探针：**先量后做**（设计文档 §4.4 判据 7）。

问题：词面路（FTS5 + jieba）零命中时，还有没有东西可捞？

为什么这是"兜底"而不是"并行索引"：jieba 的 `cut_for_search` **不吐单字**
（实测"召回率提升" → 召回/率/提升），所以**单字不在索引里**。缺口是：
跨词子串（"数库"）、英文子串（"Embed" vs "Embedding"）、单字（"元"）、
近拼写（错别字）。

量两种兜底：
  ① **子串**：`text LIKE '%查询词%'`（连续匹配）
  ② **单字 AND**：查询词每个字都要出现（能救错别字，但会过匹配）

判据：至少一种能**多捞回 ≥3 条**才值得写进生产代码；否则砍掉这个功能。

跑法（需要 backend/.env 里的 Tavily key 与 HF 镜像）：
    cd backend && HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \\
        uv run python -u scripts/char_fallback_probe.py
"""

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")

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

#: 手写的"字面零命中"查询（刻意避开索引里已有的词）。最后一组是对照组
#: （词面应该能命中，兜底不该比它差）。
QUERIES = [
    "元",           # 单字（语料里是「元脑」这个整词，索引里没有单字「元」）
    "Embed",        # 英文子串（语料里是「Embedding」）
    "切片粒度",     # 跨词子串（语料里是「切片」+「粒度」两个词，不连续）
    "vecto",        # 英文子串（语料里是「vector」）
    "数库",         # 跨词子串（「知识库」拆成「知识」+「库」）
    "Recall",       # 英文子串
    "准率",         # 跨词子串
    "vec",          # 英文子串
    "排重",         # 跨词子串
    "嵌入模型",     # 对照组：词面应该能命中
]


def _like_substring(connection, query, limit=5):
    """子串兜底：整串在正文里连续出现。"""
    rows = connection.execute(
        "SELECT c.material_id FROM material_chunks c JOIN materials m"
        " ON m.material_id = c.material_id"
        " WHERE m.project_id = ? AND m.owner_id = ? AND c.text LIKE ?"
        " LIMIT ?",
        (PROJECT, OWNER, f"%{query}%", limit),
    ).fetchall()
    return {str(row["material_id"]) for row in rows}


def _like_chars(connection, query, limit=5):
    """单字 AND 兜底：查询词每个字都要出现（能救错别字，但过匹配）。"""
    clause = " AND ".join("c.text LIKE ?" for _ in query)
    rows = connection.execute(
        "SELECT c.material_id FROM material_chunks c JOIN materials m"
        " ON m.material_id = c.material_id"
        " WHERE m.project_id = ? AND m.owner_id = ? AND " + clause + " LIMIT ?",
        (PROJECT, OWNER, *(f"%{char}%" for char in query), limit),
    ).fetchall()
    return {str(row["material_id"]) for row in rows}


def _like_tokens(connection, query, limit=5):
    """**词级子串 AND**：查询的每个 token 都要以子串形式出现。

    这是真正要实现的那条规则：
    - 单字查询（"元"）→ `LIKE '%元%'`
    - 英文子串（"vec" vs "vector"）→ token "vec" → `LIKE '%vec%'`
    - 跨词子串（"数库" vs "知识库"）→ token "数库" → `LIKE '%数库%'`
    - 错别字（"召囘率"）→ token 逐个 `LIKE` → 救得回来
    """
    from pmstudio.infrastructure.sqlite.tokenize import query_tokens

    tokens = query_tokens(query)
    if not tokens:
        return set()
    clause = " AND ".join("c.text LIKE ?" for _ in tokens)
    rows = connection.execute(
        "SELECT c.material_id FROM material_chunks c JOIN materials m"
        " ON m.material_id = c.material_id"
        " WHERE m.project_id = ? AND m.owner_id = ? AND " + clause + " LIMIT ?",
        (PROJECT, OWNER, *(f"%{token}%" for token in tokens), limit),
    ).fetchall()
    return {str(row["material_id"]) for row in rows}


def main():
    corpus_path = OUT / "C raw_content-净.json"
    if not corpus_path.exists():
        raise SystemExit(
            f"冻结语料不在 {corpus_path}；先跑 scripts/tavily_raw_bench.py 重建"
        )
    items = json.loads(corpus_path.read_text(encoding="utf-8"))
    embedder = FastEmbedEmbedder()
    db = OUT / "char_probe.db"
    if db.exists():
        db.unlink()
    connection = connect(db, embedder=embedder)
    store = SqliteMaterialStore(connection, ids=UuidIdGenerator())
    vector_index = SqliteVectorIndex(connection, embedder=embedder)
    url2mid = {}
    for item in items:
        chunks = chunk_text(
            normalize_web_text(item["text"]), parser_id="tavily", max_chars=400
        )
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
        vector_index.write(packet.material_id)
        url2mid[item["url"]] = packet.material_id
    connection.commit()

    fts = SqliteFtsRetriever(connection)
    hybrid = SqliteHybridRetriever(connection, embedder=embedder)
    print(f"语料 {len(items)} 份材料 · 查询 {len(QUERIES)} 条\n")
    print(f"{'查询':10} {'词面':>5} {'混合':>5} {'子串':>5} {'单字AND':>8} {'词级AND':>7}"
          f"  {'多捞 子/单/词':>8}")
    print("-" * 70)
    extra_substring = 0
    extra_chars = 0
    extra_tokens = 0
    for query in QUERIES:
        fts_mids = {hit.material_id for hit in fts.search(
            project_id=PROJECT, query=query, limit=5, owner=OWNER)}
        hybrid_mids = {hit.material_id for hit in hybrid.search(
            project_id=PROJECT, query=query, limit=5, owner=OWNER)}
        sub_mids = _like_substring(connection, query)
        char_mids = _like_chars(connection, query)
        token_mids = _like_tokens(connection, query)
        baseline = fts_mids | hybrid_mids
        gained_sub = len(sub_mids - baseline)
        gained_chars = len(char_mids - baseline)
        gained_tokens = len(token_mids - baseline)
        extra_substring += gained_sub
        extra_chars += gained_chars
        extra_tokens += gained_tokens
        print(f"{query:10} {len(fts_mids):>5} {len(hybrid_mids):>5} {len(sub_mids):>5}"
              f" {len(char_mids):>8} {len(token_mids):>7}  {gained_sub:>3}/{gained_chars:<3}"
              f"/{gained_tokens:<3}")
    print("-" * 70)
    print(f"兜底比现有检索多捞回：子串 {extra_substring} 条 · 单字AND {extra_chars} 条"
          f" · 词级AND {extra_tokens} 条")
    best = max(extra_substring, extra_chars, extra_tokens)
    print(f"判据（≥3 条才实现）：{'通过 → 实现' if best >= 3 else '不通过 → 砍掉'}")
    connection.close()


if __name__ == "__main__":
    main()
