#!/usr/bin/env python3
"""融合策略对比台：把词法路与语义路拆开，量几种融合方式的结果。

**为什么要有这个**（2026-10-19）：批 5 的 `reciprocal_rank_fusion` 实测让**词法召回
倒退**（逐字子串 top1 30→24、recall@5 40→36），而计划预注册的判据是"逐字与换序
两类逐档不低于 v2"。所以先量清楚**倒退的机制**，再决定改哪一处。

**量三样东西**（每一类查询各 40 条）：
1. 词法路的命中数——如果改述类词法几乎不命中，那"语义补位"就能进 top-5；
2. 每个候选的**词面覆盖率** `cov = |query token ∩ 块 token| / |query token|`——
   它区分"用户的话真在这块里"与"碰巧共享一两个词"；
3. 几种融合方式各自的 top1 / recall@5。

**跑法**（与 `vector_recall_bench.py` 同一份缓存语料）：

```bash
cd backend && HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \\
  set -a && . ./.env && set +a && \\
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/vector_fusion_lab.py
```
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(BACKEND))

from pmstudio.infrastructure.sqlite import database  # noqa: E402
from pmstudio.infrastructure.sqlite.materials import (  # noqa: E402
    SqliteFtsRetriever,
    SqliteMaterialStore,
)
from pmstudio.infrastructure.sqlite.owner_scope import set_owner_scope  # noqa: E402
from pmstudio.infrastructure.sqlite.tokenize import query_tokens  # noqa: E402

OWNER = "lab-owner"
PROJECT = "p-lab"
PER_CLASS = 40


def _log(text: str) -> None:
    print(text, flush=True)


class _SeqIds:
    def __init__(self) -> None:
        self._n = 0

    def new(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n:04d}"


def _load_env() -> None:
    for line in (BACKEND / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _bigrams(text: str) -> set[str]:
    kept = "".join(ch for ch in text if ch.isalnum())
    return {kept[i : i + 2] for i in range(len(kept) - 1)}


def _knn_hits(connection, embedder, query: str, limit: int) -> list[tuple[str, float]]:
    """语义路：KNN 命中（按距离），返回 [(material_id, 语义分)]。

    语义分用**名次归一化**（rank1 → 1.0，rankN → 1/N），不直接用距离：
    距离的绝对值随语料/模型变，名次归一化后跨语料可比。
    """
    from sqlite_vec import serialize_float32

    try:
        vector = embedder.embed_many([query])[0]
        rows = connection.execute(
            "SELECT chunk_id FROM ("
            " SELECT chunk_id FROM material_chunks_vec"
            " WHERE embedding MATCH ? ORDER BY distance LIMIT ?"
            ") WHERE chunk_id IN ("
            " SELECT c.chunk_id FROM material_chunks c"
            " JOIN materials m ON m.material_id = c.material_id"
            " WHERE m.project_id = ? AND c.owner_id = ?)",
            (serialize_float32(list(vector)), limit, PROJECT, OWNER),
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    if not rows:
        return []
    chunk_ids = [str(r["chunk_id"]) for r in rows]
    placeholders = ",".join("?" for _ in chunk_ids)
    mapping = connection.execute(
        f"SELECT chunk_id, material_id FROM material_chunks WHERE chunk_id IN ({placeholders})",
        chunk_ids,
    ).fetchall()
    by_chunk = {str(r["chunk_id"]): str(r["material_id"]) for r in mapping}
    total = max(1, len(chunk_ids))
    return [
        (by_chunk[c], 1.0 - index / total)
        for index, c in enumerate(chunk_ids)
        if c in by_chunk
    ]


def _sem_rank(material_id: str, vec_pairs: list[tuple[str, float]]) -> float:
    """语义名次倒数：vec#1 → 1.0，vec#N → 1/N（比线性归一化陡）。"""
    for index, (mid, _) in enumerate(vec_pairs, start=1):
        if mid == material_id:
            return 1.0 / index
    return 0.0


def _coverage(connection, material_id: str, tokens: tuple[str, ...]) -> float:
    """候选块对 query 的**词面覆盖率**。"""
    row = connection.execute(
        "SELECT m.title, c.text FROM material_chunks c"
        " JOIN materials m ON m.material_id = c.material_id"
        " WHERE c.material_id = ? LIMIT 1",
        (material_id,),
    ).fetchone()
    if row is None or not tokens:
        return 0.0
    doc_tokens = set(query_tokens(f"{row['title']} {row['text']}"))
    return len(set(tokens) & doc_tokens) / len(set(tokens))


def _rrf(lists: list[list[str]], k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    first: dict[str, int] = {}
    for column in lists:
        for rank, item in enumerate(column, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            first.setdefault(item, len(first))
    return sorted(scores, key=lambda item: (-scores[item], first[item]))


def main() -> int:
    _load_env()
    set_owner_scope(__import__("pmstudio.domain.shared.ids", fromlist=["UserId"]).UserId(OWNER))

    corpus_path = BACKEND.parent / ".codex" / "runs" / "2026-10-19" / "bench_corpus.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    _log(f"语料：{len(corpus)} 条（缓存）")

    db = os.environ.get("PMSTUDIO_DB") or str(BACKEND / "data" / "pmstudio.db")
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    real = [(str(r["title"]), str(r["detail"])) for r in src.execute(
        "SELECT title, detail FROM observations")]
    src.close()

    import hashlib
    import re

    from pmstudio.application.dto import MaterialChunk, ParsedMaterial
    from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
    from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex

    with tempfile.TemporaryDirectory() as tmp:
        connection = database.connect(Path(tmp) / "lab.db")
        store = SqliteMaterialStore(connection, ids=_SeqIds())
        blocks = list(real) + [(c["title"], c["text"]) for c in corpus]
        ids: dict[str, str] = {}
        for index, (title, text) in enumerate(blocks):
            parsed = ParsedMaterial(
                origin="upload", title=title, source_ref=f"attachment:lab-{index}",
                media_type="text/markdown", bytes_len=1,
                chunks=(MaterialChunk(chunk_id="local#c00", ordinal=0, text=text,
                                      heading_path=(), anchor="第 1 段"),),
                parser_id="local-text", parser_version="v1",
            )
            ids[f"{index:04d}"] = store.put(
                project_id=PROJECT, parsed=parsed,
                sha256=hashlib.sha256(f"lab-{index}".encode()).hexdigest(),
                content=b"x", now=datetime.now(UTC),
            ).material_id
        embedder = FastEmbedEmbedder()
        vi = SqliteVectorIndex(connection, embedder=embedder)
        for mid in ids.values():
            vi.write(mid)
        _log(f"灌库 {len(blocks)} 份，向量 {connection.execute('SELECT count(*) FROM material_chunks_vec').fetchone()[0]} 条")

        # ── 造查询 ────────────────────────────────────────────────
        cases: dict[str, list[tuple[str, str]]] = {
            "逐字子串": [], "同词换序": [], "同义改述": [],
        }
        for index in range(min(PER_CLASS, len(real))):
            title, text = real[index]
            for part in re.split(r"[。，、；：（）【】?？!！\s]+", text):
                part = re.sub(r"[A-Za-z0-9_./·—\-]+", "", part)
                if 8 <= len(part) <= 14:
                    break
            else:
                continue
            cases["逐字子串"].append((part, f"{index:04d}"))
            cases["同词换序"].append(("".join(reversed(query_tokens(part))), f"{index:04d}"))
        for index, item in enumerate(corpus):
            if len(cases["同义改述"]) >= PER_CLASS:
                break
            indexed = f"{item['title']}{item['text']}"
            if len(_bigrams(item["paraphrase"]) & _bigrams(indexed)) > 1:
                continue
            cases["同义改述"].append((item["paraphrase"], f"{len(real) + index:04d}"))
        for kind, items in cases.items():
            _log(f"  {kind}: {len(items)} 条")

        fts = SqliteFtsRetriever(connection)

        # ── 逐条量 ────────────────────────────────────────────────
        fts_counts: dict[str, list[int]] = {k: [] for k in cases}
        cov_target: dict[str, list[float]] = {k: [] for k in cases}
        variant_names = (
            "v2", "RRF全量", "RRF语义top10", "覆盖率先",
            "互补cov+(1-cov)sem", "cov+sem", "满覆盖优先",
            "满覆盖闸门+语义", "纯语义", "满覆盖闸门+RRF5",
            "满覆盖闸门+语义倒数", "满覆盖闸门+语义倒数+词法破平",
            "半覆盖闸门+语义", "三档闸门+语义",
        )
        results: dict[str, dict[str, list[tuple[bool, bool]]]] = {
            kind: {v: [] for v in variant_names} for kind in cases
        }
        for kind, items in cases.items():
            for query, key in items:
                target = ids[key]
                tokens = query_tokens(query)
                fts_ids = [h.material_id for h in fts.search(
                    project_id=PROJECT, query=query, limit=20, owner=OWNER)]
                vec_pairs = _knn_hits(connection, embedder, query, 200)
                vec_ids = [mid for mid, _ in vec_pairs]
                sem = {mid: score for mid, score in vec_pairs}
                fts_counts[kind].append(len(fts_ids))
                cov_target[kind].append(_coverage(connection, target, tokens))
                universe = set(fts_ids) | set(vec_ids[:20])
                rrf20 = _rrf([fts_ids, vec_ids[:20]])

                def _cov(mid: str) -> float:
                    return _coverage(connection, mid, tokens)

                variants = {
                    "v2": fts_ids,
                    "RRF全量": _rrf([fts_ids, vec_ids]),
                    "RRF语义top10": _rrf([fts_ids, vec_ids[:10]]),
                    "覆盖率先": sorted(
                        universe,
                        key=lambda mid: (-_cov(mid), rrf20.index(mid)),
                    ),
                    # 互补加权：词面已经说清楚的不再吃语义加成，词面说不了的由语义顶上
                    "互补cov+(1-cov)sem": sorted(
                        universe,
                        key=lambda mid: -(
                            _cov(mid) + (1 - _cov(mid)) * sem.get(mid, 0.0)
                        ),
                    ),
                    # 纯相加（对照）
                    "cov+sem": sorted(
                        universe,
                        key=lambda mid: -(_cov(mid) + sem.get(mid, 0.0)),
                    ),
                    # 分层：词面满覆盖的单独一层（用户的话原样在这块里），层内按 RRF
                    "满覆盖优先": sorted(
                        universe,
                        key=lambda mid: (
                            0 if _cov(mid) >= 0.999 else 1,
                            rrf20.index(mid),
                        ),
                    ),
                    # 满覆盖**做闸门**（满覆盖的一层，其余一层），层内按语义分排
                    "满覆盖闸门+语义": sorted(
                        universe,
                        key=lambda mid: (
                            0 if _cov(mid) >= 0.999 else 1,
                            -sem.get(mid, 0.0),
                        ),
                    ),
                    # 纯语义排序（对照：完全不管词面）
                    "纯语义": sorted(
                        universe, key=lambda mid: -sem.get(mid, 0.0)
                    ),
                    # 满覆盖闸门 + 层内 RRF，但 RRF 的语义列表只看 top-5
                    "满覆盖闸门+RRF5": sorted(
                        universe,
                        key=lambda mid: (
                            0 if _cov(mid) >= 0.999 else 1,
                            _rrf([fts_ids, vec_ids[:5]]).index(mid)
                            if mid in set(fts_ids) | set(vec_ids[:5]) else 999,
                        ),
                    ),
                    # 语义分改成**名次倒数**（比线性归一化更陡：vec#1 远高于 vec#10）
                    "满覆盖闸门+语义倒数": sorted(
                        universe,
                        key=lambda mid: (
                            0 if _cov(mid) >= 0.999 else 1,
                            -_sem_rank(mid, vec_pairs),
                        ),
                    ),
                    # 满覆盖闸门 + 语义倒数 + 词法名次破平
                    "满覆盖闸门+语义倒数+词法破平": sorted(
                        universe,
                        key=lambda mid: (
                            0 if _cov(mid) >= 0.999 else 1,
                            -_sem_rank(mid, vec_pairs),
                            fts_ids.index(mid) if mid in fts_ids else 999,
                        ),
                    ),
                    # 闸门放宽到"词面命中数 ≥ 半数 token"（0.5 闸门）
                    "半覆盖闸门+语义": sorted(
                        universe,
                        key=lambda mid: (
                            0 if _cov(mid) >= 0.5 else 1,
                            -sem.get(mid, 0.0),
                        ),
                    ),
                    # 两闸门：满覆盖 > 半覆盖 > 无覆盖，层内按语义
                    "三档闸门+语义": sorted(
                        universe,
                        key=lambda mid: (
                            0 if _cov(mid) >= 0.999 else (1 if _cov(mid) >= 0.5 else 2),
                            -sem.get(mid, 0.0),
                        ),
                    ),
                }
                for name, ranked in variants.items():
                    hit1 = bool(ranked) and ranked[0] == target
                    hit5 = target in ranked[:5]
                    results[kind][name].append((hit1, hit5))
            _log(f"  [{kind}] 量完")

        # ── 出表 ──────────────────────────────────────────────────
        _log("")
        variants = variant_names
        header = (
            "| 查询类型 | 词法命中数(中位) | 目标覆盖率(中位) | "
            + " | ".join(f"{v} top1/r@5" for v in variants)
            + " |"
        )
        _log(header)
        _log("|---|---|---|" + "---|" * len(variants))
        for kind in cases:
            counts = sorted(fts_counts[kind])
            covs = sorted(cov_target[kind])
            cells = []
            for name in variants:
                rows = results[kind][name]
                cells.append(f"{sum(r[0] for r in rows)}/{sum(r[1] for r in rows)}")
            _log(
                f"| {kind} | {counts[len(counts)//2]} | {covs[len(covs)//2]:.2f} | "
                + " | ".join(cells) + " |"
            )
        _log("")
        for kind in cases:
            counts = sorted(fts_counts[kind])
            _log(f"  {kind}: 词法命中数 中位 {counts[len(counts)//2]}、最大 {counts[-1]}、"
                 f"零命中 {sum(1 for c in counts if c == 0)} 条")
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
