"""R5 探针：结构化文档（docx / xlsx / pdf）上，**父块 vs 子块**在上下文层的差异（批 16 调研）。

## 为什么要量

批 10 的教训：文本路上「子块检索 + 父块返回」被**更细的块**取代了——父块把上下文撑到
3.3 倍，同预算下别的材料进不来。R5 若不先证明在**结构化路**上有收益，就是重复那个错误。

**父块不参与检索**（子块检索、父块返回）→ **检索指标完全一样** → 收益与代价全在
**上下文层**：同样 4000 字预算下，喂进去的东西**覆盖了多少个完整的章节/页**。

## 三条路的"父块"单位（由块的形态决定，不是"相邻 N 块"）

| 路径 | 块单位 | 父块 = | 依据 |
|---|---|---|---|
| docx | 标题起新块 + 表格独占一块 | **同 `heading_path` 的连续块组** | 同节连续 ✓ |
| xlsx | **一个工作表一块** | **无**（已是工作表粒度，没有更细的子块） | 无需做 |
| pdf | 每页每段一块 | **同页的块组** | 同页连续 ✓ |

## 跑法

```bash
cd backend && uv run python -u scripts/structured_parent_probe.py
```
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "src")

from pmstudio.application.context import MATERIAL_BUDGET, select_chunks
from pmstudio.application.dto import MaterialChunk, MaterialPacket, ParsedMaterial
from pmstudio.infrastructure.tools.extract import chunk_pages
from pmstudio.infrastructure.tools.office import docx_chunks, xlsx_chunks

RUNS = Path("../.codex/runs")
SAMPLES = [
    ("docx", RUNS / "2026-10-02/全量真跑审计/L3/materials/平台API对接说明.docx"),
    ("xlsx", RUNS / "2026-10-02/全量真跑审计/L3/materials/财务对账口径.xlsx"),
    ("pdf", RUNS / "2026-10-01/L3/materials/海外仓作业规范.pdf"),
]


def parse(kind: str, path: Path):
    """走**生产解析路径**（真 docx / xlsx / pdf）。"""
    data = path.read_bytes()
    if kind == "docx":
        chunks, truncated = docx_chunks(data)
        return chunks, truncated, ()
    if kind == "xlsx":
        chunks, truncated, warnings = xlsx_chunks(data)
        return chunks, truncated, warnings
    # PDF 走 **pdfminer**（生产路径 `_from_pdf` 就是它），不是 decode_text
    from io import BytesIO

    from pdfminer.high_level import extract_text as pdf_extract_text

    # 生产路径（`_from_pdf`）就是传 BytesIO
    text = pdf_extract_text(BytesIO(data))
    pages = text.split("\f")
    chunks = chunk_pages(pages, parser_id="pdf")
    truncated = False
    return chunks, truncated, ()


def parent_key(kind: str, chunk: MaterialChunk) -> str:
    """这条块的**父块键**（结构单元）。"""
    if kind == "pdf":
        # 锚点形如「第 3 页（第 2 段）」→ 取页号
        match = re.search(r"第 (\d+) 页", chunk.anchor)
        return f"page-{match.group(1)}" if match else f"page-?{chunk.ordinal}"
    if kind == "docx":
        if chunk.anchor.startswith("表格"):
            # 表格独占一块 → 自成父块
            return f"table-{chunk.anchor}"
        # 同节 = 同 heading_path
        return "section-" + "|".join(chunk.heading_path)
    return "sheet-" + "|".join(chunk.heading_path)          # xlsx：工作表即父块


def main() -> None:
    for kind, path in SAMPLES:
        if not path.exists():
            print(f"### {kind}: 缺样本 {path}")
            continue
        chunks, truncated, warnings = parse(kind, path)
        sizes = [len(c.text) for c in chunks]
        groups = defaultdict(list)
        for chunk in chunks:
            groups[parent_key(kind, chunk)].append(chunk)
        parent_sizes = [
            sum(len(c.text) for c in group) for group in groups.values()
        ]
        print(f"\n### {kind} · {path.name}")
        print(f"  块数 {len(chunks)} · 截断 {truncated} · 警告 {len(warnings)}")
        if sizes:
            print(
                f"  子块字数：min {min(sizes)} / 中位 {sorted(sizes)[len(sizes)//2]}"
                f" / max {max(sizes)}"
            )
        print(f"  父块组数 {len(groups)} · 父块字数：", end="")
        if parent_sizes:
            print(
                f"min {min(parent_sizes)} / 中位 {sorted(parent_sizes)[len(parent_sizes)//2]}"
                f" / max {max(parent_sizes)}"
            )
        else:
            print("—")
        ratio = (sum(parent_sizes) / sum(sizes)) if sizes and parent_sizes else 0
        print(f"  父块/子块 体积比 {ratio:.2f}×")
        # 同一份材料里，父块数 vs 子块数
        child_fit = MATERIAL_BUDGET // max(1, sorted(sizes)[len(sizes) // 2])
        parent_fit = (
            MATERIAL_BUDGET // max(1, sorted(parent_sizes)[len(parent_sizes) // 2])
            if parent_sizes
            else 0
        )
        print(
            f"  同预算 {MATERIAL_BUDGET} 字：子块约 {child_fit} 块 · 父块约 {parent_fit} 组"
        )
        # 真实命中：用材料里的一段话当查询，看两种口径各能覆盖多少结构单元
        packet = MaterialPacket(
            material_id="m1", project_id="p1", created_at="2026-10-21T00:00:00+00:00",
            parsed=ParsedMaterial(
                origin="upload", title=path.name, source_ref=f"attachment:{path.name}",
                media_type="application/octet-stream", bytes_len=path.stat().st_size,
                chunks=chunks, parser_id=kind, parser_version="probe",
                truncated=truncated, warnings=tuple(warnings),
            ),
        )
        probe_text = chunks[len(chunks) // 2].text[:40]
        picked, _ = select_chunks([packet], budget=MATERIAL_BUDGET, query=probe_text)
        units = {parent_key(kind, chunk) for _mid, chunk in picked}
        covered = sum(len(chunk.text) for _mid, chunk in picked)
        print(
            f"  查「{probe_text[:18]}…」→ 选 {len(picked)} 块 / {covered} 字"
            f" / 覆盖 {len(units)} 个结构单元"
        )


if __name__ == "__main__":
    main()
