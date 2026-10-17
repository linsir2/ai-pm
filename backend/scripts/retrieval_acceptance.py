#!/usr/bin/env python3
"""材料检索升级的**验收实测**（2026-10-17）：v1(LIKE) vs v2(FTS5+jieba+OR)。

## 它证明什么

1. **有效性**：v2 找到了 v1 找不到的（词序无关、插停用词、按标题）。
2. **无倒退**：v1 能找到的（逐字子串），v2 照样能找到。
3. **代码正确**：跑的是**真实的两个实现类**（`SqliteLikeRetriever` / `SqliteFtsRetriever`）
   和**真实的入库路径**（`SqliteMaterialStore` 建 FTS 索引），不是重写一份逻辑。

## 语料

真库 `observations` 的**真实中文文本**（46 条）：`title` 当材料标题、`detail` 当块正文，
经真实 `SqliteMaterialStore` 灌进一个临时 SQLite —— 索引由生产代码建。
（当前真库没有上传材料，故用真实文本当检索语料；这是代理，不是伪造。）

## 查询

每篇造 4 类（都从文档自身反推，保证"词面可命中"）：
- **逐字子串**：正文里连续一段 —— v1 该中；
- **同词换序**：那段的分词倒序 —— v1 必挂；
- **插停用词**：分词后词间插"与" —— v1 必挂；
- **按标题**：标题里一段 —— v1 必挂（v1 只搜正文，标题不在正文里）。

## 跑法

    cd backend && set -a && . ./.env && set +a && python3 scripts/retrieval_acceptance.py

数字写进 `docs/plans/2026-10-17-检索升级验收.md`。**跑完验证正确，才允许删 v1**。
"""

import itertools
import os
import re
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

from pmstudio.domain.shared.ids import ProjectId, UserId  # noqa: E402
from pmstudio.infrastructure.sqlite import database, owner_scope  # noqa: E402
from pmstudio.infrastructure.sqlite.materials import (  # noqa: E402
    SqliteFtsRetriever,
    SqliteMaterialStore,
)

# v1（`SqliteLikeRetriever`）**已按本文件的数字删除**。保留自动探测：它在时跑对照，
# 不在时跳过 v1 那一列——这样脚本在删类后**仍可复现 v2**，对照数字以 README.md 为准。
try:
    from pmstudio.infrastructure.sqlite.materials import SqliteLikeRetriever  # noqa: F401
    _HAS_V1 = True
except ImportError:
    _HAS_V1 = False

OWNER = UserId("u-retrieval-acceptance")
PROJECT = ProjectId("p-retrieval-acceptance")


class _SeqIds:
    def __init__(self) -> None:
        self._n = itertools.count(1)

    def new(self, prefix: str) -> str:
        return f"{prefix}{next(self._n)}"


def _queries(detail: str, title: str) -> dict[str, str]:
    """从一篇文档反推 4 类查询。"""

    seg = ""
    for part in re.split(r"[。，、；：（）【】?？!！\s]+", detail):
        part = re.sub(r"[A-Za-z0-9_./·—\-]+", "", part)
        if 8 <= len(part) <= 14:
            seg = part
            break
    from pmstudio.infrastructure.sqlite.tokenize import query_tokens

    out: dict[str, str] = {}
    if seg:
        toks = query_tokens(seg)
        out["逐字子串"] = seg
        out["同词换序"] = "".join(reversed(toks))
        out["插停用词"] = toks[0] + "与" + "".join(toks[1:])
    tseg = ""
    for part in re.split(r"[·\s]+", title):
        part = re.sub(r"[A-Za-z0-9_./·—\-]+", "", part)
        if len(part) >= 4:
            tseg = part
            break
    if tseg:
        out["按标题"] = tseg
    return out


def main() -> int:
    db = os.environ.get("PMSTUDIO_DB") or os.path.expanduser("~/.pmstudio/pmstudio.db")
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    obs = src.execute("select title, detail from observations").fetchall()
    if not obs:
        print("没有语料", file=sys.stderr)
        return 2

    owner_scope.set_owner_scope(OWNER)
    kinds = ["逐字子串", "同词换序", "插停用词", "按标题"]
    cases: list[tuple[str, str, str]] = []  # (kind, query, material_id)

    with tempfile.TemporaryDirectory() as tmp:
        connection = database.connect(Path(tmp) / "acc.db")
        store = SqliteMaterialStore(connection, ids=_SeqIds())
        for i, row in enumerate(obs):
            from pmstudio.application.dto import MaterialChunk, ParsedMaterial

            parsed = ParsedMaterial(
                origin="upload",
                title=row["title"],
                source_ref=f"attachment:obs-{i}",
                media_type="text/markdown",
                bytes_len=1,
                chunks=(
                    MaterialChunk(
                        chunk_id="local#c00",
                        ordinal=0,
                        text=row["detail"],
                        heading_path=(),
                        anchor="第 1 行",
                    ),
                ),
                parser_id="local-text",
                parser_version="v1",
            )
            packet = store.put(
                project_id=PROJECT,
                parsed=parsed,
                sha256=f"obs-{i}".ljust(64, "0")[:64],
                content=b"x",
                now=datetime.now(UTC),
            )
            for kind, query in _queries(row["detail"], row["title"]).items():
                if query.strip():
                    cases.append((kind, query, packet.material_id))

        v1 = SqliteLikeRetriever(connection) if _HAS_V1 else None
        v2 = SqliteFtsRetriever(connection)

        def hit(retriever, query, material_id, limit=5) -> tuple[bool, bool]:
            got = [h.material_id for h in retriever.search(
                project_id=PROJECT, query=query, limit=limit)]
            return (got[:1] == [material_id], material_id in got)

        stats = {
            k: {"v1_top1": 0, "v1_r5": 0, "v2_top1": 0, "v2_r5": 0, "n": 0}
            for k in kinds
        }
        for kind, query, mid in cases:
            u1, u5 = hit(v2, query, mid)
            s = stats[kind]
            s["n"] += 1
            s["v2_top1"] += u1
            s["v2_r5"] += u5
            if v1 is not None:
                t1, r5 = hit(v1, query, mid)
                s["v1_top1"] += t1
                s["v1_r5"] += r5
        connection.close()

    print(f"语料 {len(obs)} 条 · 查询 {len(cases)} 个"
          + ("" if _HAS_V1 else "（v1 已删除，跳过对照列）") + "\n")
    v1cols = _HAS_V1
    head_v1 = f"| {'v1(LIKE) top1':>14} {'v1 recall@5':>12} " if v1cols else ""
    print(f"{'查询类型':10} {'n':>3} {head_v1}| {'v2(FTS) top1':>13} {'v2 recall@5':>12}")
    tot = {kk: 0 for kk in ("v1_top1", "v1_r5", "v2_top1", "v2_r5", "n")}
    for k in kinds:
        s = stats[k]
        for kk in tot:
            tot[kk] += s[kk]
        cols = f"| {s['v1_top1']:>14} {s['v1_r5']:>12} " if v1cols else ""
        print(f"{k:10} {s['n']:>3} {cols}| {s['v2_top1']:>13} {s['v2_r5']:>12}")
    cols = f"| {tot['v1_top1']:>14} {tot['v1_r5']:>12} " if v1cols else ""
    print(f"{'合计':10} {tot['n']:>3} {cols}| {tot['v2_top1']:>13} {tot['v2_r5']:>12}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
