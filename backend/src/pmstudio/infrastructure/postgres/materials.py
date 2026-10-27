"""材料仓储与检索（PostgreSQL）：**唯一盖章点**（material_id / project_id 只在这里生成）。

设计与取舍（`docs/plans/2026-09-30-P17-工具层.md`）：

- **原始字节与解析结果一起存**：换解析器时靠它重解析，用户不用重传；
- **同项目内按 sha256 去重**：同一份文件传两次不会存两遍（也就不占两次提示词预算）；
- **块 id 在盖章时改写**（`local-text#c00` → `m-xxxx#c00`）：解析器不知道 id，
  改写只发生在这一处，别处不再拼；
- **列表只回摘要**：块正文一个字都不进列表（要正文只有一个地方——本轮带进去的块）。

词法索引与块同表：`tokens` 是 `index_source` 的分词串（写路径唯一来源），
`search_tsv` 是 generated tsvector，查询用 `to_tsquery('simple', ...)` +
`ts_rank_cd`（正分）；子串兜底用 `ILIKE`。不再有 FTS5/vec0 影子表。
"""

import logging
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import replace
from datetime import datetime

from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg.errors import ProgrammingError

from pmstudio.application.dto import (
    MATERIAL_SCHEMA_VERSION,
    MaterialChunk,
    MaterialHit,
    MaterialPacket,
    MaterialSummaryView,
    ParsedMaterial,
    material_summary_view,
)
from pmstudio.application.ports import IdGenerator
from pmstudio.domain.shared.ids import ProjectId, UserId
from pmstudio.infrastructure.postgres.owner_scope import require_owner
from pmstudio.infrastructure.postgres.tokenize import (
    index_source,
    match_expr,
    query_tokens,
)

logger = logging.getLogger(__name__)

_TSQUERY = "to_tsquery('simple', %s)"

#: `ts_rank_cd` 的长度归一化（第三个参数）。0 = 不按文档长度归一，长块里命中
#: 一个常见词也能压过短块里命中稀有词；2 = 按文档长度归一，更接近原 BM25 的
#: 长度惩罚。用冻结语料扫过 0/1/2/4/8/16/32：2 与 8 的改述召回最好，
#: 2 更接近 BM25 的口径。
TS_RANK_NORMALIZATION = 2


def _raw_connection(connection):  # type: ignore[no-untyped-def]
    """拿到 psycopg 真连接（`PgConnection` 是个会重连的把手）。"""

    return getattr(connection, "connection", connection)


def _register_vector(connection) -> object:  # type: ignore[no-untyped-def]
    """确保这条连接认识 `vector` 参数。**没有扩展时不在连接层炸**。"""

    raw = _raw_connection(connection)
    if getattr(raw, "_pmstudio_vector_registered", False):
        return raw
    try:
        register_vector(raw)
    except ProgrammingError:
        # 扩展不在：向量路本来就不可用，调用方会捕获并退回纯 FTS。
        pass
    else:
        raw._pmstudio_vector_registered = True
    return raw


def _in_transaction(connection) -> bool:  # type: ignore[no-untyped-def]
    depth = getattr(connection, "transaction_depth", None)
    if isinstance(depth, int):
        return depth > 0
    try:
        from psycopg.pq import TransactionStatus

        return connection.info.transaction_status != TransactionStatus.IDLE
    except Exception:  # noqa: BLE001 —— 状态不可读时按事务内处理
        return True


class SqliteMaterialStore:
    """材料仓（类名保留以兼容旧装配；实现只认 PostgreSQL）。"""

    def __init__(self, connection, *, ids: IdGenerator) -> None:  # type: ignore[no-untyped-def]
        self._connection = connection
        self._ids = ids

    # ── 写 ────────────────────────────────────────────────────────────────

    def put(
        self,
        *,
        project_id: ProjectId,
        parsed: ParsedMaterial,
        sha256: str,
        content: bytes,
        now: datetime,
        owner: UserId | None = None,
    ) -> MaterialPacket:
        resolved = require_owner(owner)
        existing = self._find_by_sha(project_id, sha256, owner=resolved)
        if existing is not None:
            return existing
        material_id = self._ids.new("m")
        stamped = _restamp(parsed, material_id)
        self._connection.execute(
            """
            INSERT INTO materials (material_id, project_id, owner_id, origin, title, source_ref,
                media_type, bytes_len, sha256, parser_id, parser_version, schema_version,
                truncated, warnings, content, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                material_id,
                str(project_id),
                str(resolved),
                stamped.origin,
                stamped.title,
                stamped.source_ref,
                stamped.media_type,
                stamped.bytes_len,
                sha256,
                stamped.parser_id,
                stamped.parser_version,
                MATERIAL_SCHEMA_VERSION,
                stamped.truncated,
                list(stamped.warnings),
                content,
                now,
            ),
        )
        self._write_chunks(material_id, stamped, owner=str(resolved))
        return MaterialPacket(
            material_id=material_id,
            project_id=str(project_id),
            parsed=stamped,
            created_at=now,
            schema_version=MATERIAL_SCHEMA_VERSION,
        )

    def delete(self, material_id: str, *, owner: UserId | None = None) -> None:
        resolved = require_owner(owner)
        self._connection.execute(
            "DELETE FROM material_chunks WHERE material_id = %s AND owner_id = %s",
            (material_id, str(resolved)),
        )
        self._connection.execute(
            "DELETE FROM materials WHERE material_id = %s AND owner_id = %s",
            (material_id, str(resolved)),
        )

    def replace_chunks(self, material_id: str, parsed: ParsedMaterial) -> MaterialPacket:
        """就地换块（重解析）：**id / 去重键 / 原始字节 / 标题都不动**，只换读法与解析器身份。

        只给"没被任何一轮引用过"的材料用（用例那边判）——被引用的材料改块 = 改指编号。
        """

        owner = require_owner(None)
        if not self._owned(material_id, owner):
            # 不属于本 owner（或不存在）：一个字都不动，与"找不到"同形。
            raise KeyError(material_id)
        stamped = _restamp(parsed, material_id)
        self._connection.execute(
            "DELETE FROM material_chunks WHERE material_id = %s AND owner_id = %s",
            (material_id, str(owner)),
        )
        self._connection.execute(
            """
            UPDATE materials
            SET parser_id = %s, parser_version = %s, schema_version = %s,
                truncated = %s, warnings = %s
            WHERE material_id = %s AND owner_id = %s
            """,
            (
                stamped.parser_id,
                stamped.parser_version,
                MATERIAL_SCHEMA_VERSION,
                stamped.truncated,
                list(stamped.warnings),
                material_id,
                str(owner),
            ),
        )
        self._write_chunks(material_id, stamped, owner=str(owner))
        updated = self.get(material_id, owner=owner)
        if updated is None:  # pragma: no cover - 上面刚 UPDATE 过，只有并发删才会到这里
            raise KeyError(material_id)
        return updated

    def refresh(
        self, material_id: str, *, parsed: ParsedMaterial, content: bytes
    ) -> MaterialPacket:
        """就地刷新（`P27`）：**原始字节、块、解析器身份、标题与出处一起换**。

        只给"没被任何一轮引用过"的材料用（用例那边判，`P19` 的同一道边）——
        用途是"同一个网址再抓一次"。`material_id`、去重键、`created_at` 都不动。
        """

        owner = require_owner(None)
        if not self._owned(material_id, owner):
            # 不属于本 owner（或不存在）：一个字都不动，与"找不到"同形。
            raise KeyError(material_id)
        stamped = _restamp(parsed, material_id)
        self._connection.execute(
            "DELETE FROM material_chunks WHERE material_id = %s AND owner_id = %s",
            (material_id, str(owner)),
        )
        self._connection.execute(
            """
            UPDATE materials
            SET origin = %s, title = %s, source_ref = %s, media_type = %s, bytes_len = %s,
                parser_id = %s, parser_version = %s, schema_version = %s, truncated = %s,
                warnings = %s, content = %s
            WHERE material_id = %s AND owner_id = %s
            """,
            (
                stamped.origin,
                stamped.title,
                stamped.source_ref,
                stamped.media_type,
                len(content),
                stamped.parser_id,
                stamped.parser_version,
                MATERIAL_SCHEMA_VERSION,
                stamped.truncated,
                list(stamped.warnings),
                content,
                material_id,
                str(owner),
            ),
        )
        self._write_chunks(material_id, stamped, owner=str(owner))
        updated = self.get(material_id, owner=owner)
        if updated is None:  # pragma: no cover - 上面刚 UPDATE 过，只有并发删才会到这里
            raise KeyError(material_id)
        return updated

    def _write_chunks(
        self, material_id: str, stamped: ParsedMaterial, *, owner: str
    ) -> None:
        for chunk in stamped.chunks:
            # 词法索引与块**同一行**：`tokens` 是写路径唯一来源，`search_tsv`
            # 由数据库 generated 列维护。
            self._connection.execute(
                """
                INSERT INTO material_chunks
                    (chunk_id, material_id, owner_id, ordinal, text, heading_path, anchor, tokens)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    chunk.chunk_id,
                    material_id,
                    owner,
                    chunk.ordinal,
                    chunk.text,
                    list(chunk.heading_path),
                    chunk.anchor,
                    index_source(
                        title=stamped.title, heading_path=chunk.heading_path, text=chunk.text
                    ),
                ),
            )

    # ── 读 ────────────────────────────────────────────────────────────────

    def get(self, material_id: str, *, owner: UserId | None = None) -> MaterialPacket | None:
        resolved = require_owner(owner)
        row = self._connection.execute(
            "SELECT * FROM materials WHERE material_id = %s AND owner_id = %s",
            (material_id, str(resolved)),
        ).fetchone()
        return None if row is None else self._packet(row)

    def raw_bytes(self, material_id: str, *, owner: UserId | None = None) -> bytes | None:
        resolved = require_owner(owner)
        row = self._connection.execute(
            "SELECT content FROM materials WHERE material_id = %s AND owner_id = %s",
            (material_id, str(resolved)),
        ).fetchone()
        return None if row is None else bytes(row["content"])

    def chunk_count(self, material_id: str, *, owner: UserId | None = None) -> int:
        resolved = require_owner(owner)
        row = self._connection.execute(
            "SELECT COUNT(*) AS total FROM material_chunks WHERE material_id = %s"
            " AND owner_id = %s",
            (material_id, str(resolved)),
        ).fetchone()
        return int(row["total"])

    def list_materials_for_reparse(self) -> tuple[tuple[str, str, str, str], ...]:
        """**维护任务用**：列出全部材料的 `(material_id, owner_id, project_id, origin)`。

        **跨 owner 且不带 owner 过滤**——维护任务天生跨账号（每个 owner 的材料
        都要按新块宽重切）；与 `optional_owner` 的"查询本身就要包含全局行"
        同一性质（templates 是先例）。**只读**：写仍然按 owner 盖章。
        """
        rows = self._connection.execute(
            "SELECT material_id, owner_id, project_id, origin FROM materials"
        ).fetchall()
        return tuple(
            (
                str(row["material_id"]),
                str(row["owner_id"]),
                str(row["project_id"]),
                str(row["origin"]),
            )
            for row in rows
        )

    def list_for_project(
        self,
        project_id: ProjectId,
        *,
        current: frozenset[tuple[str, str]],
        owner: UserId | None = None,
    ) -> tuple[MaterialSummaryView, ...]:
        resolved = require_owner(owner)
        rows = self._connection.execute(
            """
            SELECT m.*, (SELECT COUNT(*) FROM material_chunks c WHERE c.material_id = m.material_id)
                AS chunk_count,
                   (SELECT COALESCE(SUM(LENGTH(c.text)), 0) FROM material_chunks c
                    WHERE c.material_id = m.material_id) AS chars
            FROM materials m
            WHERE m.project_id = %s AND m.owner_id = %s
            ORDER BY m.created_at DESC, m.material_id DESC
            """,
            (str(project_id), str(resolved)),
        ).fetchall()
        return tuple(
            material_summary_view(
                self._packet(row, chunks=()),
                current=current,
                chunk_count=int(row["chunk_count"]),
                chars=int(row["chars"]),
            )
            for row in rows
        )

    def count_for_project(
        self, project_id: ProjectId, *, owner: UserId | None = None
    ) -> int:
        resolved = require_owner(owner)
        row = self._connection.execute(
            "SELECT COUNT(*) AS total FROM materials WHERE project_id = %s AND owner_id = %s",
            (str(project_id), str(resolved)),
        ).fetchone()
        return int(row["total"])

    def find_by_sha(
        self, project_id: ProjectId, sha256: str, *, owner: UserId | None = None
    ) -> MaterialPacket | None:
        return self._find_by_sha(project_id, sha256, owner=owner)

    # ── 内部 ──────────────────────────────────────────────────────────────

    def _find_by_sha(
        self, project_id: ProjectId, sha256: str, *, owner: UserId | None = None
    ) -> MaterialPacket | None:
        resolved = require_owner(owner)
        row = self._connection.execute(
            "SELECT * FROM materials WHERE project_id = %s AND sha256 = %s AND owner_id = %s",
            (str(project_id), sha256, str(resolved)),
        ).fetchone()
        return None if row is None else self._packet(row)

    def _owned(self, material_id: str, owner: UserId) -> bool:
        """这份材料属不属于 `owner`——就地改/删前先问，避免"改了别人的块"这种越权。"""

        return (
            self._connection.execute(
                "SELECT 1 FROM materials WHERE material_id = %s AND owner_id = %s",
                (material_id, str(owner)),
            ).fetchone()
            is not None
        )

    def _packet(self, row, *, chunks: tuple[MaterialChunk, ...] | None = None):  # type: ignore[no-untyped-def]
        if chunks is None:
            chunks = tuple(
                MaterialChunk(
                    chunk_id=item["chunk_id"],
                    ordinal=int(item["ordinal"]),
                    text=item["text"],
                    heading_path=tuple(item["heading_path"]),
                    anchor=item["anchor"],
                )
                for item in self._connection.execute(
                    "SELECT * FROM material_chunks WHERE material_id = %s ORDER BY ordinal",
                    (row["material_id"],),
                ).fetchall()
            )
        parsed = ParsedMaterial(
            origin=row["origin"],
            title=row["title"],
            source_ref=row["source_ref"],
            media_type=row["media_type"],
            bytes_len=int(row["bytes_len"]),
            chunks=chunks,
            parser_id=row["parser_id"],
            parser_version=row["parser_version"],
            truncated=bool(row["truncated"]),
            warnings=tuple(row["warnings"]),
        )
        return MaterialPacket(
            material_id=row["material_id"],
            project_id=row["project_id"],
            parsed=parsed,
            created_at=row["created_at"],
            schema_version=int(row["schema_version"]),
        )


def _restamp(parsed: ParsedMaterial, material_id: str) -> ParsedMaterial:
    """把块 id 的前缀换成 material_id（解析器用 `parser_id#cNN` 占位）。"""

    return replace(
        parsed,
        chunks=tuple(
            replace(chunk, chunk_id=f"{material_id}#c{chunk.ordinal:02d}")
            for chunk in parsed.chunks
        ),
    )


class SqliteFtsRetriever:
    """材料检索的纯词法实现：PostgreSQL generated tsvector + jieba 预分词。

    四条口径（块级返回 + 锚点 + 空查询回空 + 项目/owner 隔离）不变；
    `score` 是 `ts_rank_cd` 的**正分**（越大越相关），不再取负 BM25。
    """

    _SQL = f"""
        WITH query AS (SELECT {_TSQUERY} AS q)
        SELECT c.chunk_id, c.material_id, c.text, c.anchor, c.ordinal,
               m.title, m.source_ref, m.origin,
               ts_rank_cd(c.search_tsv, query.q, {TS_RANK_NORMALIZATION}) AS rank
        FROM material_chunks c
        JOIN materials m ON m.material_id = c.material_id
        CROSS JOIN query
        WHERE c.search_tsv @@ query.q AND m.project_id = %s AND m.owner_id = %s
        ORDER BY rank DESC, c.chunk_id
        LIMIT %s
    """

    def __init__(self, connection, *, caller: str = "search") -> None:  # type: ignore[no-untyped-def]
        self._connection = connection
        #: 观测行的 caller 标签。空串 = 不记（混合检索内部会调它，那一层已有自己的分解行）。
        self._caller = caller

    def search(
        self,
        *,
        project_id: ProjectId,
        query: str,
        limit: int = 5,
        owner: UserId | None = None,
    ) -> tuple[MaterialHit, ...]:
        resolved = require_owner(owner)
        expr = match_expr(query)
        if expr == "" or limit <= 0:
            return ()
        rows = self._connection.execute(
            self._SQL, (expr, str(project_id), str(resolved), limit)
        ).fetchall()
        tokens = query_tokens(query)
        hits = tuple(self._hit(row, tokens) for row in rows)
        if self._caller:
            # 顶栏搜索没有词面/语义分解（它是纯 FTS），只记 caller/stage/命中数。
            # R8 的真查询样本据此把"人搜索"与"建轮自动检索"分开。
            logger.info(
                "material_search caller=%s stage=lookup project=%s query=%r"
                " limit=%d hits=%d",
                self._caller,
                project_id,
                query,
                limit,
                len(hits),
            )
        return hits

    def _hit(self, row, tokens: list[str]) -> MaterialHit:  # type: ignore[no-untyped-def]
        text = str(row["text"])
        # 片段以**第一个在原文里出现的命中 token**定位（跳得回原文）；一个都没出现
        # （理论上进不来）就用第一个 token，`_excerpt` 自己会兜底给开头。
        needle = next((t for t in tokens if t.casefold() in text.casefold()), tokens[0])
        return MaterialHit(
            material_id=str(row["material_id"]),
            chunk_id=str(row["chunk_id"]),
            score=float(row["rank"]),
            excerpt=_excerpt(text, needle),
            anchor=str(row["anchor"]),
            source_ref=str(row["source_ref"]),
            title=str(row["title"]),
            origin=str(row["origin"]),
        )


#: 片段窗口：命中词前后各留多少字（列表里一行放不下整块）。
_EXCERPT_WINDOW = 40


def _excerpt(text: str, needle: str) -> str:
    """命中词前后各 `_EXCERPT_WINDOW` 字；命中词本身一定在窗口里。"""

    at = text.casefold().find(needle)
    if at < 0:  # 理论上进不来（FTS 已经筛过）；真进来就老老实实给开头
        return text[: _EXCERPT_WINDOW * 2]
    start = max(0, at - _EXCERPT_WINDOW)
    end = min(len(text), at + len(needle) + _EXCERPT_WINDOW)
    head = "…" if start > 0 else ""
    tail = "…" if end < len(text) else ""
    return f"{head}{text[start:end]}{tail}"


#: 两个分支各取多少候选再融合。PG 的 `ts_rank_cd` 没有 BM25 的 IDF，低频词
#: 不再自动拔高，目标块可能落到 FTS 20 名开外；冻结语料实测定 50（改述材料
#: recall@5 0.571 → 0.714，词面不变）。KNN 内层扫 200（过滤放在 SQL 里，
#: 由 HNSW + iterative scan 处理）。
_FTS_CANDIDATES = 50
_KNN_SCAN_LIMIT = 200

#: **融合的分档阈值**（2026-10-19 实测定的，见
#: `docs/plans/2026-10-19-B组-批5-量化与端到端验收.md`）。
#:
#: 档位按**词面覆盖率** `cov = |查询 token ∩ 块 token| / |查询 token|` 定：
#: `cov >= 1.0` = 用户的话**原样**在这块里；`cov >= 0.5` = 大半的话在；
#: 再低就是"词面说不了"（换个说法问的典型情形）。
#: 0.5 不是调出来的魔数，是"多数"的自然边界。
COVERAGE_FULL = 1.0
COVERAGE_MAJORITY = 0.5

#: **融合的 α**：语义那一路的权重（0=纯词面，1=纯语义）。
#: PG 迁移后用冻结语料（`.codex/runs/2026-10-19/tavily_raw`，15 份 / 45 词面 /
#: 14 改述 / 15 跨块 / 8 负样本）重标：α 扫描 0.2–0.7，0.4 是词面（0.844）
#: 与改述（0.714）都不低于相邻档、跨块完整率（0.467）也守住的一档。
#: **换个语料要重量**。
FUSION_ALPHA = 0.4

#: **每份材料最多占几个槽**（结果里的位置）。
#:
#: **2**（2026-10-21 批 8 实测定）：原来 1 时，**答案跨两块时第二块证据永远进不来**
#: ——实测「跨块证据完整率 0/14」（同一材料相距 4 块的两处各取一个稀有词）。
#: 给 2 个槽：**跨块完整率 0 → 0.429**、**块级 recall@5 0.867 → 0.933**，
#: 而**材料级 recall@5 保持 1.000、改述保持 1.000**（生产口径 limit=50 时，
#: 50 个位置对 15 份材料 → 每份材料 2 槽还有富余，**不是零和**）。
_MATERIAL_SLOTS = 2


def dedup_by_material(hits, limit: int, *, slots: int = _MATERIAL_SLOTS) -> list:
    """按「一份材料最多 `slots` 个槽」去重，取前 `limit` 个。

    **为什么公开**：混合路有这份去重，**词面基线也必须有**（否则拿"占满 5 个槽的
    同一份材料"去比"一份材料一行"的混合路 → **增量被高估**）。
    量化脚本与检索器的观测行都用它 → 同一把尺子 ✓（2026-10-22 批 13，R9）。
    """
    seen: dict[str, int] = {}
    out = []
    for hit in hits:
        used = seen.get(hit.material_id, 0)
        if used >= slots:
            continue
        seen[hit.material_id] = used + 1
        out.append(hit)
        if len(out) >= limit:
            break
    return out


def reciprocal_rank_fusion(
    ranked: Sequence[Sequence[str]], *, k: int = 60
) -> tuple[str, ...]:
    """把若干"已按相关性排好的 id 列表"融成一个次序（**RRF**，`Σ 1/(k+rank)`）。

    .. deprecated::
        2026-10-19 实测：RRF **满足不了**本批的预注册判据——它是**对称**融合，
        于是"词法第 1 但语义第 50"的块会被"语义第 1 但词法第 2"的块压下去
        （逐字子串类 top1 30→25、recall@5 40→36，用户升级后反而搜不到以前能搜到的）。
        现在用 `_fuse_by_score`（凸组合）。本函数**留作对照与复算用**。
    """

    scores: dict[str, float] = {}
    for column in ranked:
        for rank, item in enumerate(column, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    first_seen = {item: i for column in ranked for i, item in enumerate(column)}
    return tuple(sorted(scores, key=lambda item: (-scores[item], first_seen[item])))


class SqliteHybridRetriever:
    """材料检索的混合实现：FTS（词法）+ pgvector KNN（语义）→ 凸组合融合。

    协议零改（`MaterialRetriever` 的签名与 `MaterialHit` 不动）；两个分支的形状
    与词法实现一致（块级返回 + 锚点 + 隔离 + `score` 越大越好）。
    某一分支失败（模型下不下来 / 推理抛错 / 扩展缺失）就退化成另一分支 + warning，
    检索本身永远有结果。
    """

    def __init__(self, connection, embedder) -> None:  # type: ignore[no-untyped-def]
        self._connection = connection
        self._fts = SqliteFtsRetriever(connection, caller="")
        self._embedder = embedder

    def search(
        self,
        *,
        project_id: ProjectId,
        query: str,
        limit: int = 5,
        owner: UserId | None = None,
    ) -> tuple[MaterialHit, ...]:
        resolved = require_owner(owner)
        tokens = query_tokens(query)
        if not tokens or limit <= 0:
            return ()
        fts_hits = self._fts.search(
            project_id=project_id, query=query, limit=_FTS_CANDIDATES, owner=resolved
        )
        vec_scores = self._knn_scores(str(project_id), str(resolved), query)
        ordered = self._fuse_by_score(fts_hits, vec_scores, query)
        by_id = {hit.chunk_id: hit for hit in fts_hits}
        knn_only = [chunk_id for chunk_id in ordered if chunk_id not in by_id]
        by_id.update(self._hits_for(knn_only, str(project_id), str(resolved), tokens))
        # **子串兜底**：词面与语义都没找满时，用「用户搜的连续字串」补空槽。
        # **只补空槽、绝不挤占已有结果**——那是兜底的底线（挤占等于把好结果换掉）。
        if len(ordered) < limit:
            filler = self._substring_candidates(
                project_id=str(project_id),
                owner=str(resolved),
                query=query,
                limit=limit - len(ordered),
                exclude=set(ordered),
            )
            if filler:
                by_id.update(self._hits_for(filler, str(project_id), str(resolved), tokens))
                ordered = ordered + list(filler)
        # **一份材料最多两个槽**（2026-10-21 批 8 实测），理由见 `_MATERIAL_SLOTS`。
        seen_materials: dict[str, int] = {}
        final: list[str] = []
        for chunk_id in ordered:
            material_id = by_id[chunk_id].material_id
            used = seen_materials.get(material_id, 0)
            if used >= _MATERIAL_SLOTS:
                continue
            seen_materials[material_id] = used + 1
            final.append(chunk_id)
            if len(final) >= limit:
                break
        # **检索观测**（批 13：R8 扩到每次检索 + R9 的配对增量）。
        logger.info(
            "material_search project=%s query=%r fts_candidates=%d vec_candidates=%d"
            " fts_hits=%d hybrid_hits=%d",
            project_id,
            query,
            len(fts_hits),
            len(vec_scores),
            len(dedup_by_material(fts_hits, limit)),
            len(final),
        )
        # `score` 越大越好（与顺序同向）：用**最终次序**的倒数排名。
        return tuple(
            replace(by_id[chunk_id], score=1.0 / (position + 1))
            for position, chunk_id in enumerate(final)
        )

    def _substring_candidates(
        self, *, project_id: str, owner: str, query: str, limit: int, exclude: set[str]
    ) -> tuple[str, ...]:
        """按「用户搜的连续字串」找块 —— **只填空槽的兜底**。

        用 `ILIKE`（大小写不敏感），并把 `% _ \\` 按字面转义（否则用户搜
        "50%" 会变成通配）。中文不受影响。
        """
        needle = query.strip()
        if not needle or limit <= 0:
            return ()
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self._connection.execute(
            "SELECT c.chunk_id, c.material_id"
            " FROM material_chunks c JOIN materials m ON m.material_id = c.material_id"
            " WHERE m.project_id = %s AND c.owner_id = %s AND c.text ILIKE %s ESCAPE '\\'"
            " ORDER BY m.created_at DESC, c.material_id, c.ordinal",
            (project_id, owner, f"%{escaped}%"),
        ).fetchall()
        picked: list[str] = []
        seen_materials: set[str] = set()
        for row in rows:
            chunk_id = str(row["chunk_id"])
            material_id = str(row["material_id"])
            if chunk_id in exclude or material_id in seen_materials:
                continue
            seen_materials.add(material_id)
            picked.append(chunk_id)
            if len(picked) >= limit:
                break
        return tuple(picked)

    def _fuse_by_score(
        self, fts_hits, vec_scores: dict[str, float], query: str
    ) -> list[str]:
        """两路融合：**凸组合** `α·余弦 + (1−α)·归一化 ts_rank_cd`。

        `ts_rank_cd` 无上界（正分，越大越强），用**饱和归一** `s/(s+pivot)`：
        pivot 取候选集里 `ts_rank_cd` 的中位数。SQLite 的 BM25 换成正分的
        `ts_rank_cd` 后，量纲变了但"中位数归一"仍是查询内稳定、有界的做法；
        α 用冻结语料重标（见 `FUSION_ALPHA`）。
        """

        if not fts_hits and not vec_scores:
            return []
        lexical = {hit.chunk_id: float(hit.score) for hit in fts_hits}
        candidates = list(dict.fromkeys([hit.chunk_id for hit in fts_hits] + list(vec_scores)))
        if not lexical:
            return sorted(vec_scores, key=lambda chunk_id: -vec_scores[chunk_id])
        if not vec_scores:
            return [hit.chunk_id for hit in fts_hits]
        positive = sorted(value for value in lexical.values() if value > 0)
        pivot = positive[len(positive) // 2] if positive else 1.0
        alpha = FUSION_ALPHA
        scores = {
            chunk_id: alpha * vec_scores.get(chunk_id, 0.0)
            + (1 - alpha) * (lexical.get(chunk_id, 0.0) / (lexical.get(chunk_id, 0.0) + pivot)
                               if chunk_id in lexical else 0.0)
            for chunk_id in candidates
        }
        return sorted(candidates, key=lambda chunk_id: -scores[chunk_id])

    def _coverage(self, chunk_ids: list[str], query: str) -> dict[str, float]:
        """每个候选块的**词面覆盖率**。

        token 直接读 `material_chunks.tokens`（`index_source` 的产物）——**不重新分词**：
        重新分词可能与建索引时那次的切法不同，覆盖率就名不副实了。
        """

        tokens = set(query_tokens(query))
        if not tokens or not chunk_ids:
            return {chunk_id: 0.0 for chunk_id in chunk_ids}
        rows = self._connection.execute(
            "SELECT chunk_id, tokens FROM material_chunks WHERE chunk_id = ANY(%s)",
            (list(chunk_ids),),
        ).fetchall()
        coverage: dict[str, float] = {}
        for row in rows:
            doc_tokens = set(str(row["tokens"]).split())
            coverage[str(row["chunk_id"])] = len(tokens & doc_tokens) / len(tokens)
        return {chunk_id: coverage.get(chunk_id, 0.0) for chunk_id in chunk_ids}

    def _knn_scores(
        self, project_id: str, owner: str, query: str
    ) -> dict[str, float]:
        """KNN 分支：**块 id → 真余弦**（`1 - cosine distance`）。

        ## 为什么给余弦而不是名次（2026-10-19 定的）

        凸组合融合要的是**可比的分数幅度**。名次归一化把「第 1 名」与「第 200 名」
        压成 1.0 与 0.005——幅度整个丢了，这正是 RRF 一族（纯名次函数）的病根
        （TOIS 2023：凸组合 > RRF，in/out-of-domain 都赢）。

        ## 过滤后候选不足

        pgvector HNSW 是近似索引，带 `project_id/owner_id` 过滤时可能先扫到
        一堆别的项目。**显式事务内**用 `SET LOCAL hnsw.iterative_scan` 让扫描继续
        直到凑够候选；事务外（单条只读查询）不设 session GUC，靠 SQL 过滤 +
        更宽的扫描上限。全局约束：只用事务内 `SET LOCAL`。
        """
        try:
            vector = self._embedder.embed_many([query])[0]
        except Exception as error:  # noqa: BLE001 —— 模型/推理失败就退化成纯词法
            logger.warning("向量检索失败，退回纯词法：%s", error)
            return {}
        _register_vector(self._connection)
        # `SET LOCAL` 只在事务里有效。检索通常不在写事务里——这里显式开一小段
        # 只读事务，让 HNSW 的迭代扫描与候选深度对**每次**检索都生效；
        # 已在事务里就复用调用方的事务（不提交别人的工作）。
        owned = not _in_transaction(self._connection)
        if owned:
            self._connection.execute("BEGIN")
        try:
            self._connection.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            self._connection.execute(f"SET LOCAL hnsw.ef_search = {_KNN_SCAN_LIMIT}")
            rows = self._connection.execute(
                """
                SELECT c.chunk_id, c.embedding <=> %s AS distance
                FROM material_chunks c
                JOIN materials m ON m.material_id = c.material_id
                WHERE m.project_id = %s AND c.owner_id = %s AND c.embedding IS NOT NULL
                ORDER BY c.embedding <=> %s
                LIMIT %s
                """,
                (
                    Vector(list(vector)),
                    project_id,
                    owner,
                    Vector(list(vector)),
                    _KNN_SCAN_LIMIT,
                ),
            ).fetchall()
            if owned:
                self._connection.execute("COMMIT")
        except Exception as error:  # noqa: BLE001 —— 扩展/表不可用时退化，不炸检索
            if owned:
                with suppress(Exception):
                    self._connection.execute("ROLLBACK")
            logger.warning("向量索引不可用，退回纯词法：%s", error)
            return {}
        return {
            str(row["chunk_id"]): 1.0 - float(row["distance"]) for row in rows
        }

    def _hits_for(self, chunk_ids, project_id: str, owner: str, tokens: list[str]):
        """给"只在 KNN 里出现"的块造 `MaterialHit`（复用 `_excerpt` 的锚定规则）。"""
        if not chunk_ids:
            return {}
        rows = self._connection.execute(
            "SELECT c.chunk_id, c.material_id, c.text, c.anchor, m.title, m.source_ref,"
            " m.origin"
            " FROM material_chunks c JOIN materials m ON m.material_id = c.material_id"
            " WHERE c.chunk_id = ANY(%s)"
            " AND m.project_id = %s AND c.owner_id = %s",
            (list(chunk_ids), project_id, owner),
        ).fetchall()
        hits = {}
        for row in rows:
            text = str(row["text"])
            needle = next(
                (t for t in tokens if t.casefold() in text.casefold()), tokens[0]
            )
            hits[str(row["chunk_id"])] = MaterialHit(
                material_id=str(row["material_id"]),
                chunk_id=str(row["chunk_id"]),
                score=0.0,
                excerpt=_excerpt(text, needle),
                anchor=str(row["anchor"]),
                source_ref=str(row["source_ref"]),
                title=str(row["title"]),
                origin=str(row["origin"]),
            )
        return hits
