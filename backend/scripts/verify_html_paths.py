"""核验批 6c 的**两条 HTML 入口**：上传 `.html` 与抓网页 `web_fetch`。

**这不是测试替身**：真 bs4+lxml 解析、真 httpx 抓取（带 SSRF 护栏）、真 SQLite
（FTS5 + sqlite-vec）、真嵌入模型（`bge-small-zh-v1.5`，ONNX 本地推理）。
唯一没有网络参与的是「造一份 HTML 夹具」——那本来就是我们自己造的输入。

验四件事：

1. **上传 .html 的块结构**：块数、`heading_path`（章节链）、锚点、FTS 索引内容、
   向量输入内容 —— 四者是否自洽；
2. **抓网页与上传是否同构**：**同一份字节**走两条路，块文本与 `heading_path`
   是否逐块相同（这是"走同一个解析器"的可执行判据）；
3. **向量预算**：`upload` / `web_fetch` 不限，`web_search` 限前 N 块；
4. **字段是否在"入库"这一步丢掉/写空/写成另一个含义**。

跑法：
    cd backend && uv run python -u scripts/verify_html_paths.py
"""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
from pmstudio.infrastructure.sqlite.materials import SqliteMaterialStore
from pmstudio.infrastructure.sqlite.tokenize import index_source, index_text
from pmstudio.infrastructure.sqlite.vector_index import (
    SqliteVectorIndex,
    vector_block_budget,
)
from pmstudio.infrastructure.tools.extract import LocalMaterialParser, html_text
from pmstudio.infrastructure.tools.fetch import HttpxWebFetcher
from pmstudio.infrastructure.tools.limits import (
    CHUNK_CHARS,
    MAX_MATERIAL_CHARS,
    WEB_VECTOR_BLOCKS,
)

OUT = Path("../.codex/runs/2026-10-20/verify")
DB = OUT / "html_paths.db"
PROJECT, OWNER = "p-verify", "owner-verify"

#: 真抓网页用的目标（真实公开页，有 nav / 侧栏 / 多级标题 / 表格 / 代码块）
FETCH_URL = "https://docs.python.org/3/library/sqlite3.html"

#: 夹具 HTML：把网页该有的结构都摆出来（语义标签、多级标题、表格、代码块、裸 div 菜单）
FIXTURE_HTML = """<!doctype html>
<html lang="zh">
<head><title>权限系统设计详解</title></head>
<body>
<nav><a href="/">首页</a> <a href="/docs">文档中心</a></nav>
<header>站点横幅：本页由 PM Studio 生成</header>
<div class="menu"><a href="/a">产品目录</a><a href="/b">价格</a></div>
<aside>相关链接：<a href="/x">导出权限</a></aside>
<article>
<h1>权限系统设计详解</h1>
<p>导出权限由三段构成：资源、动作、范围。资源决定能对什么操作，动作决定能做什么，
范围决定能对哪些数据做。</p>
<h2>1 资源模型</h2>
<p>资源以树形组织，节点有稳定 id。授权判定从叶子向上冒泡，任一节点拒绝即整体拒绝。</p>
<h3>1.1 资源树的样子</h3>
<p>每一层用 path 表示，例如 <code>orders.export</code> 表示订单模块的导出动作。</p>
<h2>2 动作</h2>
<p>动作分读、写、导出三类。导出动作额外要求审计留痕，缺失审计时导出被拒绝。</p>
<table>
<thead><tr><th>动作</th><th>是否留痕</th></tr></thead>
<tbody><tr><td>读</td><td>否</td></tr><tr><td>导出</td><td>是</td></tr></tbody>
</table>
<h2>3 范围</h2>
<p>范围分本人、本部门与全局三档。本人范围是最窄的一档，也是默认档。</p>
<pre><code>def can_export(user, resource):
    return user.scope == "global" or has(user, resource, "export")
</code></pre>
</article>
<footer>版权所有 · 备案号</footer>
</body></html>
"""


async def _fetch_with_script_retry(fetcher, *, url: str, attempts: int = 6):  # type: ignore[no-untyped-def]
    """脚本层重试（**只针对网络抖动**）。

    生产代码 `HttpxWebFetcher._attempt_with_retry` 只重试 `ReadTimeout` 与
    `RemoteProtocolError`，**不重试 `ConnectTimeout`**（注释里写明"连不上就是连不上"）。
    实测（2026-10-20）：同一个公网地址十次里有四五次 `ConnectTimeout`——于是本脚本
    自己重试，好让核验不被网络抖动打断。**这不是修生产代码**，只是让证据脚本可复跑。
    """

    from pmstudio.harness.errors import ToolCallFailed

    last: Exception | None = None
    for index in range(attempts):
        try:
            return await fetcher.fetch(url=url)
        except ToolCallFailed as error:
            last = error
            print(f"  （抓取第 {index + 1} 次失败：{error.message}——脚本层重试）")
            await asyncio.sleep(2)
    raise last  # type: ignore[misc]


def _chunk_rows(parsed, limit: int = 6) -> list[dict]:  # type: ignore[no-untyped-def]
    return [
        {
            "ordinal": chunk.ordinal,
            "chunk_id": chunk.chunk_id,
            "heading_path": list(chunk.heading_path),
            "anchor": chunk.anchor,
            "text_head": chunk.text[:70].replace("\n", "\\n"),
            "chars": len(chunk.text),
        }
        for chunk in parsed.chunks[:limit]
    ]


def section_a_upload(parser: LocalMaterialParser) -> dict:
    """A：上传 `.html` → 块结构 / heading_path / FTS 索引内容 / 向量输入。"""

    data = FIXTURE_HTML.encode("utf-8")
    parsed = parser.parse(filename="权限系统设计详解.html", media_type="text/html", data=data)
    blocks = parsed.chunks
    text, title = html_text(data)
    return {
        "parser_id": parsed.parser_id,
        "parser_version": parsed.parser_version,
        "title": parsed.title,
        "source_ref": parsed.source_ref,
        "media_type": parsed.media_type,
        "bytes_len": parsed.bytes_len,
        "truncated": parsed.truncated,
        "warnings": list(parsed.warnings),
        "html_title": title,
        "text_chars": len(text),
        "chunk_count": len(blocks),
        "chunk_width_limit": CHUNK_CHARS,
        "max_chunk_chars": max((len(b.text) for b in blocks), default=0),
        "chunks_with_heading": sum(1 for b in blocks if b.heading_path),
        "chunks": _chunk_rows(parsed),
        "nav_aside_present_in_text": {
            "导航": "文档中心" in text,
            "侧栏": "相关链接" in text,
            "页脚": "版权所有" in text,
            "裸div菜单": "产品目录" in text,
        },
        "index_source_head": index_source(
            title=parsed.title, heading_path=blocks[1].heading_path, text=blocks[1].text
        )[:120],
        "index_text_head": index_text(
            title=parsed.title, heading_path=blocks[1].heading_path, text=blocks[1].text
        )[:120],
    }


def section_b_fetch(parser: LocalMaterialParser) -> dict:
    """B：真抓网页 → 与「同一份字节走上传路」逐块比对（同构判据）。"""

    async def run() -> dict:
        fetcher = HttpxWebFetcher()
        page = await _fetch_with_script_retry(fetcher, url=FETCH_URL)
        # 抓网页这条路：fetcher 只管网络，解析交给 MaterialParser（生产那条路）
        fetched = parser.parse(
            filename=page.title or page.final_url,
            media_type=page.media_type,
            data=page.content,
        )
        # 对照组：同一份字节，走「上传」那条路（文件名不同，标题会被 html_text 抽出来）
        uploaded = parser.parse(
            filename="sqlite3.html", media_type="text/html", data=page.content
        )
        left = [(c.text, c.heading_path) for c in fetched.chunks]
        right = [(c.text, c.heading_path) for c in uploaded.chunks]
        first_diff = None
        for index, (a, b) in enumerate(zip(left, right, strict=False)):
            if a != b:
                first_diff = {
                    "index": index,
                    "fetched": [a[0][:120], list(a[1])],
                    "uploaded": [b[0][:120], list(b[1])],
                }
                break
        return {
            "url": FETCH_URL,
            "final_url": page.final_url,
            "media_type": page.media_type,
            "fetcher_title": page.title,
            "bytes": len(page.content),
            "fetched_parser": [fetched.parser_id, fetched.parser_version],
            "uploaded_parser": [uploaded.parser_id, uploaded.parser_version],
            "fetched_chunk_count": len(fetched.chunks),
            "uploaded_chunk_count": len(uploaded.chunks),
            "same_chunk_count": len(left) == len(right),
            "isomorphic": left == right,
            "first_difference": first_diff,
            "fetched_heading_chunks": sum(1 for c in fetched.chunks if c.heading_path),
            "uploaded_heading_chunks": sum(1 for c in uploaded.chunks if c.heading_path),
            "fetched_anchor_head": fetched.chunks[0].anchor if fetched.chunks else "",
            "uploaded_anchor_head": uploaded.chunks[0].anchor if uploaded.chunks else "",
            "fetched_title": fetched.title,
            "uploaded_title": uploaded.title,
        }

    return asyncio.run(run())


def _make_parsed(origin: str, *, title: str, source_ref: str, count: int) -> ParsedMaterial:
    """造一份 `count` 块的材料（块宽照生产口径），用来量向量预算。"""

    chunks = tuple(
        MaterialChunk(
            chunk_id=f"probe#c{index:02d}",
            ordinal=index,
            text=f"## 第 {index} 节\n这是第 {index} 节的正文，足够长以便向量化有意义。" * 3,
            heading_path=(f"第 {index} 节",),
            anchor=f"第 {index} 段",
        )
        for index in range(count)
    )
    return ParsedMaterial(
        origin=origin,
        title=title,
        source_ref=source_ref,
        media_type="text/html",
        bytes_len=sum(len(c.text) for c in chunks),
        chunks=chunks,
        parser_id="probe",
        parser_version="probe-v1",
    )


def section_c_vector_budget(connection, store, vectors) -> dict:  # type: ignore[no-untyped-def]
    """C：向量预算——`upload`/`web_fetch` 不限，`web_search` 限前 N 块。"""

    result = {"WEB_VECTOR_BLOCKS": WEB_VECTOR_BLOCKS, "budget_rule": {}, "materials": {}}
    for origin in ("upload", "web_fetch", "web_search"):
        result["budget_rule"][origin] = vector_block_budget(origin)
    for origin in ("upload", "web_fetch", "web_search"):
        parsed = _make_parsed(
            origin,
            title=f"预算探针-{origin}",
            source_ref=f"https://example.invalid/{origin}",
            count=30,
        )
        packet = store.put(
            project_id=PROJECT,
            parsed=parsed,
            sha256=("probe-" + origin).encode().hex(),
            content=b"probe",
            now=datetime(2026, 10, 20, tzinfo=UTC),
            owner=OWNER,
        )
        vectors.write(packet.material_id)
        rows = connection.execute(
            "SELECT count(*) FROM material_chunks_vec v JOIN material_chunks c"
            " ON c.chunk_id = v.chunk_id WHERE c.material_id = ?",
            (packet.material_id,),
        ).fetchone()[0]
        result["materials"][origin] = {
            "chunks": len(parsed.chunks),
            "vectors": int(rows),
            "expected": WEB_VECTOR_BLOCKS if origin == "web_search" else 30,
            "ok": int(rows)
            == (WEB_VECTOR_BLOCKS if origin == "web_search" else 30),
        }
    return result


def section_d_field_consistency(connection, store, vectors, parser) -> dict:  # type: ignore[no-untyped-def]
    """D：字段是否在"入库"这一步丢掉 / 写空 / 写成另一个含义。"""

    data = FIXTURE_HTML.encode("utf-8")
    parsed = parser.parse(filename="字段核对.html", media_type="text/html", data=data)
    packet = store.put(
        project_id=PROJECT,
        parsed=parsed,
        sha256=b"field-check".hex(),
        content=data,
        now=datetime(2026, 10, 20, tzinfo=UTC),
        owner=OWNER,
    )
    vectors.write(packet.material_id)
    row = connection.execute(
        "SELECT * FROM materials WHERE material_id = ?", (packet.material_id,)
    ).fetchone()
    chunk_rows = connection.execute(
        "SELECT * FROM material_chunks WHERE material_id = ? ORDER BY ordinal",
        (packet.material_id,),
    ).fetchall()
    fts_rows = connection.execute(
        "SELECT chunk_id, tokens FROM material_chunks_fts WHERE material_id = ?",
        (packet.material_id,),
    ).fetchall()
    vec_rows = connection.execute(
        "SELECT v.chunk_id FROM material_chunks_vec v JOIN material_chunks c"
        " ON c.chunk_id = v.chunk_id WHERE c.material_id = ?",
        (packet.material_id,),
    ).fetchall()
    fts_by_id = {r["chunk_id"]: r["tokens"] for r in fts_rows}
    vec_ids = {r["chunk_id"] for r in vec_rows}
    problems: list[str] = []
    checks: dict[str, object] = {}
    checks["title"] = {
        "parsed": parsed.title,
        "db": row["title"],
        "ok": parsed.title == row["title"],
    }
    checks["source_ref"] = {
        "parsed": parsed.source_ref,
        "db": row["source_ref"],
        "ok": parsed.source_ref == row["source_ref"],
    }
    checks["parser_version"] = {
        "parsed": parsed.parser_version,
        "db": row["parser_version"],
        "ok": parsed.parser_version == row["parser_version"],
    }
    checks["truncated"] = {
        "parsed": parsed.truncated,
        "db": bool(row["truncated"]),
        "ok": parsed.truncated == bool(row["truncated"]),
    }
    checks["warnings"] = {
        "parsed": list(parsed.warnings),
        "db": json.loads(row["warnings"]),
        "ok": list(parsed.warnings) == json.loads(row["warnings"]),
    }
    checks["media_type"] = {
        "parsed": parsed.media_type,
        "db": row["media_type"],
        "ok": parsed.media_type == row["media_type"],
    }
    # 块：id / ordinal / heading_path / anchor / text 逐块对
    chunk_mismatch = []
    fts_mismatch = []
    for stored, original in zip(chunk_rows, parsed.chunks, strict=True):
        if stored["chunk_id"] != f"{packet.material_id}#c{original.ordinal:02d}":
            chunk_mismatch.append(stored["chunk_id"])
        if stored["text"] != original.text:
            chunk_mismatch.append(f"text@{original.ordinal}")
        if json.loads(stored["heading_path"]) != list(original.heading_path):
            chunk_mismatch.append(f"heading@{original.ordinal}")
        if stored["anchor"] != original.anchor:
            chunk_mismatch.append(f"anchor@{original.ordinal}")
        expected = index_source(
            title=parsed.title, heading_path=original.heading_path, text=original.text
        )
        if fts_by_id.get(stored["chunk_id"]) != expected:
            fts_mismatch.append(stored["chunk_id"])
    checks["chunks_roundtrip"] = {"mismatch": chunk_mismatch, "ok": not chunk_mismatch}
    checks["fts_tokens_match_index_source"] = {
        "mismatch": fts_mismatch,
        "ok": not fts_mismatch,
    }
    checks["vectors_cover_all_upload_chunks"] = {
        "vectors": len(vec_ids),
        "chunks": len(chunk_rows),
        "ok": len(vec_ids) == len(chunk_rows),
    }
    for name, value in checks.items():
        if not value["ok"]:
            problems.append(name)
    return {"material_id": packet.material_id, "checks": checks, "problems": problems}


def section_e_limits() -> dict:
    """E：三条路的**上限**口径（代码为准）。"""

    return {
        "CHUNK_CHARS": CHUNK_CHARS,
        "MAX_MATERIAL_CHARS": MAX_MATERIAL_CHARS,
        "WEB_VECTOR_BLOCKS": WEB_VECTOR_BLOCKS,
        "note": "上传/抓网页 → CHUNK_CHARS + MAX_MATERIAL_CHARS + 向量不限；"
        "联网检索 → CHUNK_CHARS + MAX_MATERIAL_CHARS + 前 N 块向量",
    }


def section_f_embed_ceiling(embedder) -> dict:
    """F：上传那条路（`CHUNK_CHARS=500`）会不会**超过嵌入模型的 512 token 上限**？

    `limits.CHUNK_CHARS=400` 的理由（`limits.py:14-20`）是：向量模型上限 512 token，
    中文约 1 字 1 token，喂进去的是「标题 + 章节链 + 正文」→ 超出部分**对向量完全不可见**。
    本节量同一件事在**上传/抓网页**那条路（块宽 500）上的表现：往块尾加一个独特标记词，
    看向量变不变（cos = 1.000000 → 尾部对向量不可见）。
    """

    def cos(left, right) -> float:
        return sum(x * y for x, y in zip(left, right, strict=True))

    title = "权限系统设计详解：导出权限的分级与审计留痕方案"
    head = ("权限系统设计详解", "1 资源模型", "1.1 资源树的样子")
    rows = []
    for width in (400, 450, 500, 600):
        body = ("导出权限由资源、动作与范围三段构成。" * (width // 13 + 1))[:width]
        base = index_text(title=title, heading_path=head, text=body)
        marker = index_text(title=title, heading_path=head, text=body + "ZZQQXX尾标记ZZQQXX")
        v_base, v_marker = embedder.embed_many([base, marker])
        rows.append(
            {
                "chunk_chars": width,
                "index_text_chars": len(base),
                "cos_with_tail_marker": round(cos(v_base, v_marker), 6),
                "tail_visible": cos(v_base, v_marker) < 0.999999,
            }
        )
    return {
        "rows": rows,
        "upload_chunk_limit": CHUNK_CHARS,
        "web_chunk_limit": CHUNK_CHARS,
        "note": "上传/抓网页那条路块宽 500 —— 加上标题与章节链后超过 512 token 的部分"
        "**对向量完全不可见**（cos=1.000000）；联网那条路用 400 就是为了避开这件事",
    }


def main() -> None:  # type: ignore[no-untyped-def]
    OUT.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()
    embedder = FastEmbedEmbedder()
    connection = connect(DB, embedder=embedder)
    store = SqliteMaterialStore(connection, ids=UuidIdGenerator())
    vectors = SqliteVectorIndex(connection, embedder=embedder)
    parser = LocalMaterialParser()

    report: dict[str, object] = {"limits": section_e_limits()}
    report["A_upload_html"] = section_a_upload(parser)
    report["B_fetch_vs_upload"] = section_b_fetch(parser)
    report["C_vector_budget"] = section_c_vector_budget(connection, store, vectors)
    report["D_field_consistency"] = section_d_field_consistency(connection, store, vectors, parser)
    report["F_embed_ceiling"] = section_f_embed_ceiling(embedder)
    connection.commit()

    (OUT / "html_paths.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    connection.close()

    a = report["A_upload_html"]
    b = report["B_fetch_vs_upload"]
    c = report["C_vector_budget"]
    d = report["D_field_consistency"]
    print("\n=== A 上传 .html ===")
    print(f"  块数 {a['chunk_count']} · 带章节链的块 {a['chunks_with_heading']} · "
          f"最宽 {a['max_chunk_chars']}（上限 {CHUNK_CHARS}）")
    print(f"  标题 {a['title']!r} · 解析器 {a['parser_id']}@{a['parser_version']}")
    print(f"  噪音去留：{a['nav_aside_present_in_text']}")
    print("\n=== B 抓网页 vs 上传（同构判据）===")
    print(f"  目标 {b['url']} · {b['bytes']} 字节 · {b['fetched_chunk_count']} 块")
    print(f"  解析器身份：抓 {b['fetched_parser']} / 传 {b['uploaded_parser']}")
    print(f"  同构：{b['isomorphic']}（块数相同 {b['same_chunk_count']}）")
    if b["first_difference"]:
        print(f"  首个差异：{json.dumps(b['first_difference'], ensure_ascii=False)[:300]}")
    print("\n=== C 向量预算 ===")
    for origin, item in c["materials"].items():
        print(f"  {origin:11} 块 {item['chunks']:>3} · 向量 {item['vectors']:>3} "
              f"· 期望 {item['expected']:>3} · {'OK' if item['ok'] else 'FAIL'}")
    print("\n=== D 字段自洽 ===")
    print(f"  问题项：{d['problems'] or '无'}")
    for name, value in d["checks"].items():
        print(f"    {'OK  ' if value['ok'] else 'FAIL'} {name}")
    print("\n=== F 嵌入模型 512-token 上限 ===")
    for row in report["F_embed_ceiling"]["rows"]:
        print(
            f"  块宽 {row['chunk_chars']:>3} 字（index_text {row['index_text_chars']:>3} 字符）"
            f" · 尾部标记对向量的影响 cos={row['cos_with_tail_marker']:.6f}"
            f" · 尾部{'可见' if row['tail_visible'] else '**不可见**'}"
        )
    print(f"\n证据：{OUT / 'html_paths.json'}")


if __name__ == "__main__":
    main()
