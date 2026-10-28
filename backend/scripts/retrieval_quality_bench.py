"""冻结语料的检索质量基准：真 PostgreSQL + pg_search + 真向量，不碰 SQLite。

口径与生产一致——直接构造生产实现类（`SqliteMaterialStore` / `SqliteVectorIndex` /
`SqliteHybridRetriever`），不是另写一份检索逻辑：

- 词法：`search_text ||| pmstudio_tokens(query)` 取 200 候选 → `pdb.score` 排序 →
  全语料 Robertson BM25 重排（IDF 按生产钳到 0）→ 前 50 进融合；
- 向量：生产 `FastEmbedEmbedder`（`BAAI/bge-small-zh-v1.5`），联网材料按
  `WEB_VECTOR_CHARS=9600` 字预算只嵌前若干块；
- 融合：`α=.4` 凸组合、每材料 2 槽、`limit=5`。

默认读仓库内冻结语料 `tests/fixtures/retrieval/`（15 份联网正文 + 45 词面查询 +
14 改述 + 15 跨块 + 8 负样本）；`--corpus` 可指到别的目录或 `corpus.json`。
脚本在临时 schema 里跑，退出时销毁；`--database-url` 不给时按 `scripts/pg_scratch.py`
的同一套 `.env` / `.env.ops` 取 URL。

用法：

    cd backend
    uv run python scripts/retrieval_quality_bench.py --chunk-chars 300
    uv run python scripts/retrieval_quality_bench.py --chunk-chars 400 --json /tmp/r400.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SRC = BACKEND_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.pg_scratch import drop_scratch, make_scratch  # noqa: E402

from pmstudio.application.dto import MaterialHit, ParsedMaterial  # noqa: E402
from pmstudio.bootstrap.container import UuidIdGenerator  # noqa: E402
from pmstudio.domain.shared.ids import ProjectId, UserId  # noqa: E402
from pmstudio.infrastructure.postgres.database import connect  # noqa: E402
from pmstudio.infrastructure.postgres.embedding import FastEmbedEmbedder  # noqa: E402
from pmstudio.infrastructure.postgres.materials import (  # noqa: E402
    SqliteHybridRetriever,
    SqliteMaterialStore,
)
from pmstudio.infrastructure.postgres.vector_index import SqliteVectorIndex  # noqa: E402
from pmstudio.infrastructure.tools.extract import chunk_text  # noqa: E402
from pmstudio.infrastructure.tools.webclean import normalize_web_text  # noqa: E402

DEFAULT_FIXTURE = BACKEND_ROOT / "tests" / "fixtures" / "retrieval"
OWNER = UserId("u-retrieval-bench")
PROJECT = ProjectId("p-retrieval-bench")
CREATED_AT = datetime(2026, 10, 19, tzinfo=UTC)
LIMIT = 5

#: 生产融合上限（日志里能看到的候选数；真实 pg_search 候选深度是 200）。
_MATERIAL_OBSERVATION = re.compile(
    r"material_search .*?fts_candidates=(?P<fts_candidates>\d+)"
    r" vec_candidates=(?P<vec_candidates>\d+)"
    r" fts_hits=(?P<fts_hits>\d+) hybrid_hits=(?P<hybrid_hits>\d+)"
)


@dataclass(frozen=True, slots=True)
class Corpus:
    documents: list[dict[str, Any]]
    materials: dict[int, str]
    chunk_ids: dict[str, tuple[str, ...]]
    chunk_texts: dict[str, tuple[str, ...]]

    @property
    def chunk_count(self) -> int:
        return sum(len(ids) for ids in self.chunk_ids.values())


@dataclass(frozen=True, slots=True)
class SearchObservation:
    hits: tuple[MaterialHit, ...]
    elapsed_ms: float
    fts_candidates: int
    vec_candidates: int
    fts_hits: int
    hybrid_hits: int


class _ObservationHandler(logging.Handler):
    """收生产检索器自己打的 `material_search` 分解行（不另算一份候选）。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith("material_search "):
            self.lines.append(message)

    def take(self) -> dict[str, int]:
        if not self.lines:
            return {
                "fts_candidates": 0,
                "vec_candidates": 0,
                "fts_hits": 0,
                "hybrid_hits": 0,
            }
        match = _MATERIAL_OBSERVATION.search(self.lines[-1])
        self.lines.clear()
        if match is None:
            return {
                "fts_candidates": 0,
                "vec_candidates": 0,
                "fts_hits": 0,
                "hybrid_hits": 0,
            }
        return {name: int(value) for name, value in match.groupdict().items()}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_paths(corpus: Path) -> tuple[Path, Path]:
    if corpus.is_dir():
        return corpus / "corpus.json", corpus
    return corpus, corpus.parent


def _documents(path: Path) -> list[dict[str, Any]]:
    payload = _load_json(path)
    if isinstance(payload, dict):
        payload = payload.get("items") or payload.get("documents")
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"{path} 不是非空的材料列表")
    for index, item in enumerate(payload):
        missing = {"title", "url", "text"} - set(item)
        if missing:
            raise ValueError(f"{path} 第 {index} 份缺字段：{sorted(missing)}")
    return payload


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[position]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _query_tokens(connection, query: str) -> list[str]:  # type: ignore[no-untyped-def]
    row = connection.execute("SELECT pmstudio_tokens(%s) AS tokens", (query,)).fetchone()
    if row is None:
        return []
    return [str(token) for token in row["tokens"]]


def _ingest(
    connection,  # type: ignore[no-untyped-def]
    documents: list[dict[str, Any]],
    *,
    chunk_chars: int,
    embedder: FastEmbedEmbedder,
) -> Corpus:
    store = SqliteMaterialStore(connection, ids=UuidIdGenerator())
    vector_index = SqliteVectorIndex(connection, embedder=embedder)
    materials: dict[int, str] = {}
    chunk_ids: dict[str, tuple[str, ...]] = {}
    chunk_texts: dict[str, tuple[str, ...]] = {}
    for index, document in enumerate(documents):
        clean = normalize_web_text(str(document["text"]))
        chunks = chunk_text(clean, parser_id="tavily", max_chars=chunk_chars)
        content = str(document["text"]).encode("utf-8")
        parsed = ParsedMaterial(
            origin="web_search",
            title=str(document["title"]),
            source_ref=str(document["url"]),
            media_type="text/html",
            bytes_len=len(content),
            chunks=chunks,
            parser_id="tavily",
            parser_version="bench",
        )
        packet = store.put(
            project_id=PROJECT,
            parsed=parsed,
            sha256=hashlib.sha256(content).hexdigest(),
            content=content,
            now=CREATED_AT,
            owner=OWNER,
        )
        material_id = packet.material_id
        if material_id in chunk_ids:
            raise ValueError(f"语料有重复材料（sha256 撞车）：{document['url']}")
        rows = connection.execute(
            "SELECT chunk_id, text FROM material_chunks"
            " WHERE material_id = %s ORDER BY ordinal",
            (material_id,),
        ).fetchall()
        materials[index] = material_id
        chunk_ids[material_id] = tuple(str(row["chunk_id"]) for row in rows)
        chunk_texts[material_id] = tuple(str(row["text"]) for row in rows)
        vector_index.write(material_id)
    return Corpus(
        documents=documents,
        materials=materials,
        chunk_ids=chunk_ids,
        chunk_texts=chunk_texts,
    )


def _cross_queries(
    connection,  # type: ignore[no-untyped-def]
    corpus: Corpus,
    *,
    chunk_chars: int,
    path: Path,
) -> list[dict[str, Any]]:
    """跨块查询：优先读冻结集；自定义语料没有冻结集时按同一规则现生成。"""

    frozen = _load_json(path) if path.is_file() else {}
    group = frozen.get(str(chunk_chars)) if isinstance(frozen, dict) else None
    if isinstance(group, list) and group:
        rows = []
        for item in group:
            index = int(item["material_index"])
            material_id = corpus.materials.get(index)
            if material_id is None:
                continue
            ids = corpus.chunk_ids[material_id]
            wanted = [f"{material_id}#c{int(ordinal):02d}" for ordinal in item["chunks"]]
            if any(chunk_id not in ids for chunk_id in wanted):
                continue
            rows.append(
                {
                    "query": str(item["query"]),
                    "material_index": index,
                    "wanted": wanted,
                }
            )
        if len(rows) == len(group):
            return rows

    # 自定义语料：chunks[6] / chunks[10] 各取一个语料稀有词；词面按 pg_search
    # jieba（与生产同一把尺子），限制在材料级词频里出现 < N/2 的词。
    token_sets: dict[str, set[str]] = {}
    for material_id, ids in corpus.chunk_ids.items():
        tokens: set[str] = set()
        for chunk_id in ids:
            row = connection.execute(
                "SELECT search_tokens FROM material_chunks WHERE chunk_id = %s",
                (chunk_id,),
            ).fetchone()
            if row is not None:
                tokens.update(str(token) for token in row["search_tokens"])
        token_sets[material_id] = tokens
    frequency: dict[str, int] = {}
    for tokens in token_sets.values():
        for token in tokens:
            frequency[token] = frequency.get(token, 0) + 1
    common = {token for token, count in frequency.items() if count >= len(token_sets) / 2}
    rows = []
    for index, material_id in sorted(corpus.materials.items()):
        ids = corpus.chunk_ids[material_id]
        if len(ids) < 11:
            continue
        first = [
            token
            for token in _query_tokens(connection, corpus.chunk_texts[material_id][6])
            if token not in common
        ]
        second = [
            token
            for token in _query_tokens(connection, corpus.chunk_texts[material_id][10])
            if token not in common
        ]
        if not first or not second:
            continue
        first.sort(key=lambda token: frequency.get(token, 0))
        second.sort(key=lambda token: frequency.get(token, 0))
        rows.append(
            {
                "query": f"{first[0]} {second[0]}",
                "material_index": index,
                "wanted": [
                    f"{material_id}#c06",
                    f"{material_id}#c10",
                ],
            }
        )
    return rows


def _search(
    retriever: SqliteHybridRetriever,
    query: str,
    *,
    observations: _ObservationHandler,
) -> SearchObservation:
    observations.lines.clear()
    started = time.perf_counter()
    hits = retriever.search(project_id=PROJECT, query=query, limit=LIMIT, owner=OWNER)
    elapsed = (time.perf_counter() - started) * 1000
    counts = observations.take()
    return SearchObservation(hits=hits, elapsed_ms=elapsed, **counts)


def _evaluate(
    connection,  # type: ignore[no-untyped-def]
    corpus: Corpus,
    *,
    chunk_chars: int,
    queries_dir: Path,
    chunk_queries_path: Path,
    retriever: SqliteHybridRetriever,
    observations: _ObservationHandler,
) -> dict[str, Any]:
    frozen = _load_json(queries_dir / "frozen_queries.json")
    paraphrases = _load_json(queries_dir / "paraphrases.json")["paraphrases"]
    negatives = _load_json(queries_dir / "negative_queries.json")
    cross = _cross_queries(
        connection,
        corpus,
        chunk_chars=chunk_chars,
        path=chunk_queries_path,
    )

    url_to_index = {
        str(document["url"]): index for index, document in enumerate(corpus.documents)
    }
    latencies: list[float] = []
    candidate_rows: list[dict[str, int]] = []

    def run(query: str) -> tuple[SearchObservation, list[MaterialHit]]:
        result = _search(retriever, query, observations=observations)
        latencies.append(result.elapsed_ms)
        candidate_rows.append(
            {
                "fts_candidates": result.fts_candidates,
                "vec_candidates": result.vec_candidates,
                "fts_hits": result.fts_hits,
                "hybrid_hits": result.hybrid_hits,
            }
        )
        return result, list(result.hits)

    def lexical_group(items: list[Any]) -> dict[str, float]:
        top1: list[float] = []
        recalled: list[float] = []
        reciprocal: list[float] = []
        block_recalled: list[float] = []
        for word, _query, url in items:
            index = url_to_index.get(str(url))
            if index is None:
                continue
            material_id = corpus.materials.get(index)
            if material_id is None:
                continue
            # 词面判据的查询就是**取自目标材料中段的那个词**，不是第三列之外的
            # 概括问句（旧基线 `retrieval_baseline.json` 同口径）。
            _, hits = run(str(word))
            hit_ids = [hit.material_id for hit in hits]
            found = material_id in hit_ids
            top1.append(1.0 if hit_ids and hit_ids[0] == material_id else 0.0)
            recalled.append(1.0 if found else 0.0)
            reciprocal.append(1.0 / (hit_ids.index(material_id) + 1) if found else 0.0)
            wanted = {
                chunk_id
                for chunk_id, text in zip(
                    corpus.chunk_ids[material_id],
                    corpus.chunk_texts[material_id],
                    strict=True,
                )
                if str(word) and str(word) in text
            }
            if wanted:
                block_recalled.append(
                    1.0 if any(hit.chunk_id in wanted for hit in hits) else 0.0
                )
        return {
            "n": len(recalled),
            "top1": _mean(top1),
            "r5": _mean(recalled),
            "block_r5": _mean(block_recalled),
            "block_n": len(block_recalled),
            "mrr": _mean(reciprocal),
        }

    def paraphrase_group(items: dict[str, list[str]]) -> dict[str, float]:
        top1: list[float] = []
        recalled: list[float] = []
        reciprocal: list[float] = []
        for index_text, group in sorted(items.items(), key=lambda item: int(item[0])):
            material_id = corpus.materials.get(int(index_text))
            if material_id is None:
                continue
            for query in group:
                _, hits = run(str(query))
                hit_ids = [hit.material_id for hit in hits]
                found = material_id in hit_ids
                top1.append(1.0 if hit_ids and hit_ids[0] == material_id else 0.0)
                recalled.append(1.0 if found else 0.0)
                reciprocal.append(
                    1.0 / (hit_ids.index(material_id) + 1) if found else 0.0
                )
        return {
            "n": len(recalled),
            "top1": _mean(top1),
            "r5": _mean(recalled),
            "mrr": _mean(reciprocal),
        }

    lexical = lexical_group(frozen["items"])
    paraphrase = paraphrase_group(paraphrases)

    cross_detail = []
    cross_complete = 0
    for item in cross:
        result, hits = run(str(item["query"]))
        hit_ids = [hit.chunk_id for hit in hits]
        ranks = {
            chunk_id: (hit_ids.index(chunk_id) + 1 if chunk_id in hit_ids else None)
            for chunk_id in item["wanted"]
        }
        complete = all(chunk_id in hit_ids for chunk_id in item["wanted"])
        cross_complete += int(complete)
        cross_detail.append(
            {
                "query": item["query"],
                "material_index": item["material_index"],
                "wanted": item["wanted"],
                "hits": hit_ids,
                "ranks": ranks,
                "complete": complete,
                "fts_candidates": result.fts_candidates,
                "vec_candidates": result.vec_candidates,
            }
        )

    false_positives = 0
    negative_detail = []
    for query in negatives:
        _, hits = run(str(query))
        if hits:
            false_positives += 1
        negative_detail.append({"query": str(query), "hits": len(hits)})

    return {
        "chunk_chars": chunk_chars,
        "materials": len(corpus.materials),
        "chunks": corpus.chunk_count,
        "queries": {
            "lexical": lexical["n"],
            "paraphrase": paraphrase["n"],
            "cross": len(cross),
            "negative": len(negatives),
        },
        "metrics": {
            "lexical": lexical,
            "paraphrase": paraphrase,
            "cross_block": {
                "complete": cross_complete,
                "n": len(cross),
                "rate": cross_complete / len(cross) if cross else 0.0,
            },
            "negative": {
                "false_positives": false_positives,
                "n": len(negatives),
                "rate": false_positives / len(negatives) if negatives else 0.0,
            },
            "candidates": {
                "fts_mean": _mean([row["fts_candidates"] for row in candidate_rows]),
                "vec_mean": _mean([row["vec_candidates"] for row in candidate_rows]),
                "fts_hits_mean": _mean([row["fts_hits"] for row in candidate_rows]),
                "hybrid_hits_mean": _mean([row["hybrid_hits"] for row in candidate_rows]),
            },
            "latency_ms": {
                "mean": _mean(latencies),
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
                "max": max(latencies) if latencies else 0.0,
                "n": len(latencies),
            },
        },
        "cross_detail": cross_detail,
        "negative_detail": negative_detail,
    }


@contextmanager
def _scratch(database_url: str | None) -> Iterator[Any]:
    if database_url:
        database = make_scratch(
            prefix="retrieval_bench",
            env={
                "PMSTUDIO_DATABASE_URL": database_url,
                "PMSTUDIO_DIRECT_DATABASE_URL": database_url,
            },
            admin_user="retrieval-bench",
            admin_password="retrieval-bench",
        )
    else:
        database = make_scratch(prefix="retrieval_bench")
    try:
        yield database
    finally:
        drop_scratch(database, direct_base=database_url)


def run(args: argparse.Namespace) -> dict[str, Any]:
    corpus_path, queries_dir = _fixture_paths(args.corpus)
    documents = _documents(corpus_path)
    chunk_queries_path = args.cross_queries or queries_dir / "cross_queries.json"
    with _scratch(args.database_url) as database:
        connection = connect(database.direct_url)
        observations = _ObservationHandler()
        logger = logging.getLogger("pmstudio.infrastructure.postgres.materials")
        logger.addHandler(observations)
        previous_level = logger.level
        logger.setLevel(logging.INFO)
        try:
            embedder = FastEmbedEmbedder()
            started = time.perf_counter()
            corpus = _ingest(
                connection,
                documents,
                chunk_chars=args.chunk_chars,
                embedder=embedder,
            )
            ingest_seconds = time.perf_counter() - started
            retriever = SqliteHybridRetriever(connection, embedder=embedder)
            started = time.perf_counter()
            result = _evaluate(
                connection,
                corpus,
                chunk_chars=args.chunk_chars,
                queries_dir=queries_dir,
                chunk_queries_path=chunk_queries_path,
                retriever=retriever,
                observations=observations,
            )
            result["ingest_seconds"] = ingest_seconds
            result["measure_seconds"] = time.perf_counter() - started
            result["embedding_model"] = "BAAI/bge-small-zh-v1.5"
            result["fusion_alpha"] = 0.4
            result["material_slots"] = 2
            result["limit"] = LIMIT
            return result
        finally:
            logger.removeHandler(observations)
            logger.setLevel(previous_level)
            connection.close()


def _print_summary(result: dict[str, Any]) -> None:
    metrics = result["metrics"]
    lexical = metrics["lexical"]
    paraphrase = metrics["paraphrase"]
    cross = metrics["cross_block"]
    negative = metrics["negative"]
    candidates = metrics["candidates"]
    latency = metrics["latency_ms"]
    print(
        f"chunk_chars={result['chunk_chars']} chunks={result['chunks']}"
        f" materials={result['materials']}"
    )
    print(
        f"lexical n={lexical['n']} top1={lexical['top1']:.3f} r5={lexical['r5']:.3f}"
        f" block_r5={lexical['block_r5']:.3f} mrr={lexical['mrr']:.3f}"
    )
    print(
        f"paraphrase n={paraphrase['n']} top1={paraphrase['top1']:.3f}"
        f" r5={paraphrase['r5']:.3f} mrr={paraphrase['mrr']:.3f}"
    )
    print(
        f"cross complete={cross['complete']}/{cross['n']} rate={cross['rate']:.3f}"
    )
    print(
        f"negative false_positive={negative['false_positives']}/{negative['n']}"
        f" rate={negative['rate']:.3f}"
    )
    print(
        f"candidates fts_mean={candidates['fts_mean']:.1f}"
        f" vec_mean={candidates['vec_mean']:.1f}"
        f" fts_hits_mean={candidates['fts_hits_mean']:.1f}"
        f" hybrid_hits_mean={candidates['hybrid_hits_mean']:.1f}"
    )
    print(
        f"latency_ms p50={latency['p50']:.1f} p95={latency['p95']:.1f}"
        f" mean={latency['mean']:.1f} max={latency['max']:.1f} n={latency['n']}"
    )
    print(
        f"ingest={result['ingest_seconds']:.1f}s measure={result['measure_seconds']:.1f}s"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="冻结语料的检索质量基准（真 PG）")
    parser.add_argument(
        "--chunk-chars",
        type=int,
        choices=(300, 400),
        default=300,
        help="块宽：300=当前生产口径，400=旧口径；默认 300",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_FIXTURE,
        help="冻结语料目录（含 corpus.json）或 corpus.json 路径",
    )
    parser.add_argument(
        "--cross-queries",
        type=Path,
        default=None,
        help="跨块查询文件；默认读语料目录的 cross_queries.json",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="临时 schema 的建库 URL；默认按 .env/.env.ops 取 app/direct URL",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        type=Path,
        default=None,
        help="把完整结果写成 JSON",
    )
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except Exception as error:  # noqa: BLE001 —— CLI 给出非零退出与原因
        print(f"检索基准失败：{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    _print_summary(result)
    if args.json_path:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"完整结果 → {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
