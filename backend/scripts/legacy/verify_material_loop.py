"""核验批 6c 的**数据闭环**：从"抓到的字节"到"用户看到的引用"，字段处处一致。

**这不是测试替身**（除了两处明确标注的）：真 SQLite（FTS5 + sqlite-vec）、真嵌入模型
（`bge-small-zh-v1.5` ONNX）、真 `LocalMaterialParser` / `HttpxWebFetcher`（真 HTTP）/
`TavilySearchGateway`（真 Tavily）、真 `SqliteMaterialStore`、真 `SqliteVectorIndex`、
真 `UploadMaterial` / `FetchMaterial` / `SubmitOpening` 用例、真账本 `EvidenceLedger`、
真渲染器 `external_material_text`。

**明确标注的替身**（只在与材料链路无关的地方用，且**不碰**被核验的那段逻辑）：
- 时钟：固定时刻；
- 事件发布：空实现（事件只做通知，不参与业务）；
- 分析者（`StartRound.analyze`）：固定报文（**模型被替身**，材料链路本身全是真的）。

核验清单（每一项都对着"块 id / 锚点 / 标题 / 来源 URL"过一遍）：

1. **入库 → 索引**：块行、FTS 索引串、向量行，三者与解析器产出逐字一致；
2. **选块 → 发号**：轮次里的 `MaterialRef` 指向真实存在的块；
3. **发号 → 账本**：账本条目的正文 = 提示词里渲染的那段（逐字）；
4. **账本 → 提示词**：渲染段里的编号 / 标题 / 来源 URL 与账本、库行一致；
5. **三条入口的"外部指称"**：上传是 `attachment:<文件>#cNN`，联网是 URL，抓网页是 URL。

外加两个**具体缺陷探针**（都用真代码路径，只把"联网网关"换成固定报文）：
- **P1 无链接结果的入库键冲突**：两条没有 URL 的检索结果会不会撞成一份；
- **P2 老档位材料的就地升级 / 变体**：被编号引用过的那份会不会被改写。

跑法（需要 backend/.env 里的 Tavily key）：
    cd backend && uv run python -u scripts/verify_material_loop.py
"""

import asyncio
import hashlib
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")

from pmstudio.application.agents.analyzer import Analysis
from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.application.use_cases import (
    FetchMaterial,
    StartRound,
    SubmitOpening,
    UploadMaterial,
    external_material_text,
)
from pmstudio.bootstrap.container import SEATS_DIR, UuidIdGenerator
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.round.claim import Claim
from pmstudio.domain.round.round import Round, SeatNomination
from pmstudio.domain.shared.ids import ProjectId, RoundId, UserId
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.template import standard_template
from pmstudio.harness.tools.registry import DEFINITIONS_DIR, TomlToolCatalog
from pmstudio.infrastructure.seats.catalog import TomlSeatCatalog
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
from pmstudio.infrastructure.sqlite.materials import SqliteMaterialStore
from pmstudio.infrastructure.sqlite.owner_scope import set_owner_scope
from pmstudio.infrastructure.sqlite.project_factory import SqliteProjectFactory
from pmstudio.infrastructure.sqlite.repositories import (
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteRoundRepository,
    SqliteWorkspaceIndex,
)
from pmstudio.infrastructure.sqlite.tokenize import index_source
from pmstudio.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
from pmstudio.infrastructure.tools.extract import LocalMaterialParser
from pmstudio.infrastructure.tools.fetch import HttpxWebFetcher
from pmstudio.infrastructure.tools.tavily import PARSER_VERSION, TavilySearchGateway

OUT = Path("../.codex/runs/2026-10-20/verify")
DB = OUT / "material_loop.db"
PROJECT = ProjectId("p-loop")
OWNER = UserId("owner-loop")
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
FETCH_URL = "https://docs.python.org/3/library/sqlite3.html"

FIXTURE_HTML = """<!doctype html>
<html lang="zh"><head><title>导出权限说明</title></head>
<body><nav><a href="/">首页</a></nav>
<article>
<h1>导出权限说明</h1>
<p>导出权限分三级：普通成员、部门负责人、系统管理员。系统管理员可以导出全量数据。</p>
<h2>审计</h2>
<p>每次导出都会写一条审计日志，日志保留 180 天，不可修改。</p>
</article></body></html>
"""


class FixedClock:
    """固定时钟（**替身**：与材料链路无关）。"""

    def now(self) -> datetime:
        return NOW


class SilentPublisher:
    """空事件发布（**替身**：事件只做通知，不参与业务判定）。"""

    def publish(self, event) -> None:  # noqa: ANN001
        return None


class StubAnalyzer:
    """固定报文的分析者（**替身**：只替模型，不碰材料链路）。"""

    def __init__(self, claim: Claim, seat_id: str) -> None:
        self._claim = claim
        self._seat_id = seat_id

    async def analyze(self, **kwargs) -> Analysis:  # noqa: ANN003
        from pydantic import BaseModel

        from pmstudio.harness.model.envelope import Envelope

        class _Stub(BaseModel):
            ok: bool = True

        return Analysis(
            claims=(self._claim,),
            seats=(SeatNomination(seat_id=self._seat_id, why="固定报文"),),
            envelope=Envelope(
                packet_id="stub-1",
                run_id="stub-1",
                kind="claims",
                produced_by="stub",
                basis="固定报文",
                payload={},
                packet=_Stub(),
            ),
            basis="固定报文",
        )


class FixedWebSearch:
    """固定报文的联网网关（**替身**：只用在两个缺陷探针里）。"""

    def __init__(self, packets: list[ParsedMaterial]) -> None:
        self._packets = packets
        self.calls: list[tuple[str, int]] = []

    async def search(self, *, query: str, limit: int) -> tuple[ParsedMaterial, ...]:
        self.calls.append((query, limit))
        return tuple(self._packets)


def _packet(origin: str, *, title: str, source_ref: str, texts: list[str]) -> ParsedMaterial:
    chunks = tuple(
        MaterialChunk(
            chunk_id=f"probe#c{index:02d}",
            ordinal=index,
            text=text,
            heading_path=(),
            anchor=f"第 {index + 1} 行",
        )
        for index, text in enumerate(texts)
    )
    return ParsedMaterial(
        origin=origin,
        title=title,
        source_ref=source_ref,
        media_type="text/html",
        bytes_len=sum(len(c.text) for c in chunks),
        chunks=chunks,
        parser_id="probe",
        parser_version=PARSER_VERSION if origin == "web_search" else "probe-v1",
        warnings=("来自检索：探针",) if origin == "web_search" else (),
    )


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
    rounds = SqliteRoundRepository(connection)
    evidences = SqliteEvidenceRepository(connection)
    index = SqliteWorkspaceIndex(connection)
    uow = SqliteUnitOfWork(connection)
    parser = LocalMaterialParser()
    ids = UuidIdGenerator()
    clock = FixedClock()
    events = SilentPublisher()
    catalog = TomlSeatCatalog(SEATS_DIR)
    tools = TomlToolCatalog(DEFINITIONS_DIR)
    seat_id = catalog.all()[0].seat_id

    SqliteProjectFactory(connection, ids=ids).create(
        project_id=PROJECT, title="闭环核验", now=NOW, template=standard_template()
    )

    report: dict[str, object] = {"project": str(PROJECT)}

    # ── 1. 上传 .html（真用例：UploadMaterial）──────────────────────────────
    upload = UploadMaterial(
        documents=documents, materials=store, parser=parser, clock=clock, uow=uow,
        vectors=vectors,
    )
    uploaded = await upload(
        project_id=PROJECT,
        filename="导出权限说明.html",
        media_type="text/html",
        data=FIXTURE_HTML.encode("utf-8"),
    )
    print(f"上传：{uploaded.title} · {uploaded.chunk_count} 块 · stale={uploaded.stale}")

    # ── 2. 抓网页（真用例：FetchMaterial + 真 httpx）─────────────────────────
    fetch = FetchMaterial(
        documents=documents, materials=store, parser=parser, fetcher=HttpxWebFetcher(),
        index=index, clock=clock, uow=uow, vectors=vectors,
    )
    fetched = await _fetch_with_script_retry(fetch, project_id=PROJECT, url=FETCH_URL)
    print(f"抓网页：{fetched.title[:30]} · {fetched.chunk_count} 块 · stale={fetched.stale}")

    # ── 3. 联网检索（真用例：SubmitOpening + 真 Tavily）──────────────────────
    start_round = StartRound(
        documents=documents, rounds=rounds, round_writer=rounds, evidence=evidences,
        evidence_writer=evidences, uow=uow,
        analyzer=StubAnalyzer(
            Claim(
                claim_id="c1",
                text="导出权限要分三级",
                label=ClaimLabel.NEED,
                target=Target.body("s3"),
                raw_span="导出权限要分三级",
            ),
            seat_id,
        ),
        assigner=_real_assigner(),
        checker=_real_checker(),
        clock=clock, ids=ids, events=events, materials=store,
        explorations=_NullExplorations(), discussions=_NullDiscussions(),
    )
    round_id = await start_round.create(
        project_id=PROJECT, user_input="导出权限要分三级", material_ids=[uploaded.material_id]
    )
    await start_round.analyze(round_id=round_id)
    gateway = TavilySearchGateway(api_key=_api_key())
    submit = SubmitOpening(
        rounds=rounds, round_writer=rounds, catalog=catalog, tools=tools,
        available_tool_keys=frozenset({"tavily_api_key"}), web_search=gateway,
        evidence=evidences, evidence_writer=evidences, materials=store, index=index,
        uow=uow, clock=clock, events=events, vectors=vectors,
    )
    await submit(
        round_id=round_id,
        seats=(seat_id,),
        web_queries=("导出权限 分级 审计留痕",),
    )
    round_ = rounds.get(round_id)
    print(f"联网检索：轮次 {round_id} 引用 {len(round_.materials)} 个块")

    # ── 4. 闭环核对 ────────────────────────────────────────────────────────
    ledger = evidences.get(PROJECT)
    prompt = external_material_text(round_, store=store)
    checks: list[dict] = []
    problems: list[str] = []

    def check(name: str, ok: bool, detail: object) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            problems.append(name)

    by_material: dict[str, list] = {}
    for ref in round_.materials:
        by_material.setdefault(ref.material_id, []).append(ref)

    for material_id, refs in by_material.items():
        packet = store.get(material_id)
        if packet is None:
            check(f"材料还在[{material_id}]", False, {"material_id": material_id})
            continue
        origin = packet.parsed.origin
        picked_ids = {r.chunk_id for r in refs}
        picked = [c for c in packet.parsed.chunks if c.chunk_id in picked_ids]
        # 联网结果：**一条结果一个编号**（多段共享它）；上传/抓网页：**逐块一个编号**
        expected_eids = 1 if origin == "web_search" else len(picked)
        check(
            f"编号个数（{origin}）",
            len({str(r.eid) for r in refs}) == expected_eids,
            {"refs": len(refs), "expected_eids": expected_eids, "origin": origin},
        )
        for ref in refs:
            chunk_row = connection.execute(
                "SELECT * FROM material_chunks WHERE chunk_id = ?", (ref.chunk_id,)
            ).fetchone()
            fts_row = connection.execute(
                "SELECT tokens FROM material_chunks_fts WHERE chunk_id = ?", (ref.chunk_id,)
            ).fetchone()
            vec_row = connection.execute(
                "SELECT chunk_id FROM material_chunks_vec WHERE chunk_id = ?",
                (ref.chunk_id,),
            ).fetchone()
            evidence = ledger.get(ref.eid)
            original = next(
                (c for c in packet.parsed.chunks if c.chunk_id == ref.chunk_id), None
            )
            check(
                f"块存在[{ref.chunk_id}]",
                chunk_row is not None,
                {"material_id": material_id, "eid": str(ref.eid)},
            )
            if chunk_row is None or original is None:
                continue
            check(
                "块正文逐字一致",
                chunk_row["text"] == original.text,
                {"len": len(original.text)},
            )
            check(
                "锚点一致",
                chunk_row["anchor"] == original.anchor,
                {"db": chunk_row["anchor"], "packet": original.anchor},
            )
            check(
                "章节链一致",
                json.loads(chunk_row["heading_path"]) == list(original.heading_path),
                {
                    "db": json.loads(chunk_row["heading_path"]),
                    "packet": list(original.heading_path),
                },
            )
            expected_tokens = index_source(
                title=packet.parsed.title,
                heading_path=original.heading_path,
                text=original.text,
            )
            check(
                "FTS 索引串 = index_source(标题+章节链+正文)",
                fts_row is not None and fts_row["tokens"] == expected_tokens,
                {"has_row": fts_row is not None},
            )
            check(
                "向量行存在",
                vec_row is not None,
                {"has_row": vec_row is not None},
            )
            check(
                "账本 kind = external",
                evidence.kind is EvidenceKind.EXTERNAL,
                {"kind": str(evidence.kind)},
            )
            check(
                "账本外部指称 = 材料出处",
                _expected_external_ref(packet, original) == evidence.source.external_ref,
                {
                    "expected": _expected_external_ref(packet, original),
                    "actual": evidence.source.external_ref,
                    "origin": origin,
                },
            )
            # 账本正文 = 提示词里渲染的那段（联网是**多段拼接**，上传是**单块**）
            expected_text = (
                "\n".join(c.text for c in picked)
                if origin == "web_search"
                else original.text
            )
            check(
                "账本正文 = 提示词渲染的那段",
                evidence.text == expected_text,
                {
                    "ledger_len": len(evidence.text),
                    "expected_len": len(expected_text),
                    "origin": origin,
                },
            )
            check(
                "提示词含该编号与该块正文",
                f"[{ref.eid}]" in prompt and original.text in prompt,
                {"eid": str(ref.eid)},
            )
        # 标题与来源：上传材料的 `attachment:<文件>` **故意不进提示词**（与标题重复）
        check(
            "提示词含材料标题",
            packet.parsed.title in prompt,
            {"title": packet.parsed.title, "origin": origin},
        )
        if origin != "upload":
            check(
                "提示词含来源 URL",
                packet.parsed.source_ref in prompt,
                {"source_ref": packet.parsed.source_ref, "origin": origin},
            )

    report["closure_checks"] = checks
    report["closure_problems"] = problems
    report["prompt_head"] = prompt[:1200]
    report["ledger_entries"] = [
        {
            "eid": str(e.eid),
            "kind": str(e.kind),
            "external_ref": e.source.external_ref,
            "text_len": len(e.text),
        }
        for e in ledger.evidences()
    ]
    report["round_materials"] = [
        {
            "material_id": r.material_id,
            "chunk_id": r.chunk_id,
            "eid": str(r.eid),
            "origin": (store.get(r.material_id).parsed.origin
                       if store.get(r.material_id) else "?"),
        }
        for r in round_.materials
    ]

    # ── 5. 缺陷探针 P1：无链接结果的入库键冲突 ─────────────────────────────
    report["probe_P1_no_url_collision"] = await _probe_p1(
        rounds=rounds, catalog=catalog, tools=tools, evidences=evidences, store=store,
        index=index, uow=uow, clock=clock, events=events, vectors=vectors, seat_id=seat_id,
    )

    # ── 6. 缺陷探针 P2：老档位材料的就地升级 / 变体 ─────────────────────────
    report["probe_P2_stale_upgrade"] = await _probe_p2(
        rounds=rounds, catalog=catalog, tools=tools, evidences=evidences,
        store=store, index=index, uow=uow, clock=clock, events=events, vectors=vectors,
        seat_id=seat_id, project_id=PROJECT,
    )

    (OUT / "material_loop.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    connection.close()
    from pmstudio.infrastructure.sqlite.owner_scope import clear_owner_scope

    clear_owner_scope(token)

    print("\n=== 闭环核对 ===")
    for item in checks:
        print(f"  {'OK  ' if item['ok'] else 'FAIL'} {item['name']}"
              + ("" if item["ok"] else f"  {json.dumps(item['detail'], ensure_ascii=False)}"))
    print(f"  问题项：{problems or '无'}")
    print("\n=== 提示词（材料段，节选）===")
    print(prompt[:900])
    print("\n=== P1 无链接结果键冲突 ===")
    print(json.dumps(report["probe_P1_no_url_collision"], ensure_ascii=False, indent=1))
    print("\n=== P2 老档位升级 ===")
    print(json.dumps(report["probe_P2_stale_upgrade"], ensure_ascii=False, indent=1))
    print(f"\n证据：{OUT / 'material_loop.json'}")


def _expected_external_ref(packet, chunk) -> str:  # type: ignore[no-untyped-def]
    source_ref = packet.parsed.source_ref
    if source_ref.startswith("http"):
        return source_ref
    return f"{source_ref}#c{chunk.ordinal:02d}"


async def _fetch_with_script_retry(fetch, *, project_id, url: str, attempts: int = 3):  # type: ignore[no-untyped-def]
    """脚本层重试（**只针对网络抖动**）。

    生产代码 `HttpxWebFetcher._attempt_with_retry` 只重试 `ReadTimeout` 与
    `RemoteProtocolError`，**不重试 `ConnectTimeout`**（注释里写明"连不上就是连不上"）。
    实测（2026-10-20）：同一个公网地址第一次 `ConnectTimeout`、第二次就成功——
    于是本脚本自己重试，好让核验不被网络抖动打断。**这不是修生产代码**，
    只是让证据脚本可复跑。
    """

    from pmstudio.harness.errors import ToolCallFailed

    last: Exception | None = None
    for index in range(attempts):
        try:
            return await fetch(project_id=project_id, url=url)
        except ToolCallFailed as error:
            last = error
            print(f"  （抓取第 {index + 1} 次失败：{error.message}——脚本层重试）")
    raise last  # type: ignore[misc]


def _real_assigner():  # type: ignore[no-untyped-def]
    from pmstudio.domain.services.assigner import EvidenceAssigner

    return EvidenceAssigner()


def _real_checker():  # type: ignore[no-untyped-def]
    from pmstudio.domain.authoring.readiness import ReadinessChecker

    return ReadinessChecker()


class _NullExplorations:
    def get(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise LookupError("本脚本不用探索")


class _NullDiscussions:
    def get(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise LookupError("本脚本不用讨论")


def evidences_safe(evidences):  # type: ignore[no-untyped-def]
    return evidences


def _api_key() -> str:
    for line in Path(".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.strip().split("=", 1)
            if key == "PMSTUDIO_TAVILY_API_KEY":
                return value
    raise SystemExit("backend/.env 里没有 PMSTUDIO_TAVILY_API_KEY")


async def _probe_p1(**kwargs) -> dict:  # type: ignore[no-untyped-def]
    """P1：两条**没有 URL** 的检索结果，入库键会不会撞成一份。"""

    rounds = kwargs["rounds"]
    evidences = kwargs["evidences"]
    store = kwargs["store"]
    index = kwargs["index"]
    uow = kwargs["uow"]
    clock = kwargs["clock"]
    events = kwargs["events"]
    vectors = kwargs["vectors"]
    catalog = kwargs["catalog"]
    tools = kwargs["tools"]
    seat_id = kwargs["seat_id"]

    packets = [
        _packet("web_search", title="无链接结果甲", source_ref="（没有链接）",
                texts=["甲这条结果的内容。"]),
        _packet("web_search", title="无链接结果乙", source_ref="（没有链接）",
                texts=["乙这条结果的内容，完全不同。"]),
    ]
    fake = FixedWebSearch(packets)
    submit = SubmitOpening(
        rounds=rounds, round_writer=rounds, catalog=catalog, tools=tools,
        available_tool_keys=frozenset({"tavily_api_key"}), web_search=fake,
        evidence=evidences, evidence_writer=evidences, materials=store, index=index,
        uow=uow, clock=clock, events=events, vectors=vectors,
    )
    round_id = await _make_round(rounds, evidences, seat_id, project=PROJECT)
    await submit(round_id=round_id, seats=(seat_id,), web_queries=("无链接探针",))
    stored = store.list_for_project(PROJECT, current=frozenset(), owner=OWNER)
    no_url = [item for item in stored if item.source_ref == "（没有链接）"]
    texts = []
    for item in no_url:
        packet = store.get(item.material_id)
        texts.append("\n".join(c.text for c in packet.parsed.chunks))
    return {
        "results_returned": len(packets),
        "materials_stored_with_no_url": len(no_url),
        "stored_texts": texts,
        "collision": len(no_url) < len(packets),
        "note": "两条结果共用 source_ref「（没有链接）」→ 去重键 sha256('web:（没有链接）') 相同",
    }


async def _probe_p2(**kwargs) -> dict:  # type: ignore[no-untyped-def]
    """P2：老档位（`tavily-rest-v1`）的联网材料，再搜到同一 URL 时会怎样。"""

    rounds = kwargs["rounds"]
    evidences = kwargs["evidences"]
    store = kwargs["store"]
    index = kwargs["index"]
    uow = kwargs["uow"]
    clock = kwargs["clock"]
    events = kwargs["events"]
    vectors = kwargs["vectors"]
    catalog = kwargs["catalog"]
    tools = kwargs["tools"]
    seat_id = kwargs["seat_id"]
    project_id = kwargs["project_id"]

    old = _packet(
        "web_search", title="老档位页面", source_ref="https://example.com/old",
        texts=["这是老档位存的片段内容。"],
    )
    old = replace(old, parser_version="tavily-rest-v1")
    old_sha = hashlib.sha256(b"web:https://example.com/old").hexdigest()
    old_packet = store.put(
        project_id=project_id, parsed=old, sha256=old_sha,
        content=b"old", now=NOW, owner=OWNER,
    )
    before = store.get(old_packet.material_id)
    fresh = _packet(
        "web_search", title="新档位页面", source_ref="https://example.com/old",
        texts=["这是新档位存的整页内容。"],
    )
    fake = FixedWebSearch([fresh])
    submit = SubmitOpening(
        rounds=rounds, round_writer=rounds, catalog=catalog, tools=tools,
        available_tool_keys=frozenset({"tavily_api_key"}), web_search=fake,
        evidence=evidences, evidence_writer=evidences, materials=store, index=index,
        uow=uow, clock=clock, events=events, vectors=vectors,
    )
    round_id = await _make_round(rounds, evidences, seat_id, project=project_id)
    await submit(round_id=round_id, seats=(seat_id,), web_queries=("老档位探针",))
    after = store.get(old_packet.material_id)
    all_materials = store.list_for_project(project_id, current=frozenset(), owner=OWNER)
    same_url = [
        item.material_id
        for item in all_materials
        if item.source_ref == "https://example.com/old"
    ]
    return {
        "material_id": old_packet.material_id,
        "before_version": before.parsed.parser_version,
        "after_version": after.parsed.parser_version,
        "before_text": before.parsed.chunks[0].text,
        "after_text": after.parsed.chunks[0].text,
        "in_place_upgraded": after.parsed.parser_version == PARSER_VERSION,
        "materials_with_same_url": len(same_url),
        "materials_total": len(all_materials),
        "note": "未被编号引用 → 就地刷新（material_id 不变）；被编号引用 → 另出一份变体",
    }


async def _make_round(rounds, evidences, seat_id: str, *, project: ProjectId) -> RoundId:  # type: ignore[no-untyped-def]
    """造一个「已复述、等开场卡」的轮次（真领域对象 + 真账本）。"""

    round_id = RoundId("r-probe")
    round_ = Round.start(
        round_id=round_id, project_id=project, user_input="探针输入", now=NOW
    )
    ledger = evidences.get(project)
    eid = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="探针输入",
        source=EvidenceSource(round_id=round_id, raw_span="探针输入"),
        now=NOW,
    )
    round_.attach_claims(
        (
            Claim(
                claim_id="c1",
                text="探针输入",
                label=ClaimLabel.NEED,
                target=Target.body("s3"),
                raw_span="探针输入",
                eid=eid,
            ),
        )
    )
    rounds.save(round_)
    evidences.save(ledger)
    return round_id


if __name__ == "__main__":
    asyncio.run(main())
