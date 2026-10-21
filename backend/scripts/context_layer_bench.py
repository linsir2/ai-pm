"""上下文层的基线：AI 每轮**实际被喂进提示词**的块里，有多少是无关的（批 8）。

为什么要有这个：检索层的**材料级召回已经满了**（limit=50 时 1.000）→ 检索侧没有
提升空间了 → 剩下的质量都在**召回之后**（选块进提示词这一步）。而这一步
（`select_chunks`）此前**没有任何指标**。

量的东西（都用真材料 + 真 `select_chunks`，不需要模型——它的粗排是纯词面 2-gram）：
- 每轮进了几块 / 几份材料 / 占多少字
- **无关块比例**：用「库外概念」的负样本查询跑一遍，看提示词里进了多少与查询无关的块
- **证据覆盖**：跨块查询下，同一份材料的两处证据有几处进了提示词
"""
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")

from pmstudio.application.context import (
    MATERIAL_BUDGET,
    select_chunks,
    web_block_cost,
)
from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
from pmstudio.infrastructure.sqlite.materials import SqliteMaterialStore
from pmstudio.infrastructure.tools.extract import chunk_text
from pmstudio.infrastructure.tools.webclean import normalize_web_text

OUT = Path("../.codex/runs/2026-10-19/tavily_raw")
PROJECT, OWNER = "p1", "o1"
NEGATIVE_QUERIES = [
    "postgresql 索引 优化", "rust 所有权 借用检查", "嵌入式 电机 驱动",
    "邮件协议 DKIM 签名", "webpack 打包 体积", "汇编 寄存器 溢出",
    "kubernetes 探针 滚动更新", "latex 参考文献 格式",
]


def main() -> None:
    items = json.loads((OUT / "C raw_content-净.json").read_text(encoding="utf-8"))
    embedder = FastEmbedEmbedder()
    db = OUT / "ctx_layer.db"
    if db.exists():
        db.unlink()
    connection = connect(db, embedder=embedder)
    store = SqliteMaterialStore(connection, ids=UuidIdGenerator())
    packets = []
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
        packets.append(
            store.put(project_id=PROJECT, parsed=parsed,
                       sha256=hashlib.sha256(item["url"].encode()).hexdigest(),
                       content=item["text"].encode(),
                       now=datetime(2026, 10, 19, tzinfo=UTC), owner=OWNER)
        )
    connection.commit()

    print("=== 上下文层基线（每轮进提示词的东西）===")
    for query in NEGATIVE_QUERIES[:3] + ["向量检索 召回效果"]:
        picked = select_chunks(packets, budget=MATERIAL_BUDGET, cost=web_block_cost,
                               query=query)
        # `select_chunks` 返回 ((material_id, chunk), ...) 的元组
        materials = {material_id for material_id, _ in picked}
        chars = sum(len(chunk.text) for _, chunk in picked)
        label = "负样本" if query in NEGATIVE_QUERIES else "正常查询"
        print(f"  [{label}] {query:22} → {len(picked)} 块 / {len(materials)} 份材料 / {chars} 字")
    print(f"\n  预算：MATERIAL_BUDGET={MATERIAL_BUDGET} 字")
    print("  读法：**负样本那一行就是「AI 被喂了多少无关材料」**——检索层治不了假阳性"
          "（语义阈值不可分），只剩这一层。")
    connection.close()


if __name__ == "__main__":
    main()
