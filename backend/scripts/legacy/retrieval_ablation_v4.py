"""**库级消融 v4**：编排层该不该从**全库**检索材料进上下文
（见 `.trae/specs/retrieval/experiment-v4.md`）。

## 要回答的问题

让编排层从全库检索材料进上下文（方案 `plan-context-engineering.md`），
相比"用户手勾"，在**答案正确性**上是好是坏？

| 臂 | 上下文里放什么 | 模拟的是 |
|---|---|---|
| A 无材料 | 空 | 下界 |
| B1 手勾·对 | 只带 **gold 那份材料**，走 `select_chunks` | 今天的最好情况 |
| B2 手勾·错 | 带 **3 份随机材料**（不含 gold），走 `select_chunks` | 今天的常见情况 |
| C 库级检索 | `SqliteHybridRetriever.search` 从 40 份里检索 → 命中材料 → `select_chunks`
  填满预算 | **本方案** |
| D oracle | 只带 **gold 块** | 上界 |

## 判据（**预先注册**，跑完只对照）

- H1：C 明显优于 A（库级检索确实有用）；
- H2：**C ≥ B1 − ε**（ε=0.05）——库级检索不输给"用户恰好勾对"（**重构的安全条件**）；
- H3：C 明显优于 B2（库级检索优于"用户猜错"）——**重构的收益**；
- H4：C 与 D 的差距 + 五臂 tokens/延迟（成本归一化）。

## 方法

- **库**：`.codex/runs/2026-10-23/rerank_llm_v2/C raw_content-净.json` 的 40 份真文
  → 真 `materials`/`material_chunks` + FTS5 + 向量索引
  （`SqliteMaterialStore` + `SqliteVectorIndex`）；
- **问题与 gold**：**直接复用 v3 的 120 个 (材料, 问题) 对 + gold 块标注**（不重做标注）；
- **复用**：`chunk_text` / `select_chunks` / `render_materials` / `LiteLLMGateway`
  （不重写渲染与指标）；
- **裁判**：与 v3 同一套（判据跑前写死、2 名裁判取均值、只看"要点有没有被明确陈述"）。

## 环境降级（与 v2/v3 同）

本沙箱 stdlib `sqlite3` 缺 `enable_load_extension` → 换 `pysqlite3-binary`，**不改 `src/`**。
`C` 实际走的检索路（FTS5+向量 / 纯 FTS5）**照实记进结果**。

跑法：

```bash
cd backend && uv run python -u scripts/retrieval_ablation_v4.py [phase ...] [--limit N] [--rebuild]
```
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import resource
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")

import sqlite3 as _sqlite3

if not hasattr(_sqlite3.Connection, "enable_load_extension"):
    import pysqlite3 as _pysqlite3

    sys.modules["sqlite3"] = _pysqlite3

from pmstudio.application.context import (  # noqa: E402
    MATERIAL_BUDGET,
    NO_MATERIAL_TEXT,
    render_materials,
    select_chunks,
)
from pmstudio.application.dto import (  # noqa: E402
    SEARCH_LIMIT,
    MaterialChunk,
    MaterialPacket,
    ParsedMaterial,
)
from pmstudio.bootstrap.container import UuidIdGenerator  # noqa: E402
from pmstudio.domain.shared.ids import (  # noqa: E402
    EvidenceId,
    ProjectId,
    UserId,
    evidence_id,
)
from pmstudio.harness.model.contracts import ModelMessage, ModelRequest  # noqa: E402
from pmstudio.harness.model.providers.litellm import LiteLLMGateway  # noqa: E402
from pmstudio.harness.model.resilience import BulkheadConfig  # noqa: E402
from pmstudio.infrastructure.sqlite.database import connect  # noqa: E402
from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder  # noqa: E402
from pmstudio.infrastructure.sqlite.materials import (  # noqa: E402
    SqliteHybridRetriever,
    SqliteMaterialStore,
)
from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex  # noqa: E402
from pmstudio.infrastructure.tools.extract import chunk_text  # noqa: E402
from pmstudio.infrastructure.tools.webclean import normalize_web_text  # noqa: E402

CORPUS = Path("../.codex/runs/2026-10-23/rerank_llm_v2")
V3 = CORPUS / "ablation_v3"
OUT = CORPUS / "ablation_v4"
DB_PATH = OUT / "ablation_v4.db"
MAP_PATH = OUT / "ablation_v4_mapping.json"

PROJECT = ProjectId("p1")
OWNER = UserId("o1")
#: B2 的随机种子（按 qid 派生 → 可复现，且不受并发顺序影响）。
RANDOM_SEED = 20261023
#: 正确性裁判数（取均值）。
CORRECTNESS_JUDGES = 2
#: 并发。
CONCURRENCY = 8
#: H2 的 ε。
EPSILON = 0.05
#: 明显优于的均值差阈值。
CLEAR_GAP = 0.10

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "point": {"type": "string"},
                    "covered": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["point", "covered", "reason"],
            },
        },
        "verdict": {"type": "string"},
    },
    "required": ["points", "verdict"],
}

#: 答案里引用编号的正则（`[E3]` / 裸 `E3`）。
CITATION_RE = re.compile(r"\[E(\d+)\]|\bE(\d+)\b")

#: 正确性判据（**预先写死**，与 v3 逐字一致）。
JUDGE_RUBRIC = """判据（预先写死，逐条独立判）：
1. **覆盖** = 候选答案**明确陈述**了该要点里的具体事实/数字/名称/结论；
   - 只提到话题、泛泛而谈、答非所问、把结论说反 → **未覆盖**；
   - 数字必须一致（或等价表述）；名称必须一致。
2. **不看长度**：答案更长或更短都不影响判断，只看"这个事实有没有被陈述"。
3. **不看引用**：答案有没有引材料编号，这里一律不管（引用另算）。
4. **不脑补**：答案没写的，就算没覆盖——不要因为"它应该知道"而判覆盖。
verdict：全部覆盖 = "correct"；部分覆盖 = "partial"；一条都没覆盖 = "wrong"。"""


def _rss_mb() -> int:
    """当前进程峰值 RSS（MB）——建库时打印，用来定位内存增长。"""

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024


class SubBatchedEmbedder:
    """把 `embed_many` 拆成小批（**只在本实验脚本里**，不动 `src/`）。

    为什么需要：`fastembed` 的 ONNX 推理峰值内存**随批大小涨**——155 块一批时
    峰值 RSS 冲到 ~3.3GB（本沙箱 cgroup 上限 4GB，建库到第 30 份被 OOM 杀掉）；
    拆成 32 一批后峰值 ~400MB，结果**逐字相同**（同样的文本、同样的模型、同样的口径）。
    """

    def __init__(self, inner, batch_size: int = 32) -> None:
        self._inner = inner
        self._batch = batch_size
        self.dimensions = inner.dimensions

    def embed_many(self, texts):
        out = []
        for start in range(0, len(texts), self._batch):
            out.extend(self._inner.embed_many(texts[start:start + self._batch]))
        return out


def env(key: str) -> str:
    """从 `backend/.env` 读配置（与 v2/v3 同一口径）。"""

    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            name, value = line.strip().split("=", 1)
            if name == key:
                return value
    raise KeyError(key)


# ── 复用 v3 的问题与 gold ───────────────────────────────────────────────


def load_v3() -> tuple[dict[int, tuple[str, list[dict]]], dict[str, dict]]:
    """v3 的 120 个 (材料, 问题) 对 + gold 块标注。

    v3 的 questions 文件里 material 9 的键是 `"09"`（另有空键 `"9"`）——
    按 `int(key)` 归一，非空的那份胜出，gold 键同口径（`"09-0"`）。
    """

    raw_q = json.loads((V3 / "ablation_v3_questions.json").read_text(encoding="utf-8"))
    raw_g = json.loads((V3 / "ablation_v3_gold.json").read_text(encoding="utf-8"))
    questions: dict[int, tuple[str, list[dict]]] = {}
    for key, items in raw_q.items():
        if not items:
            continue
        index = int(key)
        questions.setdefault(index, (key, items))
    gold: dict[str, dict] = {}
    for key, record in raw_g.items():
        gold[key] = record
    return questions, gold


# ── phase 0：建库（40 份真文 → 真库 + 索引）──────────────────────────────


def build_library(items: list[dict], embedder) -> tuple[object, dict[str, str]]:  # type: ignore[no-untyped-def]
    """把 40 份真文建成一个项目的材料库（真 `materials`/`material_chunks` + FTS5 + 向量）。

    切块口径与 v3 的 `build_packets` / v2 的 `build_index` 逐字一致（`chunk_text` 400 字）。
    返回 `(connection, {item_index: material_id})`。
    """

    OUT.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    connection = connect(DB_PATH, embedder=embedder)
    store = SqliteMaterialStore(connection, ids=UuidIdGenerator())
    vector_index = SqliteVectorIndex(connection, embedder=embedder)
    mapping: dict[str, str] = {}
    for index, item in enumerate(items):
        clean = normalize_web_text(item["text"])
        chunks = chunk_text(clean, parser_id="tavily", max_chars=400)
        parsed = ParsedMaterial(
            origin="web_fetch", title=item["title"], source_ref=item["url"],
            media_type="text/html", bytes_len=len(item["text"].encode()),
            chunks=tuple(
                MaterialChunk(chunk_id=f"c{chunk.ordinal:02d}", ordinal=chunk.ordinal,
                              text=chunk.text, heading_path=chunk.heading_path,
                              anchor=chunk.anchor)
                for chunk in chunks),
            parser_id="tavily", parser_version="bench")
        sha256 = hashlib.sha256(item["text"].encode()).hexdigest()
        packet = store.put(
            project_id=PROJECT, parsed=parsed, sha256=sha256,
            content=item["text"].encode(), now=datetime(2026, 10, 23, tzinfo=UTC),
            owner=OWNER)
        vector_index.write(packet.material_id)
        mapping[str(index)] = packet.material_id
        print(f"  [{index:02d}] {len(chunks)} 块 → {packet.material_id} · rss={_rss_mb()}MB")
    connection.commit()
    MAP_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  写入 {DB_PATH.name}（{len(mapping)} 份）")
    return connection, mapping


def open_library(embedder) -> tuple[object, dict[str, str]]:  # type: ignore[no-untyped-def]
    """复用已建好的库（不重建）。"""

    connection = connect(DB_PATH, embedder=embedder)
    mapping = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    return connection, mapping


def probe_retrieval_path(connection, embedder) -> dict:
    """**如实记下 C 实际走的检索路**：扩展载没载、向量表有没有行、embedder 能不能用。"""

    from pmstudio.infrastructure.sqlite.database import load_sqlite_vec

    extension = load_sqlite_vec(connection)
    row = connection.execute("SELECT COUNT(*) AS n FROM material_chunks_vec").fetchone()
    vec_rows = int(row["n"]) if row is not None else 0
    try:
        embedder.embed_many(["检索路径探测"])
        embedder_ok = True
    except Exception:  # noqa: BLE001 —— 探测失败就如实记
        embedder_ok = False
    path = "FTS5+向量" if (extension and vec_rows > 0 and embedder_ok) else "纯FTS5"
    return {"扩展已载入": extension, "向量行数": vec_rows, "embedder可用": embedder_ok,
            "实际检索路": path}


# ── 五臂的上下文构造 ────────────────────────────────────────────────────


def _eids_for(picked, position: dict[str, int]) -> dict[tuple[str, str], EvidenceId]:
    """按**文档序**发号（材料顺序 × 块序）——与生产 `StartRound` 同一口径。"""

    ordered = sorted(picked, key=lambda item: (position[item[0]], item[1].ordinal))
    return {
        (material_id, chunk.chunk_id): evidence_id(index + 1)
        for index, (material_id, chunk) in enumerate(ordered)
    }


def _render(picked, packets_by_id: dict[str, MaterialPacket], position: dict[str, int]):
    """选中的块 → 渲染文本 + 编号表（`render_materials` 是唯一渲染点）。"""

    if not picked:
        return NO_MATERIAL_TEXT, {}
    eid_of = _eids_for(picked, position)
    entries = []
    for material_id in dict.fromkeys(item[0] for item in picked):
        packet = packets_by_id[material_id]
        eids = {cid: eid for (mid, cid), eid in eid_of.items() if mid == material_id}
        entries.append((packet, eids))
    return render_materials(entries).text, eid_of


def arm_A() -> dict:
    return {"text": NO_MATERIAL_TEXT, "eids": {}, "picked": [], "materials": [],
            "chars": len(NO_MATERIAL_TEXT)}


def arm_single(packet: MaterialPacket, question: str) -> dict:
    """B1：只带一份材料（gold），走 `select_chunks`。"""

    picked, _ = select_chunks([packet], query=question)
    text, eids = _render(picked, {packet.material_id: packet}, {packet.material_id: 0})
    return {"text": text, "eids": eids, "picked": picked, "materials": [packet.material_id],
            "chars": len(text)}


def arm_multi(packets: list[MaterialPacket], question: str) -> dict:
    """B2：带 N 份材料，走 `select_chunks`。"""

    packets_by_id = {packet.material_id: packet for packet in packets}
    position = {packet.material_id: index for index, packet in enumerate(packets)}
    picked, _ = select_chunks(packets, query=question)
    text, eids = _render(picked, packets_by_id, position)
    return {"text": text, "eids": eids, "picked": picked,
            "materials": [packet.material_id for packet in packets], "chars": len(text)}


def arm_C(hits, store, question: str) -> dict:
    """C：**库级检索** → 命中材料 → `select_chunks` 填满预算（本方案）。

    `hits` 来自真 `SqliteHybridRetriever.search(project_id, query, limit=SEARCH_LIMIT)`。
    候选集 = 命中材料（按命中序），选块仍只有 `select_chunks` 一个决策点。
    """

    material_ids = list(dict.fromkeys(hit.material_id for hit in hits))
    packets = [store.get(material_id, owner=OWNER) for material_id in material_ids]
    packets = [packet for packet in packets if packet is not None]
    packets_by_id = {packet.material_id: packet for packet in packets}
    position = {packet.material_id: index for index, packet in enumerate(packets)}
    picked, _ = select_chunks(packets, query=question)
    text, eids = _render(picked, packets_by_id, position)
    return {"text": text, "eids": eids, "picked": picked,
            "materials": [packet.material_id for packet in packets], "chars": len(text),
            "hits": len(hits), "hit_materials": len(material_ids)}


def arm_D(packet: MaterialPacket, gold: list[int]) -> dict:
    """D oracle：只带 gold 块。"""

    gold_chunks = [packet.parsed.chunks[o] for o in gold if o < len(packet.parsed.chunks)]
    picked = [(packet.material_id, chunk) for chunk in gold_chunks]
    text, eids = _render(picked, {packet.material_id: packet}, {packet.material_id: 0})
    return {"text": text, "eids": eids, "picked": picked, "materials": [packet.material_id],
            "chars": len(text)}


def gold_eids(arm: dict, gold: list[int]) -> list[str]:
    """这一臂里，gold 块的编号（`E…`）。"""

    out = set()
    for (_material_id, chunk_id), eid in arm["eids"].items():
        ordinal = int(str(chunk_id).rsplit("#c", 1)[-1])
        if ordinal in gold:
            out.add(str(eid))
    return sorted(out, key=lambda e: int(e[1:]))


def answer_prompt(external_material: str, question: str) -> str:
    return (
        "【任务】回答下面的问题。\n"
        "要求：\n"
        "1. 回答要完整、准确；\n"
        "2. **凡是依据外部材料得到的内容，都要在相应位置用方括号标注它的编号**（如 [E3]）"
        "——引用是本轮评分项，不标编号等于没引；\n"
        "3. 编号只能来自下面给出的材料，不许编造；\n"
        "4. 如果下面没有外部材料，就凭你自己的知识回答（此时不需要编号）。\n\n"
        "【外部材料】（不是用户说的话）\n"
        f"{external_material}\n\n"
        f"【问题】\n{question}\n\n"
        "只输出 JSON：{\"answer\": \"...\"}"
    )


def build_gateway(concurrency: int = CONCURRENCY) -> LiteLLMGateway:
    return LiteLLMGateway(
        model=env("PMSTUDIO_MODEL"), api_key=env("PMSTUDIO_API_KEY"),
        api_base=env("PMSTUDIO_API_BASE"), timeout=150.0,
        model_params={"extra_body": {"enable_thinking": False}, "max_tokens": 4096},
        bulkhead_config=BulkheadConfig(capacity=concurrency, queue_wait_seconds=30.0))


async def call_json(gateway, *, purpose: str, prompt: str, schema: dict,
                    max_tokens: int = 4096) -> tuple[dict | None, int, float, str | None]:
    """一次结构化调用：返回 `(json, tokens, 墙钟ms, 错误)`；失败不抛，照实记。"""

    started = time.perf_counter()
    try:
        reply = await gateway.complete(ModelRequest(
            purpose=purpose, messages=(ModelMessage(role="user", content=prompt),),
            schema_name=purpose, response_schema=schema, max_tokens=max_tokens))
    except Exception as error:  # noqa: BLE001 —— 上游失败照实记
        return None, 0, (time.perf_counter() - started) * 1000, f"{type(error).__name__}: {error}"
    elapsed = (time.perf_counter() - started) * 1000
    tokens = int(reply.usage.get("prompt_tokens", 0)) + int(
        reply.usage.get("completion_tokens", 0))
    try:
        return json.loads(reply.text), tokens, elapsed, None
    except Exception as error:  # noqa: BLE001 —— 不是 JSON
        return None, tokens, elapsed, f"bad-json: {error}"


# ── phase 3：五臂答题 ────────────────────────────────────────────────────


async def phase_answers(jobs: list[tuple], available: list[int],
                        packets_by_index: dict[int, MaterialPacket],
                        store, retriever, gateway, concurrency: int = CONCURRENCY) -> None:
    path = OUT / "ablation_v4_answers.json"
    if path.exists():
        print(f"  复用已存的 {path.name}")
        return
    questions, gold = load_v3()
    semaphore = asyncio.Semaphore(concurrency)
    out: dict[str, dict] = {}

    async def one(qid: str, mid_key: str, qi: int, index: int, question: dict):
        async with semaphore:
            packet = packets_by_index[index]
            gold_list = list(gold.get(qid, {}).get("gold", []))
            # C：**生产检索器**，同步调用，不用 await。
            hits = retriever.search(project_id=PROJECT, query=question["question"],
                                    limit=SEARCH_LIMIT, owner=OWNER)
            arms = {
                "A": arm_A(),
                "B1": arm_single(packet, question["question"]),
                "B2": arm_multi(_b2_packets(index, qid, available, packets_by_index),
                                question["question"]),
                "C": arm_C(hits, store, question["question"]),
                "D": arm_D(packet, gold_list),
            }
            record: dict = {"question": question["question"], "points": question["points"],
                            "gold": gold_list, "arms": {}}
            for arm in ("A", "B1", "B2", "C", "D"):
                info = arms[arm]
                record["arms"][arm] = {
                    "text": info["text"],
                    "eids": {cid: str(eid) for (_m, cid), eid in info["eids"].items()},
                    "picked": [
                        [material_id, chunk.ordinal]
                        for material_id, chunk in info["picked"]
                    ],
                    "materials": info["materials"],
                    "chars": info["chars"],
                    "gold_eids": gold_eids(info, gold_list),
                }
                if arm == "C":
                    record["arms"][arm]["hits"] = info["hits"]
                    record["arms"][arm]["hit_materials"] = info["hit_materials"]
                data, tokens, elapsed, error = await call_json(
                    gateway, purpose=f"answer_{arm}",
                    prompt=answer_prompt(info["text"], question["question"]),
                    schema=ANSWER_SCHEMA, max_tokens=2048)
                answer = (data or {}).get("answer", "") if data else ""
                record["arms"][arm].update({
                    "answer": answer, "tokens": tokens, "ms": round(elapsed, 1),
                    "failed": bool(error), "error": error})
                print(f"    [{qid}][{arm}] {info['chars']}字/{len(info['materials'])}份 → "
                      f"{len(answer)}字 {tokens}tok {elapsed:.0f}ms"
                      + (f" ✗{error}" if error else ""))
            out[qid] = record

    await asyncio.gather(*(one(qid, mid_key, qi, index, question)
                           for qid, mid_key, qi, index, question in jobs))
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  写入 {path.name}")


def _b2_packets(index: int, qid: str, available: list[int],
                 packets_by_index: dict[int, MaterialPacket]) -> list:
    """B2：3 份随机材料（**不含 gold**），种子按 qid 派生 → 可复现。"""

    others = [i for i in available if i != index]
    picked = random.Random(f"{RANDOM_SEED}:{qid}").sample(others, min(3, len(others)))
    return [packets_by_index[i] for i in picked]


# ── phase 4：正确性裁判（与 v3 同一套）──────────────────────────────────


def judge_prompt(question: str, points: list[str], answer: str) -> str:
    listed = "\n".join(f"{i + 1}. {point}" for i, point in enumerate(points))
    return (
        "下面是一个问题、该问题的标准要点、以及一个候选答案。请逐条判断候选答案是否**覆盖**了要点。\n"
        f"{JUDGE_RUBRIC}\n"
        f"问题：{question}\n标准要点：\n{listed}\n候选答案：\n{answer}\n"
        "只输出 JSON：{\"points\": [{\"point\": \"...\", \"covered\": true, \"reason\": \"...\"}], "
        "\"verdict\": \"correct|partial|wrong\"}"
    )


ARMS = ("A", "B1", "B2", "C", "D")


async def phase_judge(gateway, concurrency: int = CONCURRENCY) -> None:
    answers = json.loads((OUT / "ablation_v4_answers.json").read_text(encoding="utf-8"))
    path = OUT / "ablation_v4_judgments.json"
    if path.exists():
        print(f"  复用已存的 {path.name}")
        return
    semaphore = asyncio.Semaphore(concurrency)
    out: dict[str, dict] = {}

    async def one(qid: str, record: dict):
        async with semaphore:
            entry: dict = {}
            for arm in ARMS:
                answer = record["arms"][arm]["answer"]
                votes: list[dict] = []
                for judge in range(CORRECTNESS_JUDGES):
                    data, tokens, elapsed, error = await call_json(
                        gateway, purpose=f"judge_{arm}_j{judge}",
                        prompt=judge_prompt(record["question"], record["points"], answer),
                        schema=JUDGE_SCHEMA, max_tokens=1024)
                    if error or not data:
                        print(f"    ✗ [{qid}][{arm}] 裁判{judge} 失败：{error}")
                        continue
                    votes.append(data)
                entry[arm] = votes
            out[qid] = entry
            covs = [_mean_coverage(entry[arm]) for arm in ARMS]
            print(f"    [{qid}] " + " ".join(
                f"{arm}={cov:.2f}" for arm, cov in zip(ARMS, covs, strict=True)))

    await asyncio.gather(*(one(qid, record) for qid, record in answers.items()))
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  写入 {path.name}")


def _mean_coverage(votes: list[dict]) -> float:
    """多裁判取**逐点覆盖率的均值**（没有投票就 0）。"""

    if not votes:
        return 0.0
    scores = []
    for vote in votes:
        points = vote.get("points", [])
        if not points:
            continue
        scores.append(sum(1 for item in points if item.get("covered")) / len(points))
    return sum(scores) / len(scores) if scores else 0.0


def cited_eids(answer: str) -> set[str]:
    return {f"E{match.group(1) or match.group(2)}" for match in CITATION_RE.finditer(answer)}


# ── phase 5：汇总 + 判据 ─────────────────────────────────────────────────


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _paired(rows: list[dict], arm_x: str, arm_y: str) -> dict:
    """配对增量：同一 qid，唯一差别是臂。返回 `(y 好、x 好、平)`。"""

    by: dict[str, dict[str, dict]] = {}
    for row in rows:
        by.setdefault(row["qid"], {})[row["arm"]] = row
    x_better = y_better = tie = 0
    for _qid, arms in by.items():
        if arm_x not in arms or arm_y not in arms:
            continue
        if arms[arm_y]["correctness"] > arms[arm_x]["correctness"] + 1e-9:
            y_better += 1
        elif arms[arm_x]["correctness"] > arms[arm_y]["correctness"] + 1e-9:
            x_better += 1
        else:
            tie += 1
    return {"x_better": x_better, "y_better": y_better, "tie": tie,
            "n": x_better + y_better + tie}


def _verdict(votes: list[dict]) -> str:
    if not votes:
        return "wrong"
    order = {"correct": 2, "partial": 1, "wrong": 0}
    scores = [order.get(str(vote.get("verdict", "wrong")), 0) for vote in votes]
    return {2: "correct", 1: "partial", 0: "wrong"}[max(scores)]


def _precision(rows: list[dict]) -> float:
    hits = sum(len(set(row["cited"]) & set(row["gold_eids"])) for row in rows)
    total = sum(len(row["cited"]) for row in rows)
    return hits / total if total else 0.0


def phase_aggregate(retrieval_path: dict) -> dict:
    answers = json.loads((OUT / "ablation_v4_answers.json").read_text(encoding="utf-8"))
    judgments = json.loads((OUT / "ablation_v4_judgments.json").read_text(encoding="utf-8"))
    rows: list[dict] = []
    for qid, record in answers.items():
        gold = set(record["gold"])
        for arm in ARMS:
            info = record["arms"][arm]
            gold_eids = set(info["gold_eids"])
            cited = cited_eids(info["answer"])
            gold_cited = cited & gold_eids
            rows.append({
                "qid": qid, "arm": arm,
                "correctness": _mean_coverage(judgments.get(qid, {}).get(arm, [])),
                "verdict": _verdict(judgments.get(qid, {}).get(arm, [])),
                "gold_recall": (len(gold_cited) / len(gold)) if gold else None,
                "gold_selected": len({ordinal for _m, ordinal in info["picked"]} & gold),
                "gold_total": len(gold),
                "cited": sorted(cited, key=lambda e: int(e[1:])),
                "gold_eids": sorted(gold_eids, key=lambda e: int(e[1:])),
                "cited_non_gold": sorted(cited - gold_eids, key=lambda e: int(e[1:])),
                "material_chars": info["chars"],
                "materials": len(info["materials"]),
                "picked": len(info["picked"]),
                "tokens": info["tokens"], "ms": info["ms"], "failed": info["failed"],
            })

    metrics: dict[str, dict] = {}
    for arm in ARMS:
        subset = [row for row in rows if row["arm"] == arm]
        n = len(subset)
        metrics[arm] = {
            "n": n,
            "正确率": _mean([row["correctness"] for row in subset]),
            "全对率": _mean([1.0 if row["verdict"] == "correct" else 0.0 for row in subset]),
            "引用命中": _mean([row["gold_recall"] for row in subset
                              if row["gold_recall"] is not None]),
            "引用精度": _precision(subset),
            "gold选中数": _mean([row["gold_selected"] for row in subset]),
            "材料份数": _mean([row["materials"] for row in subset]),
            "材料字数": _mean([row["material_chars"] for row in subset]),
            "tokens": sum(row["tokens"] for row in subset),
            "延迟ms": sum(row["ms"] for row in subset),
            "失败": sum(1 for row in subset if row["failed"]),
        }

    verdict = _verdict_hypotheses(rows, metrics)
    result = {"检索路": retrieval_path, "metrics": metrics, "verdict": verdict, "rows": rows}
    (OUT / "ablation_v4_results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def _verdict_hypotheses(rows: list[dict], metrics: dict) -> dict:
    """预注册判据 H1–H4（跑完只对照）。"""

    out: dict[str, dict] = {}
    # H1：C 明显优于 A
    h1 = _paired(rows, "A", "C")
    out["H1"] = {
        "判据": "C 的正确率明显高于 A（配对改善 > 倒退 且 均值差 ≥ 0.10）",
        "C-A": round(metrics["C"]["正确率"] - metrics["A"]["正确率"], 4),
        "配对": h1,
        "中": (metrics["C"]["正确率"] - metrics["A"]["正确率"] >= CLEAR_GAP
               and h1["y_better"] > h1["x_better"]),
    }
    # H2：C ≥ B1 − ε
    h2 = _paired(rows, "B1", "C")
    out["H2"] = {
        "判据": f"C ≥ B1 − ε（ε={EPSILON}）：库级检索不输给「用户恰好勾对」（重构安全条件）",
        "C-B1": round(metrics["C"]["正确率"] - metrics["B1"]["正确率"], 4),
        "配对": h2,
        "中": metrics["C"]["正确率"] - metrics["B1"]["正确率"] >= -EPSILON,
    }
    # H3：C 明显优于 B2
    h3 = _paired(rows, "B2", "C")
    out["H3"] = {
        "判据": "C 的正确率明显高于 B2（配对改善 > 倒退 且 均值差 ≥ 0.10）",
        "C-B2": round(metrics["C"]["正确率"] - metrics["B2"]["正确率"], 4),
        "配对": h3,
        "中": (metrics["C"]["正确率"] - metrics["B2"]["正确率"] >= CLEAR_GAP
               and h3["y_better"] > h3["x_better"]),
    }
    # H4：C 与 D 的差距 + 成本归一化
    gap = metrics["D"]["正确率"] - metrics["C"]["正确率"]
    out["H4"] = {
        "判据": "C 与 D（oracle）的正确率差 = 离上界还差多少；并列五臂 tokens/延迟",
        "D-C": round(gap, 4),
        "tokens": {arm: metrics[arm]["tokens"] for arm in ARMS},
        "延迟ms": {arm: round(metrics[arm]["延迟ms"]) for arm in ARMS},
        "材料字数": {arm: round(metrics[arm]["材料字数"]) for arm in ARMS},
    }
    return out


def show(result: dict) -> None:
    metrics, verdict = result["metrics"], result["verdict"]
    print(f"\n=== C 实际检索路：{result['检索路']['实际检索路']} ===")
    print("\n=== 臂 × 指标 ===")
    header = (f"{'臂':<4}{'正确率':>8}{'全对率':>8}{'引用命中':>9}{'引用精度':>9}"
              f"{'gold选中':>9}{'材料份':>7}{'tokens':>10}{'延迟ms':>10}{'失败':>6}")
    print(header)
    for arm in ARMS:
        m = metrics[arm]
        print(f"{arm:<4}{m['正确率']:>8.3f}{m['全对率']:>8.3f}{m['引用命中']:>9.3f}"
              f"{m['引用精度']:>9.3f}{m['gold选中数']:>9.2f}{m['材料份数']:>7.2f}"
              f"{m['tokens']:>10}{m['延迟ms']:>10.0f}{m['失败']:>6}")
    print("\n=== 预注册判据 ===")
    for key in ("H1", "H2", "H3", "H4"):
        item = verdict[key]
        mark = item.get("中", "")
        print(f"  {key}: {item['判据']}")
        detail = {
            k: v for k, v in item.items() if k != "判据"
        }
        print(
            f"      {json.dumps(detail, ensure_ascii=False)}"
            + (f"  → {'中' if mark else '不中'}" if isinstance(mark, bool) else "")
        )


async def main() -> None:
    args = sys.argv[1:]
    phases: list[str] = []
    limit = None
    rebuild = False
    concurrency = CONCURRENCY
    index = 0
    while index < len(args):
        item = args[index]
        if item == "--limit" and index + 1 < len(args):
            limit = int(args[index + 1])
            index += 2
            continue
        if item == "--concurrency" and index + 1 < len(args):
            concurrency = int(args[index + 1])
            index += 2
            continue
        if item == "--rebuild":
            rebuild = True
            index += 1
            continue
        if not item.startswith("--"):
            phases.append(item)
        index += 1
    OUT.mkdir(parents=True, exist_ok=True)
    items = json.loads((CORPUS / "C raw_content-净.json").read_text(encoding="utf-8"))
    if limit:
        items = items[:limit]
    embedder = SubBatchedEmbedder(FastEmbedEmbedder())

    questions, gold = load_v3()
    if rebuild or not DB_PATH.exists():
        connection, mapping = build_library(items, embedder)
    else:
        connection, mapping = open_library(embedder)
    store = SqliteMaterialStore(connection, ids=UuidIdGenerator())
    retriever = SqliteHybridRetriever(connection, embedder=embedder)
    retrieval_path = probe_retrieval_path(connection, embedder)
    print(f"  检索路：{retrieval_path}")

    packets_by_index = {
        int(index): store.get(material_id, owner=OWNER)
        for index, material_id in mapping.items()
    }
    packets_by_index = {i: p for i, p in packets_by_index.items() if p is not None}
    available = sorted(packets_by_index)
    jobs = [
        (f"{mid_key}-{qi}", mid_key, qi, index, question)
        for index, (mid_key, qs) in sorted(questions.items())
        if index in packets_by_index
        for qi, question in enumerate(qs)
    ]
    print(f"库 {len(packets_by_index)} 份 · 问题 {len(jobs)} 个 · 预算 {MATERIAL_BUDGET} 字")
    gateway = build_gateway(concurrency)

    if phases == ["build"]:
        print("  只建库，不跑模型。")
        return
    if not phases or "answers" in phases:
        print("\n=== phase · 五臂答题 ===")
        await phase_answers(jobs, available, packets_by_index, store, retriever, gateway,
                            concurrency)
    if not phases or "judge" in phases:
        print("\n=== phase · 正确性裁判 ===")
        await phase_judge(gateway, concurrency)
    if not phases or "aggregate" in phases:
        print("\n=== phase · 汇总 ===")
        show(phase_aggregate(retrieval_path))


if __name__ == "__main__":
    asyncio.run(main())
