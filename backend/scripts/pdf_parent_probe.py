"""R5 探针：**PDF 的父块怎么分**（批 16，2026-10-21）。

## 要回答的

批 10 的教训：文本路的「子块检索 + 父块返回」被**更细的块**取代了（上下文 ×3.3）。
R5 只做 PDF（docx/xlsx 的块**已经是结构单元**，父块=自己，实测体积比 1.00× → 零增量）。

父块不参与索引与检索 → **检索指标一律不变** → 收益与代价全在**上下文层**。
所以要量的是：

1. **候选划分**：页 / 页+相邻页（修跨页断裂）/ 页内累积到 N 字；
2. 每个方案的：父块数与体积、**完整句占比**、**跨页断裂是否修好**、**同预算下覆盖多少页**；
3. **块级 recall@5 不退化**（改动只在渲染层 → 必然不变，但要验）。

## 语料

`.codex/runs/2026-10-01/L3/materials/海外仓作业规范.pdf`（真 PDF，10 页）+
`.codex/runs/2026-10-02/全量真跑审计/L3/materials/平台API对接说明.docx`（真 docx）
+ `…/财务对账口径.xlsx`（真 xlsx）。外网不通，抓不到更多 PDF（2026-10-21 实测）。

## 跑法

```bash
cd backend && uv run python -u scripts/pdf_parent_probe.py
"""

import re
import sys
from collections import defaultdict
from io import BytesIO
from pathlib import Path

sys.path.insert(0, "src")

from pmstudio.infrastructure.tools.extract import chunk_pages

RUNS = Path("../.codex/runs")
PDF = RUNS / "2026-10-01/L3/materials/海外仓作业规范.pdf"
SENTENCE_END = "。！？；!?;"
#: 父块方案：(标签, 组块方式)
PLANS = [
    ("页", "page"),
    ("页+相邻页", "page+1"),
    ("页内累积400字", "acc400"),
    ("页内累积800字", "acc800"),
]


def _pages(path: Path) -> list[str]:
    from pdfminer.high_level import extract_text as pdf_extract_text

    return pdf_extract_text(BytesIO(path.read_bytes())).split("\f")


def _is_complete(text: str) -> bool:
    """这段是不是**完整的话**（以句末标点结尾）。"""
    return text.rstrip().endswith(tuple(SENTENCE_END))


def main() -> None:
    pages = _pages(PDF)
    chunks = chunk_pages(pages, parser_id="pdf")
    print(f"真 PDF：{len(pages)} 页 · {len(chunks)} 块")
    sizes = [len(c.text) for c in chunks]
    print(f"  子块字数：min {min(sizes)} / 中位 {sorted(sizes)[len(sizes)//2]} / max {max(sizes)}")
    complete = sum(1 for c in chunks if _is_complete(c.text))
    print(f"  **完整句占比**（子块）：{complete}/{len(chunks)} = {complete/len(chunks):.0%}")
    # 跨页断裂：前页末段没句号 → 次页首段接着
    by_page: dict[str, list] = defaultdict(list)
    for c in chunks:
        m = re.search(r"第 (\d+) 页", c.anchor)
        by_page[m.group(1) if m else "?"].append(c)
    broken = 0
    page_order = sorted(by_page, key=lambda p: int(p) if p.isdigit() else 0)
    for prev, nxt in zip(page_order, page_order[1:], strict=False):
        if by_page[prev] and by_page[nxt] and not _is_complete(by_page[prev][-1].text):
            broken += 1
    print(f"  **跨页断裂**（前页末段没句号）：{broken} 处")

    print("\n### 三个父块划分的对照")
    for label, mode in PLANS:
        groups: dict[str, list] = defaultdict(list)
        for c in chunks:
            m = re.search(r"第 (\d+) 页", c.anchor)
            page = m.group(1) if m else "?"
            if mode.startswith("page"):
                key = page
                if mode == "page+1":
                    key = f"{page}±1"
            else:
                key = f"{page}#{c.ordinal}"
            groups[key].append(c)
        # 「页+相邻页」：相邻两页各算一个父块（不真的合并）→ 覆盖面更广
        if mode == "page+1":
            merged: dict[str, list] = defaultdict(list)
            for c in chunks:
                m = re.search(r"第 (\d+) 页", c.anchor)
                page = int(m.group(1)) if m else 0
                for p in (page - 1, page, page + 1):
                    merged[f"p{p}"].append(c)
            groups = merged
        elif mode.startswith("acc"):
            # 按**累计字数**在同一页内分组（切在句末 → 完整句应接近 100%）
            width = int(mode[3:])
            merged = defaultdict(list)
            current: list = []
            size = 0
            page_now = None
            for c in chunks:
                m = re.search(r"第 (\d+) 页", c.anchor)
                page = m.group(1) if m else "?"
                if page != page_now and current:
                    merged[f"{page_now}#{len(merged)}"] = current
                    current, size = [], 0
                page_now = page
                if current and size + len(c.text) > width:
                    merged[f"{page}#{len(merged)}"] = current
                    current, size = [], 0
                current.append(c)
                size += len(c.text)
            if current:
                merged[f"{page_now}#{len(merged)}"] = current
            groups = merged
        parent_sizes = [sum(len(c.text) for c in g) for g in groups.values()]
        ptexts = ["\n".join(c.text for c in g) for g in groups.values()]
        pcomplete = sum(1 for t in ptexts if _is_complete(t))
        pages_covered = {
            m.group(1)
            for c in chunks
            if (m := re.search(r"第 (\d+) 页", c.anchor))
        }
        print(
            f"  {label:12} 父块 {len(groups):>3} 个"
            f" · 体积 min {min(parent_sizes):>4}"
            f" / 中位 {sorted(parent_sizes)[len(parent_sizes)//2]:>4}"
            f" / max {max(parent_sizes):>4}"
            f" · 完整句占比 {pcomplete}/{len(ptexts)} = {pcomplete/len(ptexts):.0%}"
            f" · 覆盖 {len(pages_covered)} 页"
        )


if __name__ == "__main__":
    main()
