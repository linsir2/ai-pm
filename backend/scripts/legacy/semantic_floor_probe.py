"""B3 探针：**语义单路候选要不要加下限**——先量分布，再决定（2026-10-21）。

## 问题

混合检索里「只有 KNN 命中、词面零命中」的候选能靠 `0.5·cos` 拿到整份语义权重 →
**一个整页里根本没有的词**（实测 "logo"）也能把材料搜出来（成因=向量余弦）。

## 量法

两组查询，同一套语料、同一条检索路：

- **正样本**：手写的 14 条**改述**（词面 0 命中，语义该命中）→ 量目标材料的余弦；
- **负样本**：**语料里根本没有的概念**（别的领域的真实词）→ 量任何材料的最大余弦。

**能不能分开** = 有没有一个阈值 T，使得
「正样本余弦 > T」全中 **且** 「负样本余弦 ≤ T」全不中。

- 能分开 → 加**语义腿绝对余弦下限**（只作用于"只有 KNN 命中"的候选，融合公式不动）；
- **不能分开 → 不加阀**（宁可假阳性也不误杀改述——那是已验收的正指标），
  并把分布数据写进 `04-未决.md`。

跑法：
    cd backend && HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \\
        uv run python -u scripts/semantic_floor_probe.py
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
    SqliteMaterialStore,
)
from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
from pmstudio.infrastructure.tools.extract import chunk_text
from pmstudio.infrastructure.tools.limits import CHUNK_CHARS
from pmstudio.infrastructure.tools.webclean import normalize_web_text

OUT = Path("../.codex/runs/2026-10-19/tavily_raw")
PROJECT, OWNER = "p1", "o1"

#: **负样本**：语料（15 份 RAG/推理类材料）里**根本没有**的概念。
#: 必须是**真实的**用户会输的词（不是"zqxjkvwq"那种乱码）——它们是"搜了一个
#: 库里没有的东西"的真实场景；乱码与真实词的余弦分布不一样，不能代表。
NEGATIVE_QUERIES = [
    ("postgresql 索引 优化", "postgresql"),
    ("rust 所有权 借用检查", "rust"),
    ("嵌入式 电机 驱动", "嵌入式"),
    ("邮件协议 DKIM 签名", "DKIM"),
    ("webpack 打包 体积", "webpack"),
    ("汇编 寄存器 溢出", "汇编"),
    ("kubernetes 探针 滚动更新", "kubernetes"),
    ("latex 参考文献 格式", "latex"),
]


def tokens(text: str) -> list[str]:
    from pmstudio.infrastructure.sqlite.tokenize import index_tokens

    return index_tokens(text).split()


def main() -> None:
    corpus_path = OUT / "C raw_content-净.json"
    if not corpus_path.exists():
        raise SystemExit(f"冻结语料不在 {corpus_path}；先跑 scripts/tavily_raw_bench.py")
    items = json.loads(corpus_path.read_text(encoding="utf-8"))
    para = json.loads((OUT / "paraphrases.json").read_text(encoding="utf-8"))["paraphrases"]

    embedder = FastEmbedEmbedder()
    db = OUT / "semantic_floor.db"
    if db.exists():
        db.unlink()
    connection = connect(db, embedder=embedder)
    store = SqliteMaterialStore(connection, ids=UuidIdGenerator())
    vector_index = SqliteVectorIndex(connection, embedder=embedder)
    url2mid = {}
    for item in items:
        chunks = chunk_text(
            normalize_web_text(item["text"]), parser_id="tavily", max_chars=CHUNK_CHARS
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
    corpus_words = set()
    for item in items:
        corpus_words.update(tokens(item["text"]))

    print(f"语料 {len(items)} 份材料\n")
    print("── 负样本：这些**领域特征词**在语料里存在吗？ ──")
    print("   （判据只看领域特征词——「优化」「索引」这种通用词谁都有，"
          "要求所有词都不在语料会把负样本判没）")
    usable = []
    for query, key in NEGATIVE_QUERIES:
        present = key in corpus_words
        print(f"  {query:26} 特征词「{key}」" + ("✗ 语料里有" if present else "✓ 语料里没有"))
        if not present:
            usable.append(query)
    print()

    print("── 正样本（改述）：目标材料的余弦 vs 负样本的最大余弦 ──")
    print(f"{'查询':34} {'类型':>4} {'词面':>4} {'语义最大余弦':>12} {'目标余弦':>9}")
    print("-" * 74)
    positives: list[float] = []
    negatives: list[float] = []
    for index, queries in sorted(para.items(), key=lambda kv: int(kv[0])):
        target = url2mid[items[int(index)]["url"]]
        for query in queries:
            lexical = len(fts.search(project_id=PROJECT, query=query, limit=5, owner=OWNER))
            # **目标材料**的余弦（不是全库最大——量错对象会得出错的判据）
            best = _max_cosine(connection, embedder, query, PROJECT, OWNER, only=target)
            positives.append(best)
            print(f"{query[:32]:34} {'改述':>4} {lexical:>4} {'':>12} {best:>9.4f}")
    for query in usable:
        lexical = len(fts.search(project_id=PROJECT, query=query, limit=5, owner=OWNER))
        best = _max_cosine(connection, embedder, query, PROJECT, OWNER)
        negatives.append(best)
        print(f"{query[:32]:34} {'负':>4} {lexical:>4} {best:>12.4f} {'—':>9}")

    print("-" * 74)
    print(f"正样本（改述）余弦：min {min(positives):.4f} / 中位 "
          f"{sorted(positives)[len(positives)//2]:.4f} / max {max(positives):.4f}")
    print(f"负样本余弦：min {min(negatives):.4f} / 中位 "
          f"{sorted(negatives)[len(negatives)//2]:.4f} / max {max(negatives):.4f}")
    threshold = min(positives)
    separated = all(value < threshold for value in negatives)
    print("\n判据：是否存在阈值 T 使「正样本 > T」全中且「负样本 ≤ T」全不中？")
    print(f"   取最保守的 T = min(正样本) = {threshold:.4f} → "
          f"{'**能分开**' if separated else '**不能分开**'}")
    if not separated:
        print("   → 按设计文档的退路：**不加阀**（宁可假阳性也不误杀改述），"
              "把分布数据写进 04-未决.md。")
    report = {
        "positives": positives,
        "negatives": negatives,
        "threshold_try": threshold,
        "separable": separated,
        "usable_negatives": usable,
    }
    (OUT / "semantic_floor_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    connection.close()


def _max_cosine(connection, embedder, query: str, project: str, owner: str, *,
                only: str | None = None) -> float:
    """查询与块的最大真余弦（1 - d²/2）。`only` 限定只看某份材料。"""
    from pmstudio.infrastructure.sqlite.tokenize import index_text

    vector = embedder.embed_many([query])[0]
    rows = connection.execute(
        "SELECT c.chunk_id, c.text, c.heading_path, m.title, m.material_id"
        " FROM material_chunks c JOIN materials m ON m.material_id = c.material_id"
        " WHERE m.project_id = ? AND c.owner_id = ?"
        + (" AND m.material_id = ?" if only else ""),
        (project, owner) + ((only,) if only else ()),
    ).fetchall()
    best = 0.0
    texts = [
        index_text(title=row["title"], heading_path=json.loads(row["heading_path"] or "[]"),
                   text=row["text"])
        for row in rows
    ]
    vectors = embedder.embed_many(texts)
    for vec in vectors:
        cos = 1 - sum((a - b) ** 2 for a, b in zip(vector, vec, strict=True)) / 2
        best = max(best, cos)
    return best


if __name__ == "__main__":
    main()
