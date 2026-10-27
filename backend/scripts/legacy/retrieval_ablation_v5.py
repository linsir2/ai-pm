"""**收敛消融 v5**：库级检索要不要做 stage 2「按分数收敛」（见 `experiment-v5.md`）。

## 要回答的问题

v4 测的是**没有收敛**的库级检索（`C`：命中材料全交给 `select_chunks` 的粗排）——
结果 0.485，输给"用户恰好勾对"的 B1（0.737）。根因是**预算被摊薄**：
gold 材料从 B1 的 13.45 块掉到 C 的 1.54 块。

本轮只改一件事：把命中块的**外部分数**交给 `select_chunks`（`scores=`），
让它**按分数收敛**、不做"每份材料露一名"的保底。问：这样能不能把 C 拉回 B1 − ε。

## 臂（3 个，同一次运行，模型不漂）

| 臂 | 上下文里放什么 | 模拟的是 |
|---|---|---|
| **B1** | 只带 gold 那份材料 | 今天的最好情况（用户恰好勾对）——**上界对照** |
| **C** | 命中材料 → `select_chunks(query=…)`（**有保底**） | v4 的"库级检索"——**改动的对照组** |
| **C'** | 命中材料 → `select_chunks(scores=…)`（**收敛、无保底**） | **本方案** |

> 为什么同一次运行里重跑 B1 与 C：v4 的 B1/C 是在**旧成本尺子**（`ordinal == 0` 计抬头）下测的，
> 而本次实现顺带修了尺子（F0）。同一次运行重跑，才能把"收敛的效果"与"尺子的效果"分开。
> v4 的 A/B1/B2/C/D 结果留在 `ablation_v4_results.json`，用于对照。

## 判据（**预先注册**，跑完只对照）

- **H2′**：`C' ≥ B1 − ε`（ε=0.05）——**收敛后不输给"用户恰好勾对"**（本方案的验收条件）。
- **H3′**：`C' − C`（**收敛本身的增量**）——预期 ≥ 0.10 且配对改善 > 倒退。
- **H4′**：`C'` 与 v4 的 `C` 的差 = **尺子 + 收敛**合计的效果；并列三臂 tokens/延迟/材料字数。

## 方法

- **库**：**直接复用 v4 的库**
  （`.codex/runs/2026-10-23/rerank_llm_v2/ablation_v4/ablation_v4.db`，
  40 份真文 + FTS5 + 向量索引），不重建；
- **问题与 gold**：复用 v3 的 120 个 (材料, 问题) 对 + gold 块标注；
- **复用**：`chunk_text` / `select_chunks` / `render_materials` / `LiteLLMGateway`
  （不重写渲染与指标）；
- **裁判**：与 v3/v4 同一套（判据跑前写死、2 名裁判取均值、只看"要点有没有被明确陈述"）。

## 环境降级

本沙箱 stdlib `sqlite3` 缺 `enable_load_extension` → 换 `pysqlite3-binary`，**不改 `src/`**；
载不进就退纯 FTS5，并**如实标注 C' 实际走的是哪条路**。

跑法：

```bash
cd backend && uv run python -u scripts/retrieval_ablation_v5.py \
    [answers|judge|aggregate] [--limit 10] [--reuse-limit 10]
```
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

import sqlite3 as _sqlite3

if not hasattr(_sqlite3.Connection, "enable_load_extension"):
    import pysqlite3 as _pysqlite3

    sys.modules["sqlite3"] = _pysqlite3

from pmstudio.application.context import (  # noqa: E402
    MATERIAL_BUDGET,
    SECTION_OVERHEAD,
    render_materials,
    select_chunks,
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

CORPUS = Path("../.codex/runs/2026-10-23/rerank_llm_v2")
V3 = CORPUS / "ablation_v3"
V4 = CORPUS / "ablation_v4"
OUT = CORPUS / "ablation_v5"
DB_PATH = V4 / "ablation_v4.db"
MAP_PATH = V4 / "ablation_v4_mapping.json"
V4_ANSWERS = V4 / "ablation_v4_answers.json"

PROJECT = ProjectId("p1")
OWNER = UserId("o1")
#: H2′ 的 ε。
EPSILON = 0.05
#: 明显优于的均值差阈值。
CLEAR_GAP = 0.10
#: 正确性裁判数（取均值）。
CORRECTNESS_JUDGES = 2
#: 并发。
CONCURRENCY = 8
#: 检索宽度（**本档用 10**；翻正后再扫 2/6/10/20/50）。
DEFAULT_REUSE_LIMIT = 10

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

#: 正确性判据（**预先写死**，与 v3/v4 逐字一致）。
JUDGE_RUBRIC = """判据（预先写死，逐条独立判）：
1. **覆盖** = 候选答案**明确陈述**了该要点里的具体事实/数字/名称/结论；
   - 只提到话题、泛泛而谈、答非所问、把结论说反 → **未覆盖**；
   - 数字必须一致（或等价表述）；名称必须一致。
2. **不看长度**：答案更长或更短都不影响判断，只看"这个事实有没有被陈述"。
3. **不看引用**：答案有没有引材料编号，这里一律不管（引用另算）。
4. **不脑补**：答案没写的，就算没覆盖——不要因为"它应该知道"而判覆盖。
verdict：全部覆盖 = "correct"；部分覆盖 = "partial"；一条都没覆盖 = "wrong"。"""

ARMS = ("B1", "C", "C_prime")


def env(key: str) -> str:
    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            name, value = line.strip().split("=", 1)
            if name == key:
                return value
    raise KeyError(key)


def load_v3() -> tuple[dict[int, tuple[str, list[dict]]], dict[str, dict]]:
    """v3 的 120 个 (材料, 问题) 对 + gold 块标注（**不重做标注**）。

    v3 的 questions 文件里 material 9 的键是 `"09"`（另有空键 `"9"`）——
    按 `int(key)` 归一，非空的那份胜出；`qid` 用**原始键**（`"09-0"`），与 v4 逐字一致。
    """

    raw_q = json.loads((V3 / "ablation_v3_questions.json").read_text(encoding="utf-8"))
    raw_g = json.loads((V3 / "ablation_v3_gold.json").read_text(encoding="utf-8"))
    questions: dict[int, tuple[str, list[dict]]] = {}
    for key, items in raw_q.items():
        if not items:
            continue
        index = int(key)
        questions.setdefault(index, (key, items))
    return questions, raw_g


# ── 上下文构造（与 v4 同一口径：`select_chunks` 是唯一决策点）────────────


def _eids_for(picked, position: dict[str, int]) -> dict[tuple[str, str], EvidenceId]:
    """按**文档序**发号（材料顺序 × 块序）——与生产 `StartRound` 同一口径。"""

    ordered = sorted(picked, key=lambda item: (position[item[0]], item[1].ordinal))
    return {
        (material_id, chunk.chunk_id): evidence_id(index + 1)
        for index, (material_id, chunk) in enumerate(ordered)
    }


def _render(picked, packets_by_id: dict, position: dict[str, int]):
    if not picked:
        return "", {}
    eid_of = _eids_for(picked, position)
    entries = []
    for material_id in dict.fromkeys(item[0] for item in picked):
        packet = packets_by_id[material_id]
        eids = {cid: eid for (mid, cid), eid in eid_of.items() if mid == material_id}
        entries.append((packet, eids))
    return render_materials(entries).text, eid_of


def arm_B1(packet, question: str) -> dict:
    """B1：只带 gold 那份材料（用户恰好勾对）。"""

    picked, _ = select_chunks([packet], query=question)
    text, eids = _render(picked, {packet.material_id: packet}, {packet.material_id: 0})
    return {"text": text, "eids": eids, "picked": picked,
            "materials": [packet.material_id], "chars": len(text)}


def arm_C(packets, question: str) -> dict:
    """C：命中材料 → `select_chunks(query=…)`（**有保底**，v4 的老行为）。"""

    packets_by_id = {packet.material_id: packet for packet in packets}
    position = {packet.material_id: index for index, packet in enumerate(packets)}
    picked, _ = select_chunks(packets, query=question)
    text, eids = _render(picked, packets_by_id, position)
    return {"text": text, "eids": eids, "picked": picked,
            "materials": [packet.material_id for packet in packets], "chars": len(text)}


def arm_C_prime(packets, hits, question: str) -> dict:
    """C'：命中材料 → `select_chunks(scores=…)`（**收敛、无保底**）——本方案。

    `scores` 的键是 `chunk_id`，值是检索器给的分数（越大越好）。
    **候选集只有命中的块**；非命中块不参赛，也不被"保底"拉回来。
    """

    auto_ids = {packet.material_id for packet in packets}
    scores = {hit.chunk_id: hit.score for hit in hits if hit.material_id in auto_ids}
    position = {packet.material_id: index for index, packet in enumerate(packets)}
    packets_by_id = {packet.material_id: packet for packet in packets}
    # `section_overhead` 只预留一次（生产里它由手勾段预留；这里没有手勾段）。
    picked, used = select_chunks(
        packets, scores=scores, budget=MATERIAL_BUDGET, section_overhead=SECTION_OVERHEAD
    )
    text, eids = _render(picked, packets_by_id, position)
    return {"text": text, "eids": eids, "picked": picked,
            "materials": [packet.material_id for packet in packets], "chars": len(text),
            "scored_chunks": len(scores), "used": used}


def gold_eids(arm: dict, gold: list[int]) -> list[str]:
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


def judge_prompt(question: str, points: list[str], answer: str) -> str:
    listed = "\n".join(f"{i + 1}. {point}" for i, point in enumerate(points))
    return (
        "下面是一个问题、该问题的标准要点、以及一个候选答案。请逐条判断候选答案是否**覆盖**了要点。\n"
        f"{JUDGE_RUBRIC}\n"
        f"问题：{question}\n标准要点：\n{listed}\n候选答案：\n{answer}\n"
        "只输出 JSON：{\"points\": [{\"point\": \"...\", \"covered\": true, \"reason\": \"...\"}], "
        "\"verdict\": \"correct|partial|wrong\"}"
    )


def build_gateway(concurrency: int = CONCURRENCY) -> LiteLLMGateway:
    return LiteLLMGateway(
        model=env("PMSTUDIO_MODEL"), api_key=env("PMSTUDIO_API_KEY"),
        api_base=env("PMSTUDIO_API_BASE"), timeout=150.0,
        model_params={"extra_body": {"enable_thinking": False}, "max_tokens": 4096},
        bulkhead_config=BulkheadConfig(capacity=concurrency, queue_wait_seconds=30.0))


async def call_json(gateway, *, purpose: str, prompt: str, schema: dict,
                    max_tokens: int = 4096):
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
    except Exception as error:  # noqa: BLE001
        return None, tokens, elapsed, f"bad-json: {error}"


def gold_material_id_of(qid: str) -> str | None:
    """从 v4 的答案里取这一题的 gold 材料 id（B1 臂只带了它）。"""

    if not V4_ANSWERS.exists():
        return None
    answers = json.loads(V4_ANSWERS.read_text(encoding="utf-8"))
    record = answers.get(qid)
    if record is None:
        return None
    materials = record["arms"]["B1"]["materials"]
    return materials[0] if materials else None


async def phase_answers(store, retriever, packets_by_index, mapping, gateway,
                       reuse_limit: int, concurrency: int = CONCURRENCY) -> None:
    path = OUT / f"ablation_v5_answers_L{reuse_limit}.json"
    if path.exists():
        print(f"  复用已存的 {path.name}")
        return
    questions, gold = load_v3()
    semaphore = asyncio.Semaphore(concurrency)
    out: dict[str, dict] = {}

    async def one(qid: str, index: int, question: dict):
        async with semaphore:
            packet = packets_by_index[index]
            gold_list = list(gold.get(qid, {}).get("gold", []))
            hits = retriever.search(project_id=PROJECT, query=question["question"],
                                    limit=reuse_limit, owner=OWNER)
            material_ids = list(dict.fromkeys(hit.material_id for hit in hits))
            packets = []
            for material_id in material_ids:
                packet_found = store.get(material_id, owner=OWNER)
                if packet_found is not None:
                    packets.append(packet_found)
            arms = {
                "B1": arm_B1(packet, question["question"]),
                "C": arm_C(packets, question["question"]),
                "C_prime": arm_C_prime(packets, hits, question["question"]),
            }
            record: dict = {"question": question["question"], "points": question["points"],
                            "gold": gold_list, "arms": {}}
            for arm in ARMS:
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
                if arm == "C_prime":
                    record["arms"][arm]["scored_chunks"] = info["scored_chunks"]
                    record["arms"][arm]["used"] = info["used"]
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

    jobs = [
        (f"{mid_key}-{qi}", index, question)
        for index, (mid_key, qs) in sorted(questions.items())
        if index in packets_by_index
        for qi, question in enumerate(qs)
    ]
    await asyncio.gather(*(one(qid, index, question) for qid, index, question in jobs))
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  写入 {path.name}（{len(out)} 题）")


async def phase_judge(gateway, reuse_limit: int, concurrency: int = CONCURRENCY) -> None:
    answers = json.loads(
        (OUT / f"ablation_v5_answers_L{reuse_limit}.json").read_text(encoding="utf-8"))
    path = OUT / f"ablation_v5_judgments_L{reuse_limit}.json"
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
                    data, _tokens, _elapsed, error = await call_json(
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
    if not votes:
        return 0.0
    scores = []
    for vote in votes:
        points = vote.get("points", [])
        if not points:
            continue
        scores.append(sum(1 for item in points if item.get("covered")) / len(points))
    return sum(scores) / len(scores) if scores else 0.0


def cited_eids(answer: str) -> set:
    return {f"E{match.group(1) or match.group(2)}" for match in CITATION_RE.finditer(answer)}


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _paired(rows: list[dict], arm_x: str, arm_y: str) -> dict:
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


def phase_aggregate(reuse_limit: int, retrieval_path: dict) -> dict:
    answers = json.loads(
        (OUT / f"ablation_v5_answers_L{reuse_limit}.json").read_text(encoding="utf-8"))
    judgments = json.loads(
        (OUT / f"ablation_v5_judgments_L{reuse_limit}.json").read_text(encoding="utf-8"))
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
                "material_chars": info["chars"],
                "materials": len(info["materials"]),
                "picked": len(info["picked"]),
                "scored_chunks": info.get("scored_chunks"),
                "used": info.get("used"),
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

    # v4 的 C / B1 对照（旧尺子下测的）——只用于"尺子效果"的拆分。
    v4_metrics = None
    v4_path = V4 / "ablation_v4_results.json"
    if v4_path.exists():
        v4 = json.loads(v4_path.read_text(encoding="utf-8"))
        v4_metrics = {arm: v4["metrics"][arm] for arm in ("B1", "C")}

    verdict = _verdict_hypotheses(rows, metrics, v4_metrics, reuse_limit)
    result = {"检索路": retrieval_path, "reuse_limit": reuse_limit,
              "metrics": metrics, "v4对照": v4_metrics, "verdict": verdict, "rows": rows}
    (OUT / f"ablation_v5_results_L{reuse_limit}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def _verdict_hypotheses(rows: list[dict], metrics: dict, v4_metrics, reuse_limit: int) -> dict:
    out: dict[str, dict] = {}

    h2 = _paired(rows, "B1", "C_prime")
    out["H2_prime"] = {
        "判据": f"C' ≥ B1 − ε（ε={EPSILON}）：收敛后不输给「用户恰好勾对」"
                f"（本方案的验收条件）· reuse_limit={reuse_limit}",
        "C_prime-B1": round(metrics["C_prime"]["正确率"] - metrics["B1"]["正确率"], 4),
        "配对": h2,
        "中": metrics["C_prime"]["正确率"] - metrics["B1"]["正确率"] >= -EPSILON,
    }

    h3 = _paired(rows, "C", "C_prime")
    out["H3_prime"] = {
        "判据": "C' 的正确率明显高于 C（配对改善 > 倒退 且 均值差 ≥ 0.10）"
                "——收敛本身的增量",
        "C_prime-C": round(metrics["C_prime"]["正确率"] - metrics["C"]["正确率"], 4),
        "配对": h3,
        "中": (metrics["C_prime"]["正确率"] - metrics["C"]["正确率"] >= CLEAR_GAP
               and h3["y_better"] > h3["x_better"]),
    }

    v4_c = (v4_metrics or {}).get("C", {}).get("正确率")
    v4_b1 = (v4_metrics or {}).get("B1", {}).get("正确率")
    out["H4_prime"] = {
        "判据": "C' 与 v4 的 C 的差 = 尺子 + 收敛 合计的效果；C 与 v4 的 C 的差 = 尺子单独的效果",
        "C_prime减v4C": round(metrics["C_prime"]["正确率"] - v4_c, 4) if v4_c is not None else None,
        "C减v4C": round(metrics["C"]["正确率"] - v4_c, 4) if v4_c is not None else None,
        "B1减v4B1": round(metrics["B1"]["正确率"] - v4_b1, 4) if v4_b1 is not None else None,
        "tokens": {arm: metrics[arm]["tokens"] for arm in ARMS},
        "延迟ms": {arm: round(metrics[arm]["延迟ms"]) for arm in ARMS},
        "材料字数": {arm: round(metrics[arm]["材料字数"]) for arm in ARMS},
        "v4_tokens": {arm: (v4_metrics or {}).get(arm, {}).get("tokens") for arm in ("B1", "C")},
    }
    return out


def show(result: dict) -> None:
    metrics, verdict = result["metrics"], result["verdict"]
    v4 = result.get("v4对照") or {}
    print(f"\n=== C' 实际检索路：{result['检索路']} · reuse_limit={result['reuse_limit']} ===")
    header = (f"{'臂':<8}{'正确率':>8}{'全对率':>8}{'引用命中':>9}{'引用精度':>9}"
              f"{'gold选中':>9}{'材料份':>7}{'tokens':>10}{'延迟ms':>10}{'失败':>6}")
    print(header)
    for arm in ARMS:
        m = metrics[arm]
        print(f"{arm:<8}{m['正确率']:>8.3f}{m['全对率']:>8.3f}{m['引用命中']:>9.3f}"
              f"{m['引用精度']:>9.3f}{m['gold选中数']:>9.2f}{m['材料份数']:>7.2f}"
              f"{m['tokens']:>10}{m['延迟ms']:>10.0f}{m['失败']:>6}")
    print("\n--- v4 对照（旧尺子下测的）---")
    for arm, m in v4.items():
        print(f"{arm:<8}{m['正确率']:>8.3f}{'':>8}{'':>9}{'':>9}"
              f"{m['gold选中数']:>9.2f}{m['材料份数']:>7.2f}{m['tokens']:>10}")
    print("\n=== 预注册判据 ===")
    for key in ("H2_prime", "H3_prime", "H4_prime"):
        item = verdict[key]
        mark = item.get("中", "")
        print(f"  {key}: {item['判据']}")
        detail = {k: v for k, v in item.items() if k != "判据"}
        print(
            f"      {json.dumps(detail, ensure_ascii=False)}"
            + (f"  → {'中' if mark else '不中'}" if isinstance(mark, bool) else "")
        )


def probe_retrieval_path(connection, embedder, *, probe_embedder: bool) -> tuple[dict, object]:
    """照实报 C' 实际走的检索路，并**返回真正可用的 embedder**。

    **为什么必须真测一次**：`FastEmbedEmbedder` 是**懒加载**的——`bool(embedder)` 恒为真，
    但模型可能根本载不进来（本沙箱连不上 HuggingFace）。而 `_ensure()` 失败时
    **不会缓存失败**，于是**每次 `search` 都重试一遍下载**（一次 ~90s）——
    照那么跑，120 题要几小时。所以这里**先真测一次**，载不进来就**降级成 `None`**
    （检索器内部会退回纯 FTS5），并把降级**如实写进结果**。
    """

    loaded = hasattr(connection, "enable_load_extension")
    rows = 0
    try:
        rows = int(connection.execute(
            "SELECT COUNT(*) FROM material_chunks_vec").fetchone()[0])
    except Exception:  # noqa: BLE001 —— 表不在就是没向量
        rows = 0
    usable = None
    # **默认不试**：本沙箱连不上 HuggingFace，而 `TextEmbedding(...)` 的下载重试
    # **没有超时**（会挂住几分钟甚至更久）。要试就显式传 `--probe-embedder`。
    if probe_embedder and embedder is not None and rows > 0:
        try:
            embedder.embed_many(["探活"])
            usable = embedder
        except Exception as error:  # noqa: BLE001 —— 载不进来就降级，照实记
            print(f"  ⚠ embedder 不可用（{type(error).__name__}）→ 退回纯 FTS5")
    path = {"扩展已载入": loaded, "向量行数": rows,
            "embedder可用": usable is not None,
            "实际检索路": "FTS5+向量" if usable is not None else "纯FTS5"}
    return path, usable


async def main() -> None:
    args = sys.argv[1:]
    phases: list[str] = []
    reuse_limit = DEFAULT_REUSE_LIMIT
    concurrency = CONCURRENCY
    probe_embedder = False
    index = 0
    while index < len(args):
        item = args[index]
        if item == "--reuse-limit" and index + 1 < len(args):
            reuse_limit = int(args[index + 1])
            index += 2
            continue
        if item == "--concurrency" and index + 1 < len(args):
            concurrency = int(args[index + 1])
            index += 2
            continue
        if item == "--probe-embedder":
            probe_embedder = True
            index += 1
            continue
        if not item.startswith("--"):
            phases.append(item)
        index += 1

    OUT.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        raise SystemExit(f"先跑 v4 建库：{DB_PATH}")
    mapping = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    connection = connect(DB_PATH, embedder=None)
    store = SqliteMaterialStore(connection, ids=UuidIdGenerator())
    retrieval_path, usable_embedder = probe_retrieval_path(
        connection, FastEmbedEmbedder(), probe_embedder=probe_embedder)
    retriever = SqliteHybridRetriever(connection, embedder=usable_embedder)
    print(f"  检索路：{retrieval_path}")

    packets_by_index = {
        int(index): store.get(material_id, owner=OWNER)
        for index, material_id in mapping.items()
    }
    packets_by_index = {i: p for i, p in packets_by_index.items() if p is not None}
    print(f"库 {len(packets_by_index)} 份 · 检索宽度 {reuse_limit} · 预算 {MATERIAL_BUDGET} 字")
    gateway = build_gateway(concurrency)

    if not phases or "answers" in phases:
        print("\n=== phase · 三臂答题 ===")
        await phase_answers(store, retriever, packets_by_index, mapping, gateway,
                            reuse_limit, concurrency)
    if not phases or "judge" in phases:
        print("\n=== phase · 正确性裁判 ===")
        await phase_judge(gateway, reuse_limit, concurrency)
    if not phases or "aggregate" in phases:
        print("\n=== phase · 汇总 ===")
        show(phase_aggregate(reuse_limit, retrieval_path))


if __name__ == "__main__":
    asyncio.run(main())
