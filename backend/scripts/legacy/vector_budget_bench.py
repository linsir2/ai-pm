"""向量预算扫描：C 口径（raw_content-净）下，「FTS 全量 + 向量只给前 N 块」的 N 取多少。

为什么要这个：raw_content 让入库字数涨 8.3 倍，向量推理 49ms/块 → 全量要 50s+，
不可接受。用户选的口径是「FTS 全量 + 向量只给前 N 块」，N 必须用数据定，不能拍。
"""
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")
import re

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
LINK_LINE = re.compile(r"^!?\[[^\]]*\]\([^)]*\)$")
PROJECT, OWNER = "p1", "o1"

def tokens(text):
    words = []
    for raw in text.splitlines():
        line = re.sub(r"^\s*#{1,6}\s*", "", raw)
        line = re.sub(r"^\s*[-*+]\s+", "", line)
        line = re.sub(r"^\s*>\s*", "", line)
        for w in jieba.cut(line):
            w = w.strip()
            if len(w) >= 2 and re.match(r"^[\w一-鿿]+$", w):
                words.append(w)
    return words

def main():
    items = json.loads((OUT / "C raw_content-净.json").read_text(encoding="utf-8"))
    frozen = json.loads((OUT / "frozen_queries.json").read_text(encoding="utf-8"))
    queries = [tuple(q) for q in frozen["items"]]
    embedder = FastEmbedEmbedder()
    print(f"语料 {len(items)} 份材料 · 查询 {len(queries)} 条\n")
    for cap in (0, 8, 12, 16, 24, 40, 10**9):
        db = OUT / f"budget_{cap}.db"
        if db.exists():
            db.unlink()
        conn = connect(db, embedder=embedder)
        store = SqliteMaterialStore(conn, ids=UuidIdGenerator())
        vi = SqliteVectorIndex(conn, embedder=embedder)
        url2mid = {}
        nblocks = 0
        for item in items:
            clean = normalize_web_text(item["text"])
            chunks = chunk_text(clean, parser_id="tavily", max_chars=400)
            nblocks += len(chunks)
            parsed = ParsedMaterial(
                origin="web_search", title=item["title"], source_ref=item["url"],
                media_type="text/html", bytes_len=len(item["text"].encode()),
                chunks=tuple(MaterialChunk(chunk_id=f"c{j:02d}", ordinal=j, text=ch.text,
                                           heading_path=ch.heading_path, anchor=ch.anchor)
                             for j, ch in enumerate(chunks)),
                parser_id="tavily", parser_version="bench")
            packet = store.put(project_id=PROJECT, parsed=parsed,
                               sha256=hashlib.sha256(item["text"].encode()).hexdigest(),
                               content=item["text"].encode(),
                               now=datetime(2026, 10, 19, tzinfo=UTC), owner=OWNER)
            # **只给前 cap 块写向量**（FTS 仍是全量）
            if cap:
                vi.write(packet.material_id, max_blocks=cap)
            url2mid[item["url"]] = packet.material_id
        conn.commit()
        have = {
            mid: set(tokens(it["text"]))
            for it, mid in zip(items, url2mid.values(), strict=True)
        }
        out = []
        for kind, ret in (("FTS5", SqliteFtsRetriever(conn)),
                          ("混合", SqliteHybridRetriever(conn, embedder=embedder))):
            hits = 0
            ranks = []
            for w, _q, u in queries:
                mid = url2mid.get(u)
                if mid is None or w not in have.get(mid, ()):
                    continue
                res = ret.search(project_id=PROJECT, query=w, limit=5, owner=OWNER)
                ids = [h.material_id for h in res]
                if mid in ids:
                    hits += 1
                    ranks.append(ids.index(mid) + 1)
            out.append((kind, hits, len(ranks), sum(ranks)/len(ranks) if ranks else 0,
                        sum(1 for r in ranks if r == 1)))
        nv = cap if cap < 10**9 else nblocks
        per_round = len(items) / 3  # 3 个查询
        label = "全量" if cap > 10**8 else str(cap)
        print(f"  向量上限 {label:>4} 块/份 → 向量块 {nv*len(items):>5}"
              f" · 每轮 {nv*per_round:>5.0f} 块 · 约 {nv*per_round*0.049:>4.1f}s")
        for kind, hits, n, mr, t1 in out:
            print(f"      {kind:6} 命中 {hits:>2}/{n:<3} 名次均 {mr:.2f} 第1名 {t1:>2}")
        conn.close()

if __name__ == "__main__":
    main()
