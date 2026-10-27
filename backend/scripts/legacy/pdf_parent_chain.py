"""R5 收尾核验：PDF 父块走**生产链**（批 16，2026-10-22）。

解析（`LocalMaterialParser`）→ 入库（`SqliteMaterialStore`）→ 取回（`get`）→
选块（`select_chunks`）→ 发号 → 渲染（`render_materials`）。

不是测试替身、不调模型。要验的是：**同一页只选中一部分块时，整页正文仍进提示词**，
且编号合并在同一行（`[E1][E2] 第 8 页`）。

跑法：

```bash
cd backend && uv run python -u scripts/pdf_parent_chain.py
```

判据（脚本最后一行）：`PDF_CHAIN_OK` / `PDF_CHAIN_FAILED`。
语料用仓库里那份真 PDF（`.codex/runs/2026-10-01/L3/materials/海外仓作业规范.pdf`）；
外网抓不到更多 PDF（2026-10-21 实测）。
"""

import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")

from pmstudio.application.context import render_materials, select_chunks
from pmstudio.application.dto import page_of
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.domain.shared.ids import EvidenceId
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.materials import SqliteMaterialStore
from pmstudio.infrastructure.tools.extract import LocalMaterialParser

PDF = Path("../.codex/runs/2026-10-01/L3/materials/海外仓作业规范.pdf")
DB = Path("/tmp/pmstudio-pdf-chain.db")
PROJECT, OWNER = "p-e2e", "owner-e2e"
#: 要验的那一页：真 PDF 里它有多块，且查询只命中其中一部分。
TARGET_PAGE = 8


def main() -> int:
    data = PDF.read_bytes()
    parsed = LocalMaterialParser().parse(
        filename=PDF.name, media_type="application/pdf", data=data
    )
    print(f"解析：{len(parsed.chunks)} 块 · parser={parsed.parser_id} {parsed.parser_version}")

    DB.unlink(missing_ok=True)
    connection = connect(DB)
    store = SqliteMaterialStore(connection, ids=UuidIdGenerator())
    material_id = store.put(
        project_id=PROJECT,
        parsed=parsed,
        sha256=hashlib.sha256(data).hexdigest(),
        content=data,
        now=datetime(2026, 10, 22, tzinfo=UTC),
        owner=OWNER,
    ).material_id
    connection.commit()

    packet = store.get(material_id, owner=OWNER)
    if packet is None:
        print("入库后取不回来")
        return 1
    if len(packet.parsed.chunks) != len(parsed.chunks):
        print("块数在存储往返后变了")
        return 1
    page = [c for c in packet.parsed.chunks if page_of(c.anchor) == TARGET_PAGE]
    print(f"存储往返：{len(packet.parsed.chunks)} 块，第 {TARGET_PAGE} 页 {len(page)} 块")

    picked, _ = select_chunks([packet], query=page[0].text[:20])
    picked_ids = {chunk.chunk_id for _, chunk in picked}
    hit = [chunk for _, chunk in picked if page_of(chunk.anchor) == TARGET_PAGE]
    siblings_not_picked = [c for c in page if c.chunk_id not in picked_ids]
    print(f"选块：{len(picked)} 块；第 {TARGET_PAGE} 页选中 {len(hit)} 块"
          f"，未选中的同页块 {len(siblings_not_picked)} 个")
    if not hit:
        print(f"第 {TARGET_PAGE} 页一块都没选中 → 验不到父块")
        return 1
    if not siblings_not_picked:
        print(f"第 {TARGET_PAGE} 页全被选中 → 这条核验是空的")
        return 1

    eids = {
        chunk.chunk_id: EvidenceId(f"E{index + 1}")
        for index, (_, chunk) in enumerate(picked)
    }
    rendered = render_materials([(packet, eids)])
    line = next(
        line for line in rendered.text.splitlines()
        if line.endswith(f"第 {TARGET_PAGE} 页")
    )
    whole_page = all(chunk.text in rendered.text for chunk in page)
    print(f"渲染编号行：{line}")
    print(f"整页正文都在提示词里：{whole_page}")
    print(f"used_chunk_ids={len(rendered.used_chunk_ids)}（选中 {len(picked)}）")

    ok = whole_page and line.count("[E") >= 2
    print("PDF_CHAIN_OK" if ok else "PDF_CHAIN_FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
