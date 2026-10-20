"""向量预算对**改述召回**的影响（手写改述，词面路 0 命中，只有向量能救）。

这是「FTS 全量 + 向量只给前 N 块」这个口径的成败判据：
若 N 太小 → 改述查询召不回 → 向量路的价值被砍掉 → 整个方案是假优化。
"""
import sys, json, hashlib
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, "src")
from pmstudio.infrastructure.tools.extract import chunk_text
from pmstudio.infrastructure.tools.webclean import normalize_web_text
from pmstudio.infrastructure.sqlite.materials import (
    SqliteFtsRetriever, SqliteHybridRetriever, SqliteMaterialStore)
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.application.dto import ParsedMaterial, MaterialChunk

OUT = Path("../.codex/runs/2026-10-19/tavily_raw")
PROJECT, OWNER = "p1", "o1"

def main():
    items = json.loads((OUT / "C raw_content-净.json").read_text(encoding="utf-8"))
    para = json.loads((OUT / "paraphrases.json").read_text(encoding="utf-8"))["paraphrases"]
    queries = [(q, int(i)) for i, qs in para.items() for q in qs]
    embedder = FastEmbedEmbedder()
    print(f"改述查询 {len(queries)} 条（材料 {len(set(i for _,i in queries))} 份）\n")
    for cap in (0, 8, 12, 24, 48, 10**9):
        db = OUT / f"para_{cap}.db"
        if db.exists(): db.unlink()
        conn = connect(db, embedder=embedder)
        store = SqliteMaterialStore(conn, ids=UuidIdGenerator())
        vi = SqliteVectorIndex(conn, embedder=embedder)
        url2mid = {}; nblocks = 0; per_material = []
        for idx, item in enumerate(items):
            clean = normalize_web_text(item["text"])
            chunks = chunk_text(clean, parser_id="tavily", max_chars=400)
            nblocks += len(chunks); per_material.append(len(chunks))
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
                               now=datetime(2026, 10, 19, tzinfo=timezone.utc), owner=OWNER)
            if cap:
                vi.write(packet.material_id, max_blocks=cap)
            url2mid[idx] = packet.material_id
        conn.commit()
        res = {}
        for kind, ret in (("FTS5", SqliteFtsRetriever(conn)),
                          ("混合", SqliteHybridRetriever(conn, embedder=embedder))):
            hits = 0; ranks = []
            for q, idx in queries:
                res_ = ret.search(project_id=PROJECT, query=q, limit=5, owner=OWNER)
                ids = [h.material_id for h in res_]
                if url2mid[idx] in ids:
                    hits += 1; ranks.append(ids.index(url2mid[idx]) + 1)
            res[kind] = (hits, sum(ranks)/len(ranks) if ranks else 0,
                         sum(1 for r in ranks if r == 1))
        label = "全量" if cap > 10**8 else str(cap)
        print(f"  向量上限 {label:>4}/份（材料块数 {min(per_material)}–{max(per_material)}）")
        for kind in ("FTS5", "混合"):
            h, mr, t1 = res[kind]
            print(f"      {kind:6} 改述命中 {h:>2}/{len(queries)}  名次均 {mr:.2f}  第1名 {t1}")
        conn.close()

if __name__ == "__main__":
    main()
