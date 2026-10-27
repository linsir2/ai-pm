"""**统一检索基线**：一个入口、一张表、六个指标、四类查询（批 8，2026-10-21）。

## 为什么要有这个

现状：判据散在 **8+ 个脚本的头注释**里、语料散在 **`.codex/runs/`（gitignore）**里
（新会话无法从代码知道有哪些语料）、指标只有 **top1 + recall@5**（没有假阳性率、
没有块级）、缺 **跨块证据** 与 **真实用户措辞** 两类查询。改造无从下手，
改造完也没法说清"好了多少"。

本脚本把基线**收拢成一处**：语料登记在下方「语料登记表」，判据在「判据表」，
跑法在「用法」。

## 语料登记表（改语料/加语料时改这里）

| 名字 | 条数 | 怎么造的 |
|---|---|---|
| `web_raw` | 15 份材料 | 真 Tavily 整页 markdown（`tavily_raw/C raw_content-净.json`） |
| 词面查询 | 45 | 词取自页面真实内容**中段**（`tavily_raw/frozen_queries.json`） |
| 改述查询 | 14 | **手写**同义改述（`tavily_raw/paraphrases.json`） |
| 负样本 | 8 | 语料里**根本没有**的概念（本脚本内 `NEGATIVE_QUERIES`） |
| 跨块查询 | 15 | **自动生成**：同一材料相距 4 块的两处各取一词 |

## 判据表（阈值写在这里，跑完不许改）

见 `docs/plans/2026-10-21-批8-检索基线与调优.md` 的 RIPR 表；本脚本只负责**量**。

## 指标（六个）

1. **材料级 recall@5**：目标材料在前 5 里吗（用户看到的是"材料 + 片段"）
2. **块级 recall@5**：**目标块**在前 5 里吗（答案到底找没找对位置）
3. **MRR**：目标材料的倒数名次均值（比"名次均"稳）
4. **假阳性率**：负样本被捞回的比例（**库里没有却给了候选**）
5. **跨块证据完整率**：两个证据块是否都在前 5 里（量"按材料去重"吃掉了多少）
6. **成本**：块数 / 向量块数 / 每轮向量耗时

## 用法

```bash
cd backend && HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \\
    uv run python -u scripts/retrieval_bench.py                 # 基线
    uv run python -u scripts/retrieval_bench.py --scan chunk     # 块宽扫描
    uv run python -u scripts/retrieval_bench.py --scan prefix    # 上下文前缀消融
    uv run python -u scripts/retrieval_bench.py --scan candidates # 候选数扫描
    uv run python -u scripts/retrieval_bench.py --scan alpha     # 融合 α 扫描
```
"""

import hashlib
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")

import jieba

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.infrastructure.sqlite import materials as materials_module
from pmstudio.infrastructure.sqlite import tokenize as tokenize_module
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
from pmstudio.infrastructure.sqlite.materials import (
    SqliteFtsRetriever,
    SqliteHybridRetriever,
    SqliteMaterialStore,
    dedup_by_material,
)
from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
from pmstudio.infrastructure.tools.extract import chunk_text
from pmstudio.infrastructure.tools.webclean import normalize_web_text

OUT = Path("../.codex/runs/2026-10-19/tavily_raw")
PROJECT, OWNER = "p1", "o1"

#: **负样本**：语料（15 份 RAG/推理类材料）里**根本没有**的概念。
#: 必须是真实的用户会输的词（不是乱码）——它们代表"搜了一个库里没有的东西"。
NEGATIVE_QUERIES = [
    "postgresql 索引 优化",
    "rust 所有权 借用检查",
    "嵌入式 电机 驱动",
    "邮件协议 DKIM 签名",
    "webpack 打包 体积",
    "汇编 寄存器 溢出",
    "kubernetes 探针 滚动更新",
    "latex 参考文献 格式",
]


def tokens(text: str) -> list[str]:
    """与 bench 其它脚本一致的分词（jieba，≥2 字、去纯标点）。"""
    words: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"^\s*#{1,6}\s*", "", raw)
        line = re.sub(r"^\s*[-*+]\s+", "", line)
        line = re.sub(r"^\s*>\s*", "", line)
        for word in jieba.cut(line):
            word = word.strip()
            if len(word) >= 2 and re.match(r"^[\w一-鿿]+$", word):
                words.append(word)
    return words


def build_index(items, embedder, *, chunk_chars=400, db_path=None, index_text=None):
    """按生产路径建库（真材料仓 + 真向量索引 + 真 FTS5）。"""
    path = db_path or (OUT / f"bench_{chunk_chars}.db")
    if path.exists():
        path.unlink()
    connection = connect(path, embedder=embedder)
    store = SqliteMaterialStore(connection, ids=UuidIdGenerator())
    vector_index = SqliteVectorIndex(connection, embedder=embedder)
    original = tokenize_module.index_text
    if index_text is not None:
        tokenize_module.index_text = index_text
    try:
        url2mid: dict[str, str] = {}
        mid2chunks: dict[str, list[str]] = {}
        nblocks = 0
        for item in items:
            clean = normalize_web_text(item["text"])
            chunks = chunk_text(clean, parser_id="tavily", max_chars=chunk_chars)
            nblocks += len(chunks)
            parsed = ParsedMaterial(
                origin="web_search",
                title=item["title"],
                source_ref=item["url"],
                media_type="text/html",
                bytes_len=len(item["text"].encode()),
                chunks=tuple(
                    MaterialChunk(
                        chunk_id=f"c{index:02d}",
                        ordinal=index,
                        text=chunk.text,
                        heading_path=chunk.heading_path,
                        anchor=chunk.anchor,
                    )
                    for index, chunk in enumerate(chunks)
                ),
                parser_id="tavily",
                parser_version="bench",
            )
            packet = store.put(
                project_id=PROJECT,
                parsed=parsed,
                sha256=hashlib.sha256(item["text"].encode()).hexdigest(),
                content=item["text"].encode(),
                now=datetime(2026, 10, 19, tzinfo=UTC),
                owner=OWNER,
            )
            vector_index.write(packet.material_id)
            url2mid[item["url"]] = packet.material_id
            mid2chunks[packet.material_id] = [chunk.text for chunk in chunks]
    finally:
        tokenize_module.index_text = original
    connection.commit()
    return connection, url2mid, mid2chunks, nblocks


def make_queries(items, url2mid, mid2chunks):
    """四类查询。返回 (词面, 改述, 跨块, 负样本)。"""
    frozen = json.loads((OUT / "frozen_queries.json").read_text(encoding="utf-8"))
    lexical = [(w, url) for w, _q, url in frozen["items"]]
    para = json.loads((OUT / "paraphrases.json").read_text(encoding="utf-8"))["paraphrases"]
    url_by_index = {index: item["url"] for index, item in enumerate(items)}
    paraphrase = []
    for index, queries in sorted(para.items(), key=lambda kv: int(kv[0])):
        for query in queries:
            paraphrase.append((query, url_by_index[int(index)]))
    # **跨块**：同一材料的两个**相隔几块**的位置各取一个词 → 答案必须两块拼。
    # 相距 4 块（不是 60 块）：那是"同一节里散着两处答案"的真实场景
    # （第一批我取的是第 2 块与倒数第 3 块 → 相距 60 块 → 那是极端场景，
    #  会把结论推向"跨块不可得"——**查询集造得太极端本身就是测量错误**）。
    # 语料词频（用来挑"稀有词"）
    freq: dict[str, int] = {}
    for item in items:
        for word in set(tokens(item["text"])):
            freq[word] = freq.get(word, 0) + 1
    common = {word for word, count in freq.items() if count >= len(items) // 2}
    cross = []
    for item in items:
        chunks = mid2chunks[url2mid[item["url"]]]
        if len(chunks) < 16:
            continue
        first = tokens(chunks[6])
        second = tokens(chunks[10])
        if not first or not second:
            continue
        # **取语料里最稀有的词**（高 IDF → BM25 区分度高 → 目标块能被排进去）。
        # 前两版取的是 jieba 的**常见词**（"数据""模型"…）→ 到处都出现 → BM25 区分度
        # 低 → 目标块根本排不进 top5 → 完整率恒 0。**那是查询造得太弱，不是系统的缺陷。**
        first = [w for w in first if w not in common]
        second = [w for w in second if w not in common]
        if not first or not second:
            continue
        mid = url2mid[item["url"]]
        first.sort(key=lambda w: freq.get(w, 0))
        second.sort(key=lambda w: freq.get(w, 0))
        cross.append(
            (
                f"{first[0]} {second[0]}",
                item["url"],
                # **精确的目标块 id**（我取词的那两块）——不能定义成"含该词的所有块"：
                # 那个词在导航/目录里也会出现 → 目标集合几十个 → 判定过严 → 指标失真。
                (f"{mid}#c06", f"{mid}#c10"),
            )
        )
    return lexical, paraphrase, cross, list(NEGATIVE_QUERIES)


def p_sign_test(wins: int, losses: int) -> float:
    """**精确二项检验（双侧）**：混合赢 `wins` 条、词面赢 `losses` 条时的 p 值。

    为什么用符号检验（而不是 t 检验）：配对差是**二值的**（命中/没命中），
    n 小时 t 检验不靠谱（n=14 的正态近似会给出假显著）；符号检验只看**方向**，
    对小样本是稳的。

    p = 2 · Σ_{k≤min(wins,losses)} C(wins+losses, k) / 2^(wins+losses)
    （**下尾** × 2 = 双侧；超过 1 截到 1。两边都不一致时 p = 1.0：没有方向性证据。）

    纯函数：不依赖模型、不依赖语料 → 可单测 ✓（2026-10-22 批 13，R9）。
    """
    from math import comb

    if wins <= 0 and losses <= 0:
        return 1.0
    total = wins + losses
    tail = sum(comb(total, k) for k in range(min(wins, losses) + 1))
    return min(1.0, 2 * tail / (2**total))


def paired_increment(lexical_ok: list[bool], hybrid_ok: list[bool]) -> dict[str, float]:
    """配对增量：同一批查询上，混合相对词面的**净变化**。

    `lexical_ok[i]` / `hybrid_ok[i]` = 第 i 条查询在**词面** / **混合**下是否命中目标。
    返回：Δ（净增）、wins（混合赢）、losses（词面赢）、p（符号检验的 p 值）、n。

    为什么必须是**配对**：同一批查询、同一个语料、同一个切法 → 唯一的差别是融合
    → 增量归因干净。分开算两个命中率再相减，会把语料/查询的方差混进去 ✗
    （2026-10-22 批 13，R9）。
    """
    if len(lexical_ok) != len(hybrid_ok):
        raise ValueError("两组结果必须逐条配对")
    wins = sum(1 for lex, hyb in zip(lexical_ok, hybrid_ok, strict=True) if hyb and not lex)
    losses = sum(1 for lex, hyb in zip(lexical_ok, hybrid_ok, strict=True) if lex and not hyb)
    delta = sum(
        (1 if hyb else 0) - (1 if lex else 0)
        for lex, hyb in zip(lexical_ok, hybrid_ok, strict=True)
    )
    return {
        "Δ": delta,
        "wins": wins,
        "losses": losses,
        "p": p_sign_test(wins, losses),
        "n": len(lexical_ok),
    }


def rescue_rate_from_samples(samples: list[dict]) -> dict[str, float]:
    """从**真实检索样本**算救回率与挤掉率（没有 ground truth 也能算）。

    样本来自检索器的观测行（`material_search … fts_hits=… hybrid_hits=…`）：
    - **救回**：词面一个都没命中，混合命中了 → 语义把它捞了回来 ✓
    - **挤掉**：词面命中了，混合一个都没命中 → 融合把好结果掀翻了 ✗（倒退风险）

    注意：救回**不等于**捞对了（可能捞回的是无关材料）→ 精确率那一侧由**负样本集**
    与**合成查询的配对增量**负责（2026-10-22 批 13，R9）。
    """
    # 顶栏搜索的样本（caller=search）没有词面/语义分解 → 只做查询分布，不进救回率。
    usable = [
        item for item in samples if "fts_hits" in item and "hybrid_hits" in item
    ]
    total = len(usable)
    rescued = sum(1 for item in usable if item["fts_hits"] == 0 and item["hybrid_hits"] > 0)
    pushed_out = sum(
        1 for item in usable if item["fts_hits"] > 0 and item["hybrid_hits"] == 0
    )
    return {
        "n": total,
        "救回率": rescued / total if total else 0.0,
        "挤掉率": pushed_out / total if total else 0.0,
    }


def load_samples() -> list[dict]:
    """读最新一份**真实检索样本**（R8 攒的）。没有就空列表（不影响基线）。

    样本是检索器的观测行（`material_search …`）→ 里面有**词面命中数**与
    **混合命中数** → 能算救回率/挤掉率（`rescue_rate_from_samples`）。
    位置：`.codex/runs/<日期>/search_samples.json`（**gitignored**，不入库）。
    """
    files = sorted(Path("../.codex/runs").glob("*/search_samples.json"))
    if not files:
        return []
    payload = json.loads(files[-1].read_text(encoding="utf-8"))
    return list(payload.get("samples", []))


def measure(connection, embedder, url2mid, mid2chunks, queries, *, limit=5, samples=None):
    """量六个指标 + 配对增量（R9）。

    **词面基线按同样的材料去重口径算**（`dedup_by_material`）：混合路一份材料只占
    1–2 个槽，词面基线若不这样去重，同一份材料的 5 个块会占满 top5
    → **增量被高估** → 结论会错（2026-10-22 批 13）。
    """
    lexical, paraphrase, cross, negatives = queries
    hybrid = SqliteHybridRetriever(connection, embedder=embedder)
    fts_only = SqliteFtsRetriever(connection, caller="bench")

    def target_chunks(mid: str, word: str) -> set[str]:
        """目标词所在的**所有**块（该词可能在好几块里都出现）。

        为什么是"所有"而不是"第一块"：词面查询的词取自页面中段，它在那一页里
        可能出现多次（导航/目录/正文都提到）——只认第一块会把"找对了材料但
        命中的是另一处提及"误判成失败。
        """
        return {
            f"{mid}#c{ordinal:02d}"
            for ordinal, text in enumerate(mid2chunks.get(mid, []))
            if word in text
        }

    out: dict[str, float] = {}
    for name, group in (("词面", lexical), ("改述", paraphrase)):
        material_hits = chunk_hits = 0
        block_total = 0
        rrs: list[float] = []
        lexical_ok: list[bool] = []
        hybrid_ok: list[bool] = []
        for query, url in group:
            mid = url2mid.get(url)
            if mid is None:
                continue
            hits = hybrid.search(project_id=PROJECT, query=query, limit=limit, owner=OWNER)
            ids = [hit.material_id for hit in hits]
            # 词面基线：**同口径**（同样的材料去重）→ 增量归因干净
            base_ids = [
                hit.material_id
                for hit in dedup_by_material(
                    fts_only.search(
                        project_id=PROJECT, query=query, limit=limit, owner=OWNER
                    ),
                    limit,
                )
            ]
            lexical_ok.append(mid in base_ids)
            hybrid_ok.append(mid in ids)
            if mid in ids:
                material_hits += 1
                rrs.append(1.0 / (ids.index(mid) + 1))
            # 块级：目标词所在的**任一**块在前 5 里就算对
            wanted = target_chunks(mid, query)
            if wanted:
                block_total += 1
                if any(hit.chunk_id in wanted for hit in hits):
                    chunk_hits += 1
        n = max(1, len(group))
        out[f"{name}_材料r5"] = material_hits / n
        out[f"{name}_块r5"] = chunk_hits / max(1, block_total)
        out[f"{name}_块r5n"] = block_total
        out[f"{name}_MRR"] = sum(rrs) / max(1, len(rrs))
        increment = paired_increment(lexical_ok, hybrid_ok)
        out[f"{name}_增量Δ"] = increment["Δ"]
        out[f"{name}_增量赢"] = increment["wins"]
        out[f"{name}_增量输"] = increment["losses"]
        out[f"{name}_增量p"] = increment["p"]
    # 负样本 → 假阳性率
    false_positive = 0
    for query in negatives:
        hits = hybrid.search(project_id=PROJECT, query=query, limit=limit, owner=OWNER)
        if hits:
            false_positive += 1
    out["假阳性率"] = false_positive / max(1, len(negatives))
    # 跨块 → 证据完整率（两个证据块都在前 5 里）
    complete = 0
    for query, url, wanted in cross:
        mid = url2mid.get(url)
        if mid is None:
            continue
        wanted = set(wanted)
        hits = hybrid.search(project_id=PROJECT, query=query, limit=limit, owner=OWNER)
        got = {hit.chunk_id for hit in hits}
        if wanted <= got:
            complete += 1
    out["跨块证据完整率"] = complete / max(1, len(cross))
    # **真实检索样本**（R8 攒的）：没有 ground truth → 只能算救回率/挤掉率
    rescue = rescue_rate_from_samples(
        list(samples) if samples is not None else load_samples()
    )
    out["真查询n"] = rescue["n"]
    out["真查询救回率"] = rescue["救回率"]
    out["真查询挤掉率"] = rescue["挤掉率"]
    out["_词面n"] = len(lexical)
    out["_改述n"] = len(paraphrase)
    out["_跨块n"] = len(cross)
    out["_负样本n"] = len(negatives)
    return out


def show(title: str, out: dict[str, float], *, extra: str = "") -> None:
    print(f"\n{title}{extra}")
    print(
        f"  词面: 材料r5 {out['词面_材料r5']:.3f} · 块r5 {out['词面_块r5']:.3f}"
        f" · MRR {out['词面_MRR']:.3f}   (n={out['_词面n']})"
    )
    block = (
        f"{out['改述_块r5']:.3f}"
        if out.get("改述_块r5n")
        else "—（改述的词不在正文里 → 目标块未定义）"
    )
    print(
        f"  改述: 材料r5 {out['改述_材料r5']:.3f} · 块r5 {block}"
        f" · MRR {out['改述_MRR']:.3f}   (n={out['_改述n']})"
    )
    print(
        f"  跨块证据完整率 {out['跨块证据完整率']:.3f} (n={out['_跨块n']})"
        f" · 假阳性率 {out['假阳性率']:.3f} (n={out['_负样本n']})"
    )
    for name in ("词面", "改述"):
        print(
            f"  {name}配对增量: Δ {out[f'{name}_增量Δ']:+.0f}"
            f" · 混合赢 {out[f'{name}_增量赢']:.0f} / 词面赢 {out[f'{name}_增量输']:.0f}"
            f" · p={out[f'{name}_增量p']:.3f}"
        )
    if out.get("真查询n"):
        print(
            f"  真查询样本: n={out['真查询n']:.0f}"
            f" · 救回率 {out['真查询救回率']:.3f} · 挤掉率 {out['真查询挤掉率']:.3f}"
        )


def main() -> None:
    items = json.loads((OUT / "C raw_content-净.json").read_text(encoding="utf-8"))
    embedder = FastEmbedEmbedder()
    scan = ""
    for index, arg in enumerate(sys.argv):
        if arg == "--scan" and index + 1 < len(sys.argv):
            scan = sys.argv[index + 1]

    if scan == "chunk":
        print("=== 块宽扫描（每档重建索引）===")
        for width in (200, 300, 400, 500, 600):
            connection, url2mid, mid2chunks, nblocks = build_index(
                items, embedder, chunk_chars=width
            )
            queries = make_queries(items, url2mid, mid2chunks)
            out = measure(connection, embedder, url2mid, mid2chunks, queries)
            show(f"块宽 {width}", out, extra=f"  · {nblocks} 块")
            connection.close()
        return

    if scan == "prefix":
        print("=== 上下文前缀消融（向量输入里带不带标题/章节链）===")
        variants = {
            "现状 标题+章节链": None,
            "只有标题": lambda **kw: kw["title"] + "\n" + kw["text"],
            "只有章节链": lambda **kw: " ".join(kw["heading_path"]) + "\n" + kw["text"],
            "都不要": lambda **kw: kw["text"],
        }
        for label, variant in variants.items():
            connection, url2mid, mid2chunks, nblocks = build_index(
                items, embedder, index_text=variant
            )
            queries = make_queries(items, url2mid, mid2chunks)
            out = measure(connection, embedder, url2mid, mid2chunks, queries)
            show(label, out)
            connection.close()
        return

    connection, url2mid, mid2chunks, nblocks = build_index(items, embedder)
    queries = make_queries(items, url2mid, mid2chunks)

    if scan == "candidates":
        print("=== 候选数扫描（FTS 候选 / KNN 扫描）===")
        for fts_candidates, knn_scan in ((20, 200), (50, 200), (20, 500), (50, 500)):
            materials_module._FTS_CANDIDATES = fts_candidates
            materials_module._KNN_SCAN_LIMIT = knn_scan
            out = measure(connection, embedder, url2mid, mid2chunks, queries)
            show(f"FTS {fts_candidates} / KNN {knn_scan}", out)
        materials_module._FTS_CANDIDATES = 20
        materials_module._KNN_SCAN_LIMIT = 200
        return

    if scan == "slots":
        print("=== 每份材料的槽位数扫描（现在恒为 1 → 跨块证据被吃掉）===")
        for limit in (5, 50):
            for slots in (1, 2, 3):
                materials_module._MATERIAL_SLOTS = slots
                out = measure(
                    connection, embedder, url2mid, mid2chunks, queries, limit=limit
                )
                show(f"limit {limit} · 每份材料 {slots} 槽", out)
        materials_module._MATERIAL_SLOTS = 1
        return

    if scan == "slots5":
        print("=== 每份材料的槽位数扫描（现在恒为 1 → 跨块证据被吃掉）===")
        for slots in (1, 2, 3):
            materials_module._MATERIAL_SLOTS = slots
            out = measure(connection, embedder, url2mid, mid2chunks, queries)
            show(f"每份材料 {slots} 槽", out)
        materials_module._MATERIAL_SLOTS = 1
        return

    if scan == "alpha":
        print("=== 融合 α 扫描 ===")
        for alpha in (0.3, 0.4, 0.5, 0.6, 0.7):
            materials_module.FUSION_ALPHA = alpha
            out = measure(connection, embedder, url2mid, mid2chunks, queries)
            show(f"α={alpha}", out)
        materials_module.FUSION_ALPHA = 0.5
        return

    print("=== 基线（当前生产参数）===")
    start = time.perf_counter()
    out = measure(connection, embedder, url2mid, mid2chunks, queries)
    elapsed = time.perf_counter() - start
    show("基线", out, extra=f"  · {nblocks} 块 · 检索耗时 {elapsed:.1f}s")
    (OUT / "retrieval_baseline.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    connection.close()


if __name__ == "__main__":
    main()
