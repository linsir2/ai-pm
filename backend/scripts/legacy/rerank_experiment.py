"""**二阶段检索（粗排 → 精排）实验**（批 9，2026-10-21）。

## 要回答的问题

检索层的**材料级召回已经满了**（limit=50 时 1.000）→ 检索侧没有提升空间 →
剩下的质量全在**召回之后**。而假阳性（库里没有的概念也返回候选，8/8）**治不了**：
批 7 已证**绝对余弦阈值不可分**（改述与负样本的余弦分布完全重叠）。

**cross-encoder 正好判「这段到底答不答这个问题」** —— 这是 bi-encoder 做不到的
（它只看向量距离，不看词与段落的交互）。

## 实验设计

- **粗排**：现有混合检索（FTS5 + KNN → 凸组合）取前 N 个**块**；
- **精排**：cross-encoder 对 (查询, 块) 逐对打分 → 重排 → 取前 K；
- **对照**：无重排（现状）。

**指标**（六个，与 `retrieval_bench.py` 同一套口径）：
材料级 r5 · **块级 r5** · MRR · **跨块证据完整率** ·
**上下文层的无关块数**（负样本查询下，有多少无关块会被喂进提示词）· **延迟**。

## 跑法

```bash
cd backend && HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \\
    uv run python -u scripts/rerank_experiment.py
```
"""

import hashlib
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")

import jieba

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
from pmstudio.infrastructure.sqlite.materials import (
    SqliteHybridRetriever,
    SqliteMaterialStore,
)
from pmstudio.infrastructure.sqlite.tokenize import index_text
from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
from pmstudio.infrastructure.tools.extract import chunk_text
from pmstudio.infrastructure.tools.webclean import normalize_web_text

OUT = Path("../.codex/runs/2026-10-19/tavily_raw")
PROJECT, OWNER = "p1", "o1"

#: 候选重排模型（按体积从小到大）。`v2-m3-int8` 0.57GB、base 1.04GB。
MODELS = ["BAAI/bge-reranker-base"]   # fp32：用来分辨「int8 损坏了排序」还是「重排本身不适合」

#: 库外概念（负样本）：库里根本没有的东西 → 假阳性的探针
NEGATIVE_QUERIES = [
    "postgresql 索引 优化", "rust 所有权 借用检查", "嵌入式 电机 驱动",
    "邮件协议 DKIM 签名", "webpack 打包 体积", "汇编 寄存器 溢出",
    "kubernetes 探针 滚动更新", "latex 参考文献 格式",
]

#: 粗排候选数 / 精排取几条
GRID = [(20, 5), (50, 5)]   # 重排很慢（秒级/查询），只测两格


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


def build(items, embedder):
    path = OUT / "rerank.db"
    if path.exists():
        path.unlink()
    connection = connect(path, embedder=embedder)
    store = SqliteMaterialStore(connection, ids=UuidIdGenerator())
    vector_index = SqliteVectorIndex(connection, embedder=embedder)
    url2mid = {}
    mid2chunks = {}
    titles = {}
    for item in items:
        clean = normalize_web_text(item["text"])
        chunks = chunk_text(clean, parser_id="tavily", max_chars=400)
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
        titles[packet.material_id] = item["title"]
    connection.commit()
    chunk_by_id = {
        f"{mid}#c{chunk.ordinal:02d}": (chunk.text, chunk.heading_path)
        for mid, chunks in mid2chunks.items()
        for chunk in chunks
    }
    return connection, url2mid, mid2chunks, titles, chunk_by_id


def _subset(group, n):
    """取子集（重排是秒级/查询，全量跑不动）。**按固定顺序取前 n**，可复跑。"""
    return group[:n]


def make_queries(items, url2mid, mid2chunks):
    frozen = json.loads((OUT / "frozen_queries.json").read_text(encoding="utf-8"))
    lexical = [(w, url) for w, _q, url in frozen["items"]]
    para = json.loads((OUT / "paraphrases.json").read_text(encoding="utf-8"))["paraphrases"]
    url_by_index = {i: it["url"] for i, it in enumerate(items)}
    paraphrase = [
        (q, url_by_index[int(i)])
        for i, qs in sorted(para.items(), key=lambda kv: int(kv[0]))
        for q in qs
    ]
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
        cross.append((f"{first[0]} {second[0]}", item["url"], (f"{mid}#c06", f"{mid}#c10")))
    # 缩减：重排秒级/查询 → 每类取前若干条（固定顺序，可复跑）
    return _subset(lexical, 20), _subset(paraphrase, 8), _subset(cross, 8)


def rerank(cross_encoder, query, hits, chunk_by_id, titles):
    """对粗排结果重排（cross-encoder 逐对打分）。

    `MaterialHit` **不带块正文**（只有 excerpt）→ 用 chunk_id 映射回正文
    （chunk_id = `{material_id}#c{ordinal:02d}`，与入库时的盖章规则一致）。
    """
    docs = [
        index_text(title=titles[hit.material_id],
                   heading_path=chunk_by_id[hit.chunk_id][1],
                   text=chunk_by_id[hit.chunk_id][0])
        for hit in hits
    ]
    # fastembed 的 `rerank` 返回**与输入同序的分数**（不是 (分数, 下标) 对）
    scores = list(cross_encoder.rerank(query, docs))
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return [hits[index] for index in order]


def measure(retriever, reranker, queries, url2mid, mid2chunks, titles, chunk_by_id, *, grid, limit):
    """量六个指标（limit = 粗排候选数；top_k = 精排取几条）。"""
    lexical, paraphrase, cross = queries
    negatives = NEGATIVE_QUERIES
    out = {"_词面n": len(lexical), "_改述n": len(paraphrase),
           "_跨块n": len(cross), "_负样本n": len(negatives)}
    out["_延迟ms"] = 0.0
    for name, group in (("词面", lexical), ("改述", paraphrase)):
        material_hits = chunk_hits = 0
        rrs = []
        total = 0
        for query, url in group:
            mid = url2mid.get(url)
            if mid is None:
                continue
            start = time.perf_counter()
            hits = retriever.search(project_id=PROJECT, query=query, limit=limit, owner=OWNER)
            if reranker is not None and hits:
                hits = rerank(reranker, query, hits[:limit], chunk_by_id, titles)[:grid]
            total += (time.perf_counter() - start) * 1000
            ids = [hit.material_id for hit in hits]
            if mid in ids:
                material_hits += 1
                rrs.append(1.0 / (ids.index(mid) + 1))
            wanted = {
                f"{mid}#c{i:02d}"
                for i, chunk in enumerate(mid2chunks.get(mid, []))
                if query in chunk.text
            }
            if wanted and any(hit.chunk_id in wanted for hit in hits):
                chunk_hits += 1
        n = max(1, len(group))
        out[f"{name}_材料r5"] = material_hits / n
        out[f"{name}_块r5"] = chunk_hits / n
        out[f"{name}_MRR"] = sum(rrs) / max(1, len(rrs))
        out["_延迟ms"] += total / n
    # 跨块
    complete = 0
    for query, url, wanted in cross:
        mid = url2mid.get(url)
        if mid is None:
            continue
        hits = retriever.search(project_id=PROJECT, query=query, limit=limit, owner=OWNER)
        if reranker is not None and hits:
            hits = rerank(reranker, query, hits[:limit], chunk_by_id, titles)[:grid]
        got = {hit.chunk_id for hit in hits}
        if set(wanted) <= got:
            complete += 1
    out["跨块完整率"] = complete / max(1, len(cross))
    # 负样本 → **上下文层会喂进去多少无关块**
    noise = 0
    for query in negatives:
        hits = retriever.search(project_id=PROJECT, query=query, limit=limit, owner=OWNER)
        if reranker is not None and hits:
            hits = rerank(reranker, query, hits[:limit], chunk_by_id, titles)[:grid]
        noise += len(hits)
    out["负样本块数"] = noise / max(1, len(negatives))
    return out


def show(label, out):
    print(f"  {label:26} 材料r5 {out['词面_材料r5']:.3f} · 块r5 {out['词面_块r5']:.3f}"
          f" · MRR {out['词面_MRR']:.3f} · 改述r5 {out['改述_材料r5']:.3f}"
          f" · 跨块 {out['跨块完整率']:.3f} · 负样本块 {out['负样本块数']:.1f}"
          f" · {out['_延迟ms']:.0f}ms")


def main() -> None:
    items = json.loads((OUT / "C raw_content-净.json").read_text(encoding="utf-8"))
    embedder = FastEmbedEmbedder()
    connection, url2mid, mid2chunks, titles, chunk_by_id = build(items, embedder)
    queries = make_queries(items, url2mid, mid2chunks)
    retriever = SqliteHybridRetriever(connection, embedder=embedder)

    print("=== 对照：现状（无精排）===")
    for limit, top_k in GRID:
        out = measure(retriever, None, queries, url2mid, mid2chunks, titles, chunk_by_id,
                      grid=top_k, limit=limit)
        show(f"粗排 {limit} → 取 {top_k}", out)

    for model_name in MODELS:
        print(f"\n=== 精排：{model_name} ===")
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            cross_encoder = TextCrossEncoder(model_name=model_name)
        except Exception as error:  # noqa: BLE001 —— 模型下不来就跳过（不假装）
            print(f"  ✗ 不可用：{type(error).__name__}: {str(error)[:120]}")
            continue
        for limit, top_k in GRID:
            out = measure(retriever, cross_encoder, queries, url2mid, mid2chunks, titles,
                          chunk_by_id, grid=top_k, limit=limit)
            show(f"粗排 {limit} → 取 {top_k}", out)
    connection.close()


if __name__ == "__main__":
    main()
