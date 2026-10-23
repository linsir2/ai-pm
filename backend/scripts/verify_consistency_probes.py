"""核验批 6c 的**口径一致性**：`bytes_len` / 去重键 / 预算记账，三处对不对得上。

**这不是测试替身**：真 SQLite、真嵌入模型、真 `HttpxWebFetcher`（真 HTTP、真重定向）、
真 `UploadMaterial` / `FetchMaterial` 用例、真 `SqliteMaterialStore`。
用到的"联网网关"是固定报文（**替身**，只用来让 `SubmitOpening` 走一遍入库）。

三个探针：

- **P3 `bytes_len` 的含义**：库里 `materials.bytes_len` 写的是多少？与真正存进
  `materials.content` 的字节数一致吗？（三条入口各是什么口径）
- **P4 抓网页的去重键**：跳转到 https 之后再抓同一个页面，会不会多出一份？
  （去重键用**原地址**、来源写**最终地址** → 两者会分叉）
- **P5 联网结果的预算记账**：`web_block_cost`（选块时算的）与
  `render_web_results`（渲染时真正写出去的）差多少？会不会"选进来了却渲染不出去"？

跑法：
    cd backend && uv run python -u scripts/verify_consistency_probes.py
"""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")

from pmstudio.application.context import (
    WEB_BUDGET,
    render_web_results,
    select_chunks,
    web_block_cost,
)
from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.application.use_cases import FetchMaterial, UploadMaterial
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.domain.shared.ids import ProjectId, UserId
from pmstudio.domain.shared.template import standard_template
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
from pmstudio.infrastructure.sqlite.materials import SqliteMaterialStore
from pmstudio.infrastructure.sqlite.owner_scope import clear_owner_scope, set_owner_scope
from pmstudio.infrastructure.sqlite.project_factory import SqliteProjectFactory
from pmstudio.infrastructure.sqlite.repositories import SqliteDocumentRepository
from pmstudio.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
from pmstudio.infrastructure.tools.extract import LocalMaterialParser
from pmstudio.infrastructure.tools.fetch import HttpxWebFetcher

OUT = Path("../.codex/runs/2026-10-20/verify")
DB = OUT / "consistency.db"
PROJECT = ProjectId("p-cons")
OWNER = UserId("owner-cons")
NOW = datetime(2026, 10, 20, 13, 0, tzinfo=UTC)

HTML = b"""<html><head><title>probe</title></head><body><article>
<h1>probe</h1><p>body text</p></article></body></html>"""

#: 会 301 到 https 的地址（抓它 → 最终地址与原地址不同）
REDIRECT_URL = "http://docs.python.org/3/library/sqlite3.html"
FINAL_URL = "https://docs.python.org/3/library/sqlite3.html"


class FixedClock:
    def now(self) -> datetime:
        return NOW


def _bytes_of(material_id: str, connection) -> int:  # type: ignore[no-untyped-def]
    row = connection.execute(
        "SELECT length(content) FROM materials WHERE material_id = ?", (material_id,)
    ).fetchone()
    return int(row[0])


def probe_p3_bytes_len(connection, store) -> dict:  # type: ignore[no-untyped-def]
    """P3：`bytes_len` 写的是多少，与真正存下来的字节数差多少。"""

    rows = []
    for row in connection.execute(
        "SELECT material_id, origin, title, bytes_len, length(content) AS blob"
        " FROM materials ORDER BY origin, created_at"
    ):
        rows.append(
            {
                "origin": row["origin"],
                "title": str(row["title"])[:30],
                "bytes_len": int(row["bytes_len"]),
                "blob_bytes": int(row["blob"]),
                "equal": int(row["bytes_len"]) == int(row["blob"]),
                "delta": int(row["bytes_len"]) - int(row["blob"]),
            }
        )
    return {
        "rows": rows,
        "note": "上传/抓网页：bytes_len = 原始字节数 = content blob 长度；"
        "联网：bytes_len = **清洗前**的整页字节数，而 content blob = 清洗并切块后的正文",
    }


async def probe_p4_fetch_key(fetch, connection) -> dict:  # type: ignore[no-untyped-def]
    """P4：先抓 http（会跳 https），再抓 https —— 同一页会不会多出一份？"""

    first = await _fetch_with_retry(fetch, REDIRECT_URL)
    second = await _fetch_with_retry(fetch, FINAL_URL)
    rows = [
        {
            "material_id": row["material_id"],
            "origin": row["origin"],
            "source_ref": row["source_ref"],
            "sha256": row["sha256"][:16],
        }
        for row in connection.execute(
            "SELECT material_id, origin, source_ref, sha256 FROM materials"
            " WHERE project_id = ? ORDER BY created_at",
            (str(PROJECT),),
        )
    ]
    same_ref = [r for r in rows if r["source_ref"] == FINAL_URL]
    return {
        "first_source_ref": first.source_ref,
        "second_source_ref": second.source_ref,
        "materials_total": len(rows),
        "materials_with_same_source_ref": len(same_ref),
        "rows": rows,
        "note": "去重键 = sha256('fetch:' + **原地址**)，来源写**最终地址**"
        " → 原地址与最终地址各抓一次 = 两份材料、同一 URL",
    }


def probe_p5_budget() -> dict:  # type: ignore[no-untyped-def]
    """P5：选块时算的（`web_block_cost`）与渲染时真正写出去的差多少。"""

    from pmstudio.application.dto import MaterialPacket

    def make(count: int, title: str = "预算探针") -> ParsedMaterial:
        chunks = tuple(
            MaterialChunk(
                chunk_id=f"tavily#c{index:02d}",
                ordinal=index,
                text=f"这是第 {index} 段的内容。" * 20,
                heading_path=(),
                anchor=f"网页摘要 · 第 {index + 1} 段",
            )
            for index in range(count)
        )
        return ParsedMaterial(
            origin="web_search",
            title=title,
            source_ref=f"https://example.com/{title}",
            media_type="text/html",
            bytes_len=sum(len(c.text) for c in chunks),
            chunks=chunks,
            parser_id="tavily",
            parser_version="probe",
        )

    rows = []
    for count in (1, 3, 8, 20):
        parsed = make(count)
        packet = MaterialPacket(
            material_id="m-probe",
            project_id=str(PROJECT),
            parsed=parsed,
            created_at=NOW,
        )
        picked, _ = select_chunks([packet], budget=WEB_BUDGET, cost=web_block_cost)
        costed = 0
        seen = set()
        for _, chunk in picked:
            first = "m-probe" not in seen
            seen.add("m-probe")
            costed += web_block_cost(packet, chunk, first_of_material=first)
        rendered = render_web_results([(packet, [c for _, c in picked], "E12")])
        # **渲染器真正写出去的那几段**（不含尾部那句说明）
        body = "\n".join(chunk.text for _, chunk in picked)
        block = f"[E12] {parsed.title}\n{parsed.source_ref}\n{body}"
        rows.append(
            {
                "chunks": count,
                "picked": len(picked),
                "costed": costed,
                "block_len": len(block),
                "delta_cost_vs_block": len(block) - costed,
                "rendered_len": len(rendered.text),
                "rendered_truncated": rendered.truncated,
                "budget": WEB_BUDGET,
                "over_budget": len(block) > WEB_BUDGET,
                "formula": "delta = len(eid) + 段数 - 10（多段结果之间还有换行）",
            }
        )


    # 多条结果：选块按成本算满 2000，渲染按真实长度写 → 可能超出预算 → 末尾那条被丢
    packets = []
    for index in range(5):
        parsed = make(4, title=f"多条-{index}")
        packets.append(
            MaterialPacket(
                material_id=f"m-multi-{index}",
                project_id=str(PROJECT),
                parsed=parsed,
                created_at=NOW,
            )
        )
    picked, _ = select_chunks(packets, budget=WEB_BUDGET, cost=web_block_cost)
    by_id = {packet.material_id: packet for packet in packets}
    costed = 0
    seen = set()
    for material_id, chunk in picked:
        first = material_id not in seen
        seen.add(material_id)
        costed += web_block_cost(by_id[material_id], chunk, first_of_material=first)
    grouped: dict[str, list] = {}
    for material_id, chunk in picked:
        grouped.setdefault(material_id, []).append(chunk)
    entries = [
        (packet, grouped[packet.material_id], f"E{index + 1}")
        for index, packet in enumerate(packets)
        if packet.material_id in grouped
    ]
    rendered = render_web_results(entries)

    # 搜一组参数，让「成本 ≤ 预算 < 实际渲染长度」成立 —— 那就是
    # 「选进来了（编号已发）却渲染不出去」的确证
    overshoot = None
    for results in range(2, 8):
        for chunks_per in range(1, 9):
            for repeats in range(10, 40, 2):
                trial = []
                for index in range(results):
                    parsed = make(chunks_per, title=f"搜-{index}")
                    parsed = ParsedMaterial(
                        origin=parsed.origin,
                        title=parsed.title,
                        source_ref=parsed.source_ref,
                        media_type=parsed.media_type,
                        bytes_len=parsed.bytes_len,
                        chunks=tuple(
                            MaterialChunk(
                                chunk_id=c.chunk_id,
                                ordinal=c.ordinal,
                                text=c.text[:repeats * 10],
                                heading_path=c.heading_path,
                                anchor=c.anchor,
                            )
                            for c in parsed.chunks
                        ),
                        parser_id=parsed.parser_id,
                        parser_version=parsed.parser_version,
                    )
                    trial.append(
                        MaterialPacket(
                            material_id=f"m-s-{index}",
                            project_id=str(PROJECT),
                            parsed=parsed,
                            created_at=NOW,
                        )
                    )
                trial_picked, _ = select_chunks(
                    trial, budget=WEB_BUDGET, cost=web_block_cost
                )
                trial_by_id = {p.material_id: p for p in trial}
                trial_costed = 0
                trial_seen = set()
                for mid, chunk in trial_picked:
                    first = mid not in trial_seen
                    trial_seen.add(mid)
                    trial_costed += web_block_cost(
                        trial_by_id[mid], chunk, first_of_material=first
                    )
                trial_group: dict[str, list] = {}
                for mid, chunk in trial_picked:
                    trial_group.setdefault(mid, []).append(chunk)
                trial_entries = [
                    (p, trial_group[p.material_id], f"E{i + 1}")
                    for i, p in enumerate(trial)
                    if p.material_id in trial_group
                ]
                trial_rendered = render_web_results(trial_entries)
                trial_block_len = sum(
                    len(f"[E{i + 1}] {p.parsed.title}\n{p.parsed.source_ref}\n"
                        + "\n".join(c.text for c in trial_group[p.material_id]))
                    for i, p in enumerate(trial)
                    if p.material_id in trial_group
                )
                if trial_costed <= WEB_BUDGET < trial_block_len:
                    overshoot = {
                        "results": results,
                        "chunks_per_result": chunks_per,
                        "text_repeats": repeats,
                        "costed": trial_costed,
                        "budget": WEB_BUDGET,
                        "block_len": trial_block_len,
                        "rendered_len": len(trial_rendered.text),
                        "rendered_truncated": trial_rendered.truncated,
                        "results_selected": len(trial_entries),
                        "results_rendered": trial_rendered.text.count(
                            "https://example.com/搜-"
                        ),
                    }
                    break
            if overshoot:
                break
        if overshoot:
            break

    return {
        "rows": rows,
        "multi": {
            "results": len(packets),
            "selected_chunks": len(picked),
            "costed": costed,
            "rendered_len": len(rendered.text),
            "budget": WEB_BUDGET,
            "rendered_truncated": rendered.truncated,
            "results_rendered": rendered.text.count("https://example.com/多条-"),
            "results_selected": len(entries),
        },
        "overshoot_case": overshoot,
        "note": "成本函数只算「正文 + 标题 + URL + 编号位(12)」，渲染时还要写"
        "「[E12] 」「\\n」与多段之间的换行 → 多段结果的成本被低估；"
        "低估够多时「选进来的最后一条」渲染不出去（编号已在账本里）",
    }


async def main() -> None:  # type: ignore[no-untyped-def]
    OUT.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()
    token = set_owner_scope(OWNER)

    embedder = FastEmbedEmbedder()
    connection = connect(DB, embedder=embedder)
    store = SqliteMaterialStore(connection, ids=UuidIdGenerator())
    vectors = SqliteVectorIndex(connection, embedder=embedder)
    documents = SqliteDocumentRepository(connection)
    uow = SqliteUnitOfWork(connection)
    parser = LocalMaterialParser()
    clock = FixedClock()
    ids = UuidIdGenerator()
    SqliteProjectFactory(connection, ids=ids).create(
        project_id=PROJECT, title="一致性探针", now=NOW, template=standard_template()
    )

    upload = UploadMaterial(
        documents=documents, materials=store, parser=parser, clock=clock, uow=uow,
        vectors=vectors,
    )
    await upload(project_id=PROJECT, filename="probe.html", media_type="text/html", data=HTML)

    fetch = FetchMaterial(
        documents=documents, materials=store, parser=parser, fetcher=HttpxWebFetcher(),
        index=_NullIndex(), clock=clock, uow=uow, vectors=vectors,
    )
    p4: dict = {}
    try:
        p4 = await probe_p4_fetch_key(fetch, connection)
    except Exception as error:  # noqa: BLE001 —— 网络抖动不该让整份证据作废
        p4 = {"skipped": True, "error": f"{type(error).__name__}: {error}"}

    # 联网材料也放一份进去（用**生产建包函数** `_packet`），好让 P3 覆盖第三个来源
    from pmstudio.infrastructure.tools.tavily import _packet as tavily_packet

    raw_item = {
        "title": "整页探针",
        "url": "https://example.com/web-search-probe",
        "content": "这是 Tavily 按 query 挑的那一段。",
        "raw_content": "\n".join(
            line
            for i in range(30)
            for line in (
                f"## 第 {i} 节",
                "[导航行](https://example.com/nav) ",  # 清洗规则会剔掉这一行
                f"第 {i} 节的正文内容。" * 6,
            )
        ),
    }
    web_packet = tavily_packet(raw_item, query="探针")
    store.put(
        project_id=PROJECT,
        parsed=web_packet,
        sha256=b"web-search-probe",
        content="\n".join(c.text for c in web_packet.chunks).encode("utf-8"),
        now=NOW,
        owner=OWNER,
    )

    report = {
        "P3_bytes_len": probe_p3_bytes_len(connection, store),
        "P4_fetch_dedupe_key": p4,
        "P5_web_budget": probe_p5_budget(),
    }
    (OUT / "consistency.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    connection.close()
    clear_owner_scope(token)

    print("=== P3 bytes_len 口径 ===")
    for row in report["P3_bytes_len"]["rows"]:
        print(
            f"  {row['origin']:11} bytes_len={row['bytes_len']:>8} blob={row['blob_bytes']:>8}"
            f" 相等={row['equal']} 差 {row['delta']:+d}"
        )
    print("\n=== P4 抓网页去重键 ===")
    print(json.dumps(report["P4_fetch_dedupe_key"], ensure_ascii=False, indent=1))
    print("\n=== P5 联网预算记账 ===")
    print(json.dumps(report["P5_web_budget"], ensure_ascii=False, indent=1))
    print(f"\n证据：{OUT / 'consistency.json'}")


class _NullIndex:
    def material_references(self, project_id, *, owner=None):  # type: ignore[no-untyped-def]
        return {}


async def _fetch_with_retry(fetch, url: str, attempts: int = 6):  # type: ignore[no-untyped-def]
    from pmstudio.harness.errors import ToolCallFailed

    last: Exception | None = None
    for index in range(attempts):
        try:
            return await fetch(project_id=PROJECT, url=url)
        except ToolCallFailed as error:
            last = error
            print(f"  （抓取第 {index + 1} 次失败：{error.message}——脚本层重试）")
            await asyncio.sleep(2)
    raise last  # type: ignore[misc]


if __name__ == "__main__":
    asyncio.run(main())
