#!/usr/bin/env python3
"""材料检索升级的选型实验（2026-10-17）：在**真实中文文本**上量 LIKE vs jieba-FTS。

## 目的

`docs/plans/2026-10-17-检索升级-方案.md` §5 的 3 个调优项（AND/OR、停用词、jieba vs trigram）
原本"待语料"。本脚本拿真库里的真实中文文本（observations）当语料，直接量出来，把"猜"换成"数"。

## 方法

语料 = 真库 `observations` 的 title+detail（46 条、8082 字，纯中文）。
每篇按标点切一段纯中文 8–14 字，造 3 类查询：
- **逐字子串**：原样那一段（LIKE 该命中）
- **同词换序**：把那段的分词倒序拼回（LIKE 必挂、词级倒排该命中）
- **插停用词**：分词后词间插"与"（LIKE 挂、AND 可能挂、OR 该命中）
量 top1 与 recall@5。15 篇 × 3 类 = 45 个查询。

## 跑法

    cd backend && set -a && . ./.env && set +a && python3 \
        scripts/retrieval_experiment.py

## 结论（2026-10-17 实测）

| 方法 | top1 | recall@5 |
|---|---|---|
| LIKE（现状 v1） | 10/45 | 15/45 |
| jieba-FTS AND | 36/45 | 39/45 |
| **jieba-FTS OR** | **39/45** | **45/45** |
| trigram | 10/45 | 15/45 |

→ 查询用 **OR**；停用词**不处理**；**trigram ≡ LIKE**（没改善），jieba 唯一有效。
"""

import os
import random
import re
import sqlite3
import sys

import jieba

jieba.setLogLevel(60)


def tokens(text: str) -> list[str]:
    return [t for t in jieba.cut_for_search(text) if t.strip()]


def main() -> int:
    db = os.environ.get("PMSTUDIO_DB") or os.path.expanduser("~/.pmstudio/pmstudio.db")
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    docs = [
        (row["title"] + "。" + row["detail"])
        for row in src.execute("select title, detail from observations")
    ]
    if not docs:
        print("没有语料（observations 为空）", file=sys.stderr)
        return 2

    index = sqlite3.connect(":memory:")
    index.execute("CREATE VIRTUAL TABLE fts USING fts5(tokens, tokenize='unicode61')")
    index.execute("CREATE VIRTUAL TABLE tri USING fts5(body, tokenize='trigram')")
    for i, doc in enumerate(docs, 1):
        index.execute(
            "INSERT INTO fts(rowid, tokens) VALUES(?,?)", (i, " ".join(tokens(doc)))
        )
        index.execute("INSERT INTO tri(rowid, body) VALUES(?,?)", (i, doc))

    def like(q, limit=5):
        return [i for i, d in enumerate(docs, 1) if q in d][:limit]

    def fts(expr, limit=5):
        try:
            return [
                r[0]
                for r in index.execute(
                    "SELECT rowid, bm25(fts) r FROM fts WHERE fts MATCH ? ORDER BY r LIMIT ?",
                    (expr, limit),
                ).fetchall()
            ]
        except sqlite3.OperationalError:
            return []

    def tri(q, limit=5):
        try:
            return [
                r[0]
                for r in index.execute(
                    "SELECT rowid FROM tri WHERE tri MATCH ? LIMIT ?", (f'"{q}"', limit)
                ).fetchall()
            ]
        except sqlite3.OperationalError:
            return []

    def q_and(q):
        return " ".join(f'"{t}"' for t in tokens(q))

    def q_or(q):
        return " OR ".join(f'"{t}"' for t in tokens(q))

    random.seed(11)
    cases: dict[str, list[tuple[str, int]]] = {"逐字": [], "换序": [], "插停用词": []}
    for i, doc in enumerate(docs, 1):
        for seg in re.split(r"[。，、；：（）【】?？!！\s]+", doc):
            seg = re.sub(r"[A-Za-z0-9_./·—\-]+", "", seg)
            if 8 <= len(seg) <= 14 and len(tokens(seg)) >= 2:
                parts = tokens(seg)
                cases["逐字"].append((seg, i))
                cases["换序"].append(("".join(reversed(parts)), i))
                cases["插停用词"].append((parts[0] + "与" + "".join(parts[1:]), i))
                break
    for key in cases:
        cases[key] = cases[key][-15:]

    def report(name, fn) -> None:
        print(f"\n== {name} ==")
        tot1 = tot5 = total = 0
        for kind, items in cases.items():
            top1 = sum(1 for q, want in items if fn(q)[:1] == [want])
            rec5 = sum(1 for q, want in items if want in fn(q))
            total += len(items)
            tot1 += top1
            tot5 += rec5
            print(f"    {kind:6} top1={top1}/{len(items)}  recall@5={rec5}/{len(items)}")
        print(f"    合计 top1={tot1}/{total}  recall@5={tot5}/{total}")

    print(f"语料 {len(docs)} 条 · 样本 {len(cases['逐字'])} 篇 × 3 类")
    report("LIKE（现状）", like)
    report("jieba-FTS AND", lambda q: fts(q_and(q)))
    report("jieba-FTS OR", lambda q: fts(q_or(q)))
    report("trigram（无 jieba）", tri)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
