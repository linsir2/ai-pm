#!/usr/bin/env python3
"""诊断：改述查询下，目标块到底**卡在哪一步**（KNN 召回 / 分档 / 排序）。

真数据跑出来的现象（2026-10-19）：联网材料的改述类查询，**混合检索 r@5 = 0**，
比纯 FTS5（也是 0，但那是"本来就该 0"）还让人意外——语义路明明该救回来。

本脚本把链路拆成三步各自量：
1. **KNN 召回**：目标块在语义列表里排第几？（不在列表 = 向量没起作用）
2. **分档**：目标块被分到第几档？前面那一档有多少块？（档位判错 = 被挤掉）
3. **最终排序**：目标块在最终 top5 里吗？

**跑法**：`cd backend && HF_ENDPOINT=... python3 scripts/vector_web_diag.py`
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(BACKEND))

from scripts.vector_web_bench import (  # noqa: E402
    _fetch,
    _literal_segment,
    _make_paraphrases,
    _load_env,
)


def _log(text: str) -> None:
    print(text, flush=True)


def main() -> int:
    _load_env()
    from pmstudio.domain.shared.ids import UserId
    from pmstudio.infrastructure.sqlite import database
    from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
    from pmstudio.infrastructure.sqlite.materials import (
        SqliteFtsRetriever,
        SqliteHybridRetriever,
        SqliteMaterialStore,
    )
    from pmstudio.infrastructure.sqlite.owner_scope import set_owner_scope
    from pmstudio.infrastructure.sqlite.tokenize import query_tokens
    from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
    from pmstudio.infrastructure.tools.tavily import _packet
    from pmstudio.infrastructure.tools.webclean import normalize_web_text
    from pmstudio.infrastructure.tools.webdedup import dedupe_by_content
    from tests.support.doubles import SeqIds

    project, owner = "p-diag", "u-diag"
    set_owner_scope(UserId(owner))

    raw = asyncio.run(_fetch("basic"))
    _log(f"真结果 {len(raw)} 条")
    packets = [_packet(item, query=item["query"]) for item in raw]
    unique = dedupe_by_content(packets)

    with tempfile.TemporaryDirectory() as tmp:
        connection = database.connect(Path(tmp) / "d.db")
        connection.execute(
            "INSERT OR IGNORE INTO users (user_id, password_hash, created_at)"
            " VALUES (?, 'x', ?)", (owner, datetime.now(UTC).isoformat()))
        store = SqliteMaterialStore(connection, ids=SeqIds())
        embedder = FastEmbedEmbedder()
        vectors = SqliteVectorIndex(connection, embedder=embedder)
        stamped = []
        import hashlib
        for packet in unique:
            item = store.put(
                project_id=project, parsed=packet,
                sha256=hashlib.sha256(packet.source_ref.encode()).hexdigest(),
                content=b"x", now=datetime.now(UTC))
            vectors.write(item.material_id)
            stamped.append(item)
        _log(f"入库 {len(stamped)} 份材料，"
             f"{connection.execute('SELECT count(*) FROM material_chunks_vec').fetchone()[0]} 个向量")

        # 挑几段做改述
        samples = []
        for item in stamped[:8]:
            text = item.parsed.chunks[0].text
            if _literal_segment(text):
                samples.append((item, text))
        paraphrases = _make_paraphrases([t for _, t in samples])

        def _bigrams(text: str) -> set[str]:
            cleaned = re.sub(r"\s+", "", text)
            return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}

        hybrid = SqliteHybridRetriever(connection, embedder=embedder)
        fts = SqliteFtsRetriever(connection)

        for text, (item, source) in zip(paraphrases, samples, strict=False):
            if len(_bigrams(text) & _bigrams(source)) > 1:
                _log(f"  [跳过] 改述与原文词面重合太多：{text[:24]}")
                continue
            tokens = query_tokens(text)
            # ① KNN 召回
            vec_ids = hybrid._knn_ids(project, owner, text)
            chunk_ids = [c.chunk_id for c in item.parsed.chunks]
            rank = next(
                (i for i, cid in enumerate(vec_ids) if cid in chunk_ids), None
            )
            # ② 分档
            fts_ids = [h.chunk_id for h in fts.search(
                project_id=project, query=text, limit=20, owner=owner)]
            coverage = hybrid._coverage(
                list(dict.fromkeys(fts_ids + vec_ids)), text)
            target_tier = min(
                (0 if coverage.get(cid, 0.0) >= 1.0
                 else 1 if coverage.get(cid, 0.0) >= 0.5 else 2)
                for cid in chunk_ids)
            ahead = sum(
                1 for cid in set(fts_ids) | set(vec_ids)
                if (0 if coverage.get(cid, 0.0) >= 1.0
                    else 1 if coverage.get(cid, 0.0) >= 0.5 else 2) < target_tier
            )
            # ③ 最终
            hits = hybrid.search(project_id=project, query=text, limit=5, owner=owner)
            final = [h.chunk_id for h in hits]
            found = any(cid in final for cid in chunk_ids)
            _log(f"  改述「{text}」")
            _log(f"    原文「{source[:60]}」")
            _log(f"    目标块 {chunk_ids[:1]}… KNN 排名 {rank}（列表 {len(vec_ids)} 个）"
                 f" → 档位 {target_tier}，前面堵着 {ahead} 个块")
            _log(f"    最终 top5 里有目标：{found}"
                 f"；FTS 路返回 {len(fts_ids)} 个块")
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
