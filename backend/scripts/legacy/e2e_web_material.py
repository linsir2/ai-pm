"""真机端到端：联网材料**整页入库 → 搜得到整页里才有的词**。

这不是测试替身：真 Tavily（真 key）、真嵌入模型（真 ONNX）、真 SQLite + FTS5 + sqlite-vec、
真 `TavilySearchGateway` → 真 `SqliteMaterialStore` → 真 `SqliteVectorIndex` →
真 `SqliteHybridRetriever`。走的正是生产那条路（`SubmitOpening._keep` 用的是同两个件）。

**要验的那件事**：Tavily 的 `content`（按 query 挑的片段）里**没有**的词，
在整页 `raw_content` 里 → 入库后**必须能搜到**。这正是批 6c 的全部意义。

跑法（需要 backend/.env 里的 Tavily key）：
    cd backend && HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \\
        uv run python -u scripts/e2e_web_material.py
"""

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")

from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
from pmstudio.infrastructure.sqlite.materials import (
    SqliteFtsRetriever,
    SqliteHybridRetriever,
    SqliteMaterialStore,
)
from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
from pmstudio.infrastructure.tools.tavily import _packet

OUT = Path("../.codex/runs/2026-10-20/e2e")
PROJECT, OWNER = "p-e2e", "owner-e2e"
QUERY = "向量检索 企业知识库 召回效果"


def api_key() -> str:
    for line in Path(".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.strip().split("=", 1)
            if key == "PMSTUDIO_TAVILY_API_KEY":
                return value
    raise SystemExit("backend/.env 里没有 PMSTUDIO_TAVILY_API_KEY")


def pick_whole_page_only_word(raw: str, content: str) -> str | None:
    """从整页里挑一个**只存在于整页、不在片段里**的词（长度 ≥ 3）。"""
    import re

    from pmstudio.infrastructure.sqlite.tokenize import index_tokens

    # `index_tokens` 返回的是**空格连接的串**（FTS5 的索引源），要 `.split()` 才是 token 列表
    content_tokens = set(index_tokens(content).split())
    for line in raw.splitlines():
        line = re.sub(r"^\s*[#>\-*]+\s*", "", line.strip())
        # 去掉 markdown 链接的 **URL 部分**（否则会挑到 URL 里的词，如 "model"）
        line = re.sub(r"\([^)]*\)", " ", line)
        for word in index_tokens(line).split():
            # 要**真词**：含汉字或字母（纯数字/标点当探针太宽，如 "2.1" 到处都命中）
            if (
                len(word) >= 3
                and re.search(r"[\u4e00-\u9fffA-Za-z]", word)
                and word not in content_tokens
            ):
                return word
    return None


def fetch_items() -> list[dict]:  # type: ignore[no-untyped-def]
    """一次真 Tavily 调用，**同时**拿到两个字段：`content`（按 query 挑的片段）
    与 `raw_content`（整页 markdown）。用同一批结果做对照，才有意义。"""
    import httpx

    response = httpx.post(
        "https://api.tavily.com/search",
        json={
            "api_key": api_key(),
            "query": QUERY,
            "max_results": 5,
            "search_depth": "basic",
            "include_raw_content": "markdown",
        },
        timeout=120,
    )
    response.raise_for_status()
    return list(response.json().get("results", []))


async def main() -> None:  # type: ignore[no-untyped-def]
    OUT.mkdir(parents=True, exist_ok=True)
    items = fetch_items()
    print(f"真 Tavily 回了 {len(items)} 条结果")
    # 用**生产同款**的建包逻辑（`TavilySearchGateway._packet`——网关的公开
    # `search()` 走的就是它，重试/退避那层由单元测试守着）
    packets = [_packet(item, query=QUERY) for item in items]
    fragments = {item.get("url", ""): str(item.get("content") or "") for item in items}

    db = OUT / "e2e.db"
    if db.exists():
        db.unlink()
    embedder = FastEmbedEmbedder()
    connection = connect(db, embedder=embedder)
    store = SqliteMaterialStore(connection, ids=UuidIdGenerator())
    vector_index = SqliteVectorIndex(connection, embedder=embedder)

    stored = []
    for packet in packets:
        packet_id = store.put(
            project_id=PROJECT,
            parsed=packet,
            sha256=hashlib.sha256(packet.source_ref.encode()).hexdigest(),
            content="\n".join(chunk.text for chunk in packet.chunks).encode("utf-8"),
            now=datetime(2026, 10, 20, tzinfo=UTC),
            owner=OWNER,
        )
        vector_index.write(packet_id.material_id)
        stored.append((packet, packet_id))
    connection.commit()

    total_chunks = connection.execute("SELECT count(*) FROM material_chunks").fetchone()[0]
    total_vectors = connection.execute("SELECT count(*) FROM material_chunks_vec").fetchone()[0]
    print(f"入库：{len(stored)} 份材料 · {total_chunks} 块 · {total_vectors} 条向量")

    fts = SqliteFtsRetriever(connection)
    hybrid = SqliteHybridRetriever(connection, embedder=embedder)

    # 找一个「只在整页里、片段里没有」的词
    target = None
    for packet, packet_id in stored:
        # 比较基准必须是**整页**（所有块拼起来），不是第一块——
        # 否则第一块的词当然都在片段里（片段就是从页面上切的）。
        page_text = "\n".join(chunk.text for chunk in packet.chunks)
        word = pick_whole_page_only_word(
            page_text, fragments.get(packet.source_ref, "")
        )
        if word:
            target = (word, packet_id)
            break
    if target is None:
        print("这一批结果里没找到「只在整页里」的词（样本不足），跳过该项")
        return
    word, packet_id = target
    print(f"\n拿一个整页里才有的词来验：{word!r}")

    fts_hits = fts.search(project_id=PROJECT, query=word, limit=5, owner=OWNER)
    hybrid_hits = hybrid.search(project_id=PROJECT, query=word, limit=5, owner=OWNER)
    fts_ok = any(hit.material_id == packet_id.material_id for hit in fts_hits)
    hybrid_ok = any(hit.material_id == packet_id.material_id for hit in hybrid_hits)
    print(f"  词面路命中：{fts_ok}（{len(fts_hits)} 条）")
    print(f"  混合路命中：{hybrid_ok}（{len(hybrid_hits)} 条）")
    for hit in hybrid_hits[:3]:
        print(f"    · {hit.title[:34]} · {hit.excerpt[:60]!r}")

    report = {
        "query": QUERY,
        "materials": len(stored),
        "chunks": total_chunks,
        "vectors": total_vectors,
        "probe_word": word,
        "fts_hit": fts_ok,
        "hybrid_hit": hybrid_ok,
    }
    (OUT / "e2e_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    connection.close()
    if not hybrid_ok:
        raise SystemExit("整页里才有的词搜不到——批 6c 的目标没达成")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
