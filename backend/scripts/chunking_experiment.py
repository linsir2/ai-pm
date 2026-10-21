"""**分块粒度实验**（批 9，2026-10-21）：单粒度 vs 父子分块，目标维度是**跨块证据**。

## 要回答的问题

批 8 的基线显示：材料级召回已满（1.000），但
- **跨块证据完整率只有 0.429**（答案散在文档两处时，两处拿不全）；
- **块级 r5 0.933**（材料对、块错 6.7%）。

「块宽」批 8 已扫过（200–600 非单调 → 维持 400），但那是在**旧指标**下扫的。
这次换三个新的东西：**父子分块**、**新指标**、**上下文层的代价**。

## 父子分块（不改 schema）

- **细块**（200 字）用于**检索**；**父块**（800 字 = 4 个细块）用于**回喂**（给模型看）；
- 父块**不需要新列**：细块的 `ordinal` 与 `heading_path` 已存在 → 父键 =
  `(material_id, heading_path, ordinal // 4)` → **可推导** ✓；
- 「展示单元」：单粒度 = 块；父子 = 父块（命中任一细块即该父块被覆盖）→ 两个口径**可比**。

## 指标

块级 r5 · MRR · **跨块证据完整率**（两处证据是否都在前 5 个展示单元里）·
**上下文层代价**（每轮喂进提示词多少字 / 多少份材料）· 块数与向量成本。

## 跑法

```bash
cd backend && HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \\
    uv run python -u scripts/chunking_experiment.py
"""
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")

import jieba

sys.path.insert(0, str(Path(__file__).parent))

from pmstudio.application.context import (
    MATERIAL_BUDGET,
    select_chunks,
    web_block_cost,
)
from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
from pmstudio.infrastructure.sqlite.materials import (
    SqliteHybridRetriever,
    SqliteMaterialStore,
)
from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
from pmstudio.infrastructure.tools.extract import chunk_text
from pmstudio.infrastructure.tools.webclean import normalize_web_text

OUT = Path("../.codex/runs/2026-10-19/tavily_raw")
PROJECT, OWNER = "p1", "o1"

#: 变体：(标签, 细块宽, 父块宽)。父块宽 = 0 表示单粒度。
#: (细块宽, 父块宽)。父块宽 0 = 单粒度。父块宽必须是细块宽的整数倍。
#: (切法, 细块上限, 父块上限)。父块 0 = 单粒度。
#: 切法 "行优先" = 现状；"标点优先" = 作者要的（在预算周围找句末标点，块贴近预算）。
VARIANTS = [
    ("生产", 200, 0), ("生产", 300, 0), ("生产", 400, 0),
    ("生产", 400, 1600), ("生产", 400, 1200), ("生产", 300, 1200),
]


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


def parent_key(material_id: str, ordinal: int, per_parent: int) -> str:
    """父块键：**可推导**，不新增数据库列。"""
    return f"{material_id}#P{ordinal // per_parent:03d}"


def build(items, embedder, child_chars, parent_chars, style="行优先"):
    path = OUT / f"chunk_{child_chars}_{parent_chars}.db"
    if path.exists():
        path.unlink()
    connection = connect(path, embedder=embedder)
    store = SqliteMaterialStore(connection, ids=UuidIdGenerator())
    vector_index = SqliteVectorIndex(connection, embedder=embedder)
    url2mid = {}
    mid2chunks = {}
    for item in items:
        clean = normalize_web_text(item["text"])
        # 2026-10-21（批 10 已实现）：**生产实现本身就是标点优先** →
        # bench 直接用它量真实行为，不再用实验变体。
        chunks = chunk_text(clean, parser_id="tavily", max_chars=child_chars)
        parsed = ParsedMaterial(
            origin="web_search", title=item["title"], source_ref=item["url"],
            media_type="text/html", bytes_len=len(item["text"].encode()),
            chunks=tuple(
                MaterialChunk(chunk_id=f"c{i:02d}", ordinal=i, text=c.text,
                              heading_path=c.heading_path, anchor=c.anchor)
                for i, c in enumerate(chunks)
            ),
            parser_id="tavily", parser_version="bench",
        )
        packet = store.put(
            project_id=PROJECT, parsed=parsed,
            sha256=hashlib.sha256(item["text"].encode()).hexdigest(),
            content=item["text"].encode(),
            now=datetime(2026, 10, 19, tzinfo=UTC), owner=OWNER,
        )
        vector_index.write(packet.material_id)
        url2mid[item["url"]] = packet.material_id
        mid2chunks[packet.material_id] = chunks
    connection.commit()
    return connection, url2mid, mid2chunks


def make_queries(items, url2mid, mid2chunks):
    frozen = json.loads((OUT / "frozen_queries.json").read_text(encoding="utf-8"))
    lexical = [(w, url) for w, _q, url in frozen["items"]][:20]
    para = json.loads((OUT / "paraphrases.json").read_text(encoding="utf-8"))["paraphrases"]
    url_by_index = {i: it["url"] for i, it in enumerate(items)}
    paraphrase = [
        (q, url_by_index[int(i)])
        for i, qs in sorted(para.items(), key=lambda kv: int(kv[0]))
        for q in qs
    ][:8]
    freq = {}
    for item in items:
        for word in set(tokens(item["text"])):
            freq[word] = freq.get(word, 0) + 1
    common = {w for w, c in freq.items() if c >= len(items) // 2}
    cross = []
    for item in items:
        mid = url2mid[item["url"]]
        chunks = mid2chunks[mid]
        if len(chunks) < 16:
            continue
        first = [w for w in tokens(chunks[6].text) if w not in common]
        second = [w for w in tokens(chunks[10].text) if w not in common]
        if not first or not second:
            continue
        first.sort(key=lambda w: freq.get(w, 0))
        second.sort(key=lambda w: freq.get(w, 0))
        cross.append((f"{first[0]} {second[0]}", item["url"],
                      (f"{mid}#c06", f"{mid}#c10")))
    return {"词面": lexical, "改述": paraphrase, "跨块": cross}


def main() -> None:
    items = json.loads((OUT / "C raw_content-净.json").read_text(encoding="utf-8"))
    embedder = FastEmbedEmbedder()
    print("=== 分块粒度实验（真 Tavily 语料 / 真模型）===")
    print("  指标：块级r5 · MRR · 跨块完整率 · 上下文层(块/份/字) · 块数")
    for style, child, parent in VARIANTS:
        label = (f"{style} 父子 {child}→{parent}" if parent
                 else f"{style} 单粒度 {child}")
        connection, url2mid, mid2chunks = build(items, embedder, child, parent, style)
        queries = make_queries(items, url2mid, mid2chunks)
        retriever = SqliteHybridRetriever(connection, embedder=embedder)
        per_parent = max(1, parent // child) if parent else 1

        # 块级 r5 / MRR
        block_hits = material_hits = 0
        rrs = []
        for query, url in queries["词面"]:
            mid = url2mid.get(url)
            hits = retriever.search(project_id=PROJECT, query=query, limit=5, owner=OWNER)
            ids = [h.material_id for h in hits]
            if mid in ids:
                material_hits += 1
                rrs.append(1.0 / (ids.index(mid) + 1))
            wanted = {f"{mid}#c{i:02d}" for i, c in enumerate(mid2chunks[mid]) if query in c.text}
            if wanted and any(h.chunk_id in wanted for h in hits):
                block_hits += 1
        # 改述召回（块宽 × 向量预算 = 语义覆盖的字数 → 改述是这块的探针）
        para_hits = 0
        for query, url in queries["改述"]:
            mid = url2mid.get(url)
            hits = retriever.search(project_id=PROJECT, query=query, limit=5, owner=OWNER)
            if any(h.material_id == mid for h in hits):
                para_hits += 1
        # 跨块：展示单元（父子时把细块映射到父块）
        complete = 0
        for query, url, wanted in queries["跨块"]:
            mid = url2mid.get(url)
            hits = retriever.search(project_id=PROJECT, query=query, limit=5, owner=OWNER)
            if parent:
                units = {
                    parent_key(h.material_id, int(h.chunk_id.split("#c")[1]), per_parent)
                    for h in hits
                }
                need = {
                    parent_key(mid, int(c.split("#c")[1]), per_parent) for c in wanted
                }
            else:
                units = {h.chunk_id for h in hits}
                need = set(wanted)
            if need <= units:
                complete += 1
        # 上下文层：**回喂父块**（父块 = 相邻细块拼起来，不需要新增存储）后的
        # 块数 / 份数 / 字数，以及**跨块两处证据是否同框**（进同一个父块）
        packets = packets_of(connection)
        picked = select_chunks(
            packets, budget=MATERIAL_BUDGET, cost=web_block_cost, query="向量检索 召回效果"
        )
        if parent:
            per_parent = max(1, parent // child)
            fed_texts = []
            fed_parents = set()
            for material_id, chunk in picked:
                siblings = mid2chunks[material_id]
                lo = (chunk.ordinal // per_parent) * per_parent
                hi = min(len(siblings), lo + per_parent)
                fed_texts.append("".join(s.text for s in siblings[lo:hi]))
                fed_parents.add((material_id, lo // per_parent))
        else:
            fed_texts = [chunk.text for _m, chunk in picked]
            fed_parents = {(m, chunk.ordinal) for m, chunk in picked}
        # 跨块两处证据在**上下文层**是否同框（同一个父块）
        together = 0
        for _query, url, wanted in queries["跨块"]:
            mid = url2mid.get(url)
            siblings = mid2chunks[mid]
            if parent:
                per_parent = max(1, parent // child)
                units = {
                    (material_id, (chunk.ordinal // per_parent))
                    for material_id, chunk in picked
                }
                need = {
                    (mid, int(c.split("#c")[1]) // per_parent) for c in wanted
                }
                if len(need) == 1 and need <= units:
                    together += 1
            else:
                units = {(material_id, chunk.ordinal) for material_id, chunk in picked}
                need = {(mid, int(c.split("#c")[1])) for c in wanted}
                if need <= units:
                    together += 1
        nblocks = connection.execute("SELECT count(*) FROM material_chunks").fetchone()[0]
        print(f"  {label:20} 块r5 {block_hits/max(1,len(queries['词面'])):.3f}"
              f" · 材料r5 {material_hits/max(1,len(queries['词面'])):.3f}"
              f" · MRR {sum(rrs)/max(1,len(rrs)):.3f}"
              f" · 改述r5 {para_hits/max(1,len(queries['改述'])):.3f}"
              f" · 跨块(检索) {complete/max(1,len(queries['跨块'])):.3f}"
              f" · 跨块(上下文同框) {together/max(1,len(queries['跨块'])):.3f}"
              f" · {nblocks:>4} 块"
              f" · 上下文 {len(fed_texts)} 块/{len(fed_parents)} 份"
              f"/{sum(len(x) for x in fed_texts)} 字")
        connection.close()


def packets_of(connection):
    """把库里的材料读回成 `MaterialPacket`（`select_chunks` 要的是这个形状）。"""
    from pmstudio.bootstrap.container import UuidIdGenerator
    from pmstudio.infrastructure.sqlite.materials import SqliteMaterialStore

    store = SqliteMaterialStore(connection, ids=UuidIdGenerator())
    packets = []
    for material_id, in connection.execute("SELECT material_id FROM materials").fetchall():
        packet = store.get(str(material_id), owner=OWNER)
        if packet is not None:
            packets.append(packet)
    return packets


if __name__ == "__main__":
    main()
