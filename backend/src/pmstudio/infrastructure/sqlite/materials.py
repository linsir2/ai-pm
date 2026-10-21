"""材料仓储：**唯一盖章点**（material_id / project_id 只在这里生成）。

设计与取舍（`docs/plans/2026-09-30-P17-工具层.md`）：

- **原始字节与解析结果一起存**：换解析器时靠它重解析，用户不用重传；
- **同项目内按 sha256 去重**：同一份文件传两次不会存两遍（也就不占两次提示词预算）；
- **块 id 在盖章时改写**（`local-text#c00` → `m-xxxx#c00`）：解析器不知道 id，
  改写只发生在这一处，别处不再拼；
- **列表只回摘要**：块正文一个字都不进列表（要正文只有一个地方——本轮带进去的块）。
"""

import json
import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

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
from pmstudio.infrastructure.sqlite.owner_scope import require_owner
from pmstudio.infrastructure.sqlite.tokenize import index_source, match_expr, query_tokens


@dataclass(frozen=True, slots=True)
class _Row:
    material_id: str
    project_id: str
    origin: str
    title: str
    source_ref: str
    media_type: str
    bytes_len: int
    parser_id: str
    parser_version: str
    schema_version: int
    truncated: int
    warnings: str
    created_at: str


class SqliteMaterialStore:
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                int(stamped.truncated),
                json.dumps(list(stamped.warnings), ensure_ascii=False),
                content,
                now.isoformat(),
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
            "DELETE FROM material_chunks WHERE material_id = ? AND owner_id = ?",
            (material_id, str(resolved)),
        )
        self._drop_index(material_id)
        self._connection.execute(
            "DELETE FROM materials WHERE material_id = ? AND owner_id = ?",
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
            "DELETE FROM material_chunks WHERE material_id = ? AND owner_id = ?",
            (material_id, str(owner)),
        )
        self._drop_index(material_id)
        self._connection.execute(
            """
            UPDATE materials
            SET parser_id = ?, parser_version = ?, schema_version = ?,
                truncated = ?, warnings = ?
            WHERE material_id = ? AND owner_id = ?
            """,
            (
                stamped.parser_id,
                stamped.parser_version,
                MATERIAL_SCHEMA_VERSION,
                int(stamped.truncated),
                json.dumps(list(stamped.warnings), ensure_ascii=False),
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
            "DELETE FROM material_chunks WHERE material_id = ? AND owner_id = ?",
            (material_id, str(owner)),
        )
        self._drop_index(material_id)
        self._connection.execute(
            """
            UPDATE materials
            SET origin = ?, title = ?, source_ref = ?, media_type = ?, bytes_len = ?,
                parser_id = ?, parser_version = ?, schema_version = ?, truncated = ?,
                warnings = ?, content = ?
            WHERE material_id = ? AND owner_id = ?
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
                int(stamped.truncated),
                json.dumps(list(stamped.warnings), ensure_ascii=False),
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
            self._connection.execute(
                """
                INSERT INTO material_chunks
                    (chunk_id, material_id, owner_id, ordinal, text, heading_path, anchor)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.chunk_id,
                    material_id,
                    owner,
                    chunk.ordinal,
                    chunk.text,
                    json.dumps(list(chunk.heading_path), ensure_ascii=False),
                    chunk.anchor,
                ),
            )
            # 同一事务里维护全文索引（检索 P1）：索引侧与查询侧共用 `tokenize.index_tokens`。
            self._connection.execute(
                "INSERT INTO material_chunks_fts (chunk_id, material_id, tokens)"
                " VALUES (?, ?, ?)",
                (
                    chunk.chunk_id,
                    material_id,
                    index_source(
                        title=stamped.title, heading_path=chunk.heading_path, text=chunk.text
                    ),
                ),
            )

    def _drop_index(self, material_id: str) -> None:
        """删这份材料的全文索引条目。三处删块的地方都调它（材料删/重解析/换正文）。"""

        self._connection.execute(
            "DELETE FROM material_chunks_fts WHERE material_id = ?", (material_id,)
        )

    # ── 读 ────────────────────────────────────────────────────────────────

    def get(self, material_id: str, *, owner: UserId | None = None) -> MaterialPacket | None:
        resolved = require_owner(owner)
        row = self._connection.execute(
            "SELECT * FROM materials WHERE material_id = ? AND owner_id = ?",
            (material_id, str(resolved)),
        ).fetchone()
        return None if row is None else self._packet(row)

    def raw_bytes(self, material_id: str, *, owner: UserId | None = None) -> bytes | None:
        resolved = require_owner(owner)
        row = self._connection.execute(
            "SELECT content FROM materials WHERE material_id = ? AND owner_id = ?",
            (material_id, str(resolved)),
        ).fetchone()
        return None if row is None else bytes(row["content"])

    def chunk_count(self, material_id: str, *, owner: UserId | None = None) -> int:
        resolved = require_owner(owner)
        row = self._connection.execute(
            "SELECT COUNT(*) AS total FROM material_chunks WHERE material_id = ?"
            " AND owner_id = ?",
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
            WHERE m.project_id = ? AND m.owner_id = ?
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
            "SELECT COUNT(*) AS total FROM materials WHERE project_id = ? AND owner_id = ?",
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
            "SELECT * FROM materials WHERE project_id = ? AND sha256 = ? AND owner_id = ?",
            (str(project_id), sha256, str(resolved)),
        ).fetchone()
        return None if row is None else self._packet(row)

    def _owned(self, material_id: str, owner: UserId) -> bool:
        """这份材料属不属于 `owner`——就地改/删前先问，避免"改了别人的块"这种越权。"""

        return (
            self._connection.execute(
                "SELECT 1 FROM materials WHERE material_id = ? AND owner_id = ?",
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
                    heading_path=tuple(json.loads(item["heading_path"])),
                    anchor=item["anchor"],
                )
                for item in self._connection.execute(
                    "SELECT * FROM material_chunks WHERE material_id = ? ORDER BY ordinal",
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
            warnings=tuple(json.loads(row["warnings"])),
        )
        return MaterialPacket(
            material_id=row["material_id"],
            project_id=row["project_id"],
            parsed=parsed,
            created_at=datetime.fromisoformat(row["created_at"]),
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
    """材料检索的升级实现：FTS5 + jieba 预分词（**同一个 `MaterialRetriever` 协议**）。

    四条口径（块级返回 + 锚点 + 空查询回空 + 项目/owner 隔离）与被替换掉的 v1
    （整句 `LIKE`，**已于 2026-10-17 删除**）逐字不变；升级点只在**怎么匹配**：
    词级倒排 + OR（词序无关、容忍停用词）。验收实测（`docs/plans/2026-10-17-检索升级验收.md`）：
    合计 top1 34→134、recall@5 39→169（n=184），且逐字子串类零倒退。

    `score = -bm25`（bm25 越小越好，取负 → 越大越好，与"`score` 与顺序同向"的旧约定一致）。
    """

    _SQL = """
        SELECT c.chunk_id, c.material_id, c.text, c.anchor, c.ordinal,
               m.title, m.source_ref, bm25(material_chunks_fts) AS rank
        FROM material_chunks_fts f
        JOIN material_chunks c ON c.chunk_id = f.chunk_id
        JOIN materials m ON m.material_id = c.material_id
        WHERE material_chunks_fts MATCH ? AND m.project_id = ? AND m.owner_id = ?
        ORDER BY rank
        LIMIT ?
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

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
        return tuple(self._hit(row, tokens) for row in rows)

    def _hit(self, row: sqlite3.Row, tokens: list[str]) -> MaterialHit:
        text = str(row["text"])
        # 片段以**第一个在原文里出现的命中 token**定位（跳得回原文）；一个都没出现
        # （理论上进不来）就用第一个 token，`_excerpt` 自己会兜底给开头。
        needle = next((t for t in tokens if t.casefold() in text.casefold()), tokens[0])
        return MaterialHit(
            material_id=str(row["material_id"]),
            chunk_id=str(row["chunk_id"]),
            score=-float(row["rank"]),
            excerpt=_excerpt(text, needle),
            anchor=str(row["anchor"]),
            source_ref=str(row["source_ref"]),
            title=str(row["title"]),
        )


#: 片段窗口：命中词前后各留多少字（列表里一行放不下整块）。
_EXCERPT_WINDOW = 40


def _excerpt(text: str, needle: str) -> str:
    """命中词前后各 `_EXCERPT_WINDOW` 字；命中词本身一定在窗口里。"""

    at = text.casefold().find(needle)
    if at < 0:  # 理论上进不来（MATCH 已经筛过）；真进来就老老实实给开头
        return text[: _EXCERPT_WINDOW * 2]
    start = max(0, at - _EXCERPT_WINDOW)
    end = min(len(text), at + len(needle) + _EXCERPT_WINDOW)
    head = "…" if start > 0 else ""
    tail = "…" if end < len(text) else ""
    return f"{head}{text[start:end]}{tail}"


logger = logging.getLogger(__name__)

#: 两个分支各取多少候选再融合。单用户几百块封顶：FTS 取前 20 足够覆盖；
#: KNN 内层扫 200（`ORDER BY distance` 的全序扫描在 C 里是点积，200 行可忽略），
#: 然后按项目/owner 过滤——过滤放 Python 侧，不赌 vec0 对 JOIN 的优化器行为
#: （实测：vec0 的 KNN 不接受 JOIN，哪怕带了 LIMIT）。
_FTS_CANDIDATES = 20
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
#: 值由 `scripts/vector_fusion_lab.py` 在我们的语料上量出来（判据：逐字/换序两类
#: 不低于纯 FTS5，改述类高于纯 FTS5）。**换个语料要重量**。
FUSION_ALPHA = 0.5

#: **每份材料最多占几个槽**（结果里的位置）。
#:
#: **2**（2026-10-21 批 8 实测定）：原来 1 时，**答案跨两块时第二块证据永远进不来**
#: ——实测「跨块证据完整率 0/14」（同一材料相距 4 块的两处各取一个稀有词）。
#: 给 2 个槽：**跨块完整率 0 → 0.429**、**块级 recall@5 0.867 → 0.933**，
#: 而**材料级 recall@5 保持 1.000、改述保持 1.000**（生产口径 limit=50 时，
#: 50 个位置对 15 份材料 → 每份材料 2 槽还有富余，**不是零和**）。
#:
#: 代价：MRR 略降（0.593 → 0.564；改述 0.403 → 0.367）——同一材料占两个位置，
#: 别的材料被推后一点点。
#:
#: ⚠️ 扫描时有个**假象**要记住：用 `limit=5` 量时，改述召回会从 0.786 掉到 0.500
#: ——那是 5 个位置的**零和**紧张（小 limit），**生产是 limit=50**（`SEARCH_LIMIT`），
#: 不存在这个代价。数字见 `scripts/retrieval_bench.py --scan slots`。
_MATERIAL_SLOTS = 2

#: 语义分用**名次归一化**（vec#1 → 1.0，vec#N → 1/N），不用距离绝对值：
#: 距离的绝对值随语料/模型变，名次归一化后跨语料可比。
def _semantic_score(rank: int | None, total: int) -> float:
    if rank is None or total <= 0:
        return 0.0
    return 1.0 - (rank - 1) / total


def reciprocal_rank_fusion(
    ranked: Sequence[Sequence[str]], *, k: int = 60
) -> tuple[str, ...]:
    """把若干"已按相关性排好的 id 列表"融成一个次序（**RRF**，`Σ 1/(k+rank)`）。

    .. deprecated::
        2026-10-19 实测：RRF **满足不了**本批的预注册判据——它是**对称**融合，
        于是"词法第 1 但语义第 50"的块会被"语义第 1 但词法第 2"的块压下去
        （逐字子串类 top1 30→25、recall@5 40→36，用户升级后反而搜不到以前能搜到的）。
        现在用 `_fuse_by_coverage`（分档 + 语义排序）。本函数**留作对照与复算用**，
        量化对照表里那一列就是这么算的。
    """

    scores: dict[str, float] = {}
    for column in ranked:
        for rank, item in enumerate(column, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    first_seen = {item: i for column in ranked for i, item in enumerate(column)}
    return tuple(sorted(scores, key=lambda item: (-scores[item], first_seen[item])))


class SqliteHybridRetriever:
    """材料检索的混合实现：FTS5（词法）+ sqlite-vec KNN（语义）→ RRF 融合。

    协议零改（`MaterialRetriever` 的签名与 `MaterialHit` 不动）；两个分支的形状
    与 v2 的 `SqliteFtsRetriever` 逐字一致（块级返回 + 锚点 + 隔离 + `score` 越大越好）。
    某一分支失败（扩展没载 / 模型下不下来 / 推理抛错）就退化成另一分支 + warning，
    检索本身永远有结果。
    """

    def __init__(self, connection: sqlite3.Connection, embedder) -> None:
        self._connection = connection
        self._fts = SqliteFtsRetriever(connection)
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
        # **一份材料只占一个槽**（2026-10-20 批 6c 实测加的）。
        #
        # 为什么：① 界面（`dto.search_hits`）**一份材料只出一行**——检索器返回 5 个
        # 块但都来自同一份材料的话，界面只有 1 行，别的材料被挤出去了；
        # ② 它同时治了"部分向量索引"的偏置传导：KNN 的候选都来自前 N 块
        # （同一份材料的标题/导言），不按材料去重的话它们会占满 top5，
        # 把词面命中的真块挤掉——实测改述召回从 10/14 掉到 4/14（比不用向量还差）。
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
        # `score` 越大越好（与顺序同向）：用**最终次序**的倒数排名，与旧约定一致。
        # 2026-10-19 修：原先写的是 `1.0/(total-position)`，于是**第一名拿最小分**
        # （`total` 是候选总数，`position` 从 0 起 → 第一名 1/total、末名 1.0），
        # 与上面这句注释正好相反。改成 `1.0/(position+1)`：第一名 1.0，末名 1/limit。
        return tuple(
            replace(by_id[chunk_id], score=1.0 / (position + 1))
            for position, chunk_id in enumerate(final)
        )

    def _substring_candidates(
        self, *, project_id: str, owner: str, query: str, limit: int, exclude: set[str]
    ) -> tuple[str, ...]:
        """按「用户搜的连续字串」找块 —— **只填空槽的兜底**。

        ## 为什么要有这条

        jieba 的 `cut_for_search` 切出来的 token 与用户输入的连续字串**不一定对得上**：
        "数库"是词典里的整词（索引里有 `数库`），而材料里写的是"知识库"
        （索引成 `知识`/`知识库`）→ 搜"数库"**词面零命中**，可"数库"确实在正文里。
        这类缺口词面与语义都补不上（实测里"数库""准率""排重"都是词面零命中）。

        实测（2026-10-20，冻结语料 15 份 + 10 条手写查询）：子串这条路能补上
        词面与语义**都没**找到的材料（子串 10 条 / 单字 AND 13 条 / 词级 AND 9 条；
        取**最严的子串版**——它捞回来的片段里真的出现用户搜的字串，读得通；
        单字 AND 虽然捞得多，但捞出的片段读起来莫名其妙）。

        ## 两条纪律

        ① **一份材料只占一个槽**：同一材料的多个块都含该字串时只取第一个——
           否则一份材料会占满 top5，而界面（`dto.search_hits`）一份材料只出一行。
        ② **只填空槽**：调用方只在结果没找满时调它，绝不挤占已有结果。
        """
        needle = query.strip()
        if not needle or limit <= 0:
            return ()
        # LIKE 的通配符要转义，否则用户搜 "50%" 会变成通配（`%`/`_` 是通配符）
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self._connection.execute(
            "SELECT c.chunk_id, c.material_id"
            " FROM material_chunks c JOIN materials m ON m.material_id = c.material_id"
            " WHERE m.project_id = ? AND c.owner_id = ? AND c.text LIKE ? ESCAPE '\\'"
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
        """两路融合：**凸组合** `α·余弦 + (1−α)·归一化 BM25`。

        ## 为什么是凸组合而不是 RRF / 分档（2026-10-19 实测 + 调研定的）

        原来的口径是「**离散**分档（覆盖率 1.0 / 0.5 / else）+ 档内按语义名次排」。
        它的病是**把分数幅度丢了**：BM25 说"这块多强"被一个阈值拍成两档，
        档内再按语义名次重排 → 强度信息彻底没了。症状是"查询多一个字
        （覆盖率 1.0→0.8）就整档掉，BM25 挣到的第 1 位被语义掀翻"。

        调研结论（TOIS 2023，9 个数据集，in-domain 与 out-of-domain 都赢 RRF）：
        **归一化分数的凸组合 > RRF**；RRF 对参数敏感（NDCG "swings wildly"），
        因为它是**纯名次**函数，丢掉了距离/幅度信息；凸组合的参数 α **sample-efficient**
        （几个标注查询就够调，α∈[0.6,0.8] 稳健）。

        我们要的正是这个：**词面完全命中**（BM25 很高）与**语义很近**（余弦很高）
        能在**同一个尺度上比**，谁强谁上。

        ## 归一化方式

        两路都归一化到 [0,1]：
        - 语义：余弦本来就是 [-1,1]（`vec_scores` 给的是真余弦）；
        - 词面：BM25 无上界（SQLite 的 `bm25()` 取负后越大越强），
          用**饱和归一** `s/(s+k)`（k 取候选集里 BM25 的中位数）——
          比 min-max 稳（min-max 被一个极端值压扁其余，TOIS 也说归一方式影响不大）。

        ## α

        `FUSION_ALPHA`（语义那一路的权重）在我们的语料上量出来的
        （`scripts/vector_fusion_lab.py`）。选它的判据：逐字/换序两类**不低于纯 FTS5**，
        改述类**高于纯 FTS5**。
        """

        if not fts_hits and not vec_scores:
            return []
        bm25 = {hit.chunk_id: float(hit.score) for hit in fts_hits}
        candidates = list(dict.fromkeys([hit.chunk_id for hit in fts_hits] + list(vec_scores)))
        if not bm25:
            return sorted(vec_scores, key=lambda chunk_id: -vec_scores[chunk_id])
        if not vec_scores:
            return [hit.chunk_id for hit in fts_hits]
        pivot = sorted(bm25.values())[len(bm25) // 2]
        alpha = FUSION_ALPHA
        scores = {
            chunk_id: alpha * vec_scores.get(chunk_id, 0.0)
            + (1 - alpha) * (bm25.get(chunk_id, 0.0) / (bm25.get(chunk_id, 0.0) + pivot)
                               if chunk_id in bm25 else 0.0)
            for chunk_id in candidates
        }
        return sorted(candidates, key=lambda chunk_id: -scores[chunk_id])

    def _coverage(self, chunk_ids: list[str], query: str) -> dict[str, float]:
        """每个候选块的**词面覆盖率**。

        token 直接读 FTS 影子表里存的那串（`index_source` 的产物）——**不重新分词**：
        重新分词可能与建索引时那次的切法不同，覆盖率就名不副实了。
        """

        tokens = set(query_tokens(query))
        if not tokens or not chunk_ids:
            return {chunk_id: 0.0 for chunk_id in chunk_ids}
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = self._connection.execute(
            f"SELECT chunk_id, tokens FROM material_chunks_fts WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
        coverage: dict[str, float] = {}
        for row in rows:
            doc_tokens = set(str(row["tokens"]).split())
            coverage[str(row["chunk_id"])] = len(tokens & doc_tokens) / len(tokens)
        return {chunk_id: coverage.get(chunk_id, 0.0) for chunk_id in chunk_ids}

    def _knn_scores(
        self, project_id: str, owner: str, query: str
    ) -> dict[str, float]:
        """KNN 分支：**块 id → 真余弦**（query 向量 → vec0 按距离扫 → 换算）。

        ## 为什么给余弦而不是名次（2026-10-19 定的）

        凸组合融合要的是**可比的分数幅度**。名次归一化把「第 1 名」与「第 200 名」
        压成 1.0 与 0.005——幅度整个丢了，这正是 RRF 一族（纯名次函数）的病根
        （TOIS 2023：凸组合 > RRF，in/out-of-domain 都赢）。
        余弦保住了"差多远"，于是"词面完全命中"与"语义很近"可以**在同一个尺度上比**。

        ## 余弦怎么来

        两个 embedder 都**已归一**（实测 L2 范数 1.0：fastembed 默认归一，
        `FakeEmbedder` 也显式归一）→ L2 距离 `d` 与余弦的关系是 `cos = 1 - d²/2`。
        vec0 的 KNN 查询可以直接取 `distance` 列（实测可用）。
        """
        try:
            vector = self._embedder.embed_many([query])[0]
        except Exception as error:  # noqa: BLE001 —— 模型/推理失败就退化成纯 FTS5
            logger.warning("向量检索失败，退回纯 FTS5：%s", error)
            return {}
        try:
            from sqlite_vec import serialize_float32

            rows = self._connection.execute(
                "SELECT chunk_id, distance FROM ("
                " SELECT chunk_id, distance FROM material_chunks_vec"
                " WHERE embedding MATCH ? ORDER BY distance LIMIT ?"
                ") WHERE chunk_id IN ("
                " SELECT c.chunk_id FROM material_chunks c"
                " JOIN materials m ON m.material_id = c.material_id"
                " WHERE m.project_id = ? AND c.owner_id = ?)",
                (serialize_float32(list(vector)), _KNN_SCAN_LIMIT, project_id, owner),
            ).fetchall()
        except Exception as error:  # noqa: BLE001 —— 扩展没载/表不在都退化，不炸检索
            logger.warning("向量索引不可用，退回纯 FTS5：%s", error)
            return {}
        scores: dict[str, float] = {}
        for row in rows:
            distance = float(row["distance"])
            scores[str(row["chunk_id"])] = 1.0 - (distance * distance) / 2.0
        return scores

    def _hits_for(self, chunk_ids, project_id: str, owner: str, tokens: list[str]):
        """给"只在 KNN 里出现"的块造 `MaterialHit`（复用 `_excerpt` 的锚定规则）。"""
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = self._connection.execute(
            "SELECT c.chunk_id, c.material_id, c.text, c.anchor, m.title, m.source_ref"
            " FROM material_chunks c JOIN materials m ON m.material_id = c.material_id"
            f" WHERE c.chunk_id IN ({placeholders})"
            " AND m.project_id = ? AND c.owner_id = ?",
            (*chunk_ids, project_id, owner),
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
            )
        return hits
