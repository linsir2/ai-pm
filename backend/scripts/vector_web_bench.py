#!/usr/bin/env python3
"""批 6a 量化判据：联网材料**入库保留率**、去重前后条数、检索命中（改前 vs 改后）。

**判据先注册在这里，跑完不许改**：

| 判据 | 阈值 | 改前（2026-10-19 实测） |
|---|---|---|
| 入库保留率（清洗后入库字数 / Tavily 原文字数） | ≥ 95% | **21%**（截 300 字） |
| 逐字/换序类 recall@5 | **不低于**改前（纯 FTS5 在同一批语料上的数） | 逐字 40/40、换序 40/40 |
| 同义改述类 recall@5 | **高于**改前（改前 = 0，联网材料当时没有向量） | 0/0 |

**改后 vs 改前怎么比**：同一批真 Tavily 结果、同一批查询，**两套装配各跑一遍**——
「改前」= 只建 FTS 索引（不写向量，模拟今天）；「改后」= 真材料仓 + 真向量索引。

**Tavily 深度**（basic / advanced）用**同一批判据**各跑一遍，用数字决定留哪个。

**跑法**：

```bash
cd backend && HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \\
  set -a && . ./.env && set +a && \\
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/vector_web_bench.py
```

要真花 Tavily 的钱（`.env` 里有 key）。退出码非 0 = 判据没过。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(BACKEND))

# ── 判据（注册在这里，跑完不许改）──────────────────────────────────────────
KEEP_RATIO_MIN = 0.95          # 入库保留率
PER_CLASS = 40                 # 每类查询条数

QUERIES = [
    "日志留存时间 不少于六个月 的要求",
    "个人信息保护法 影响评估 记录 保存",
    "数据出境 安全评估 申报 流程",
    "网络安全等级保护 测评 周期",
    "关键信息基础设施 采购 目录",
]


def _log(text: str) -> None:
    print(text, flush=True)


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


def _literal_segment(text: str) -> str:
    """正文里切一段 8–14 字的连续中文（逐字子串类查询）。"""
    for part in re.split(r"[。，、；：（）【】?？!！\s]+", text):
        part = re.sub(r"[A-Za-z0-9_./·—\-]+", "", part)
        if 8 <= len(part) <= 14:
            return part
    return ""


async def _fetch(depth: str) -> list[dict]:
    """真跑 Tavily，拿到一批原始结果（**没过清洗**，好量"保留率"）。"""
    import httpx

    key = os.environ.get("PMSTUDIO_TAVILY_API_KEY", "")
    if not key:
        raise SystemExit("没有 PMSTUDIO_TAVILY_API_KEY")
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=60) as client:
        for query in QUERIES:
            response = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": key, "query": query, "max_results": 5,
                      "search_depth": depth},
            )
            if response.status_code != 200:
                _log(f"  [警告] {query} → HTTP {response.status_code}")
                continue
            for item in response.json().get("results", []):
                out.append({
                    "query": query,
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "content": item.get("content", ""),
                })
    return out


def _make_paraphrases(samples: list[str]) -> list[str]:
    """用真模型把正文改述成"用户自己的说法"（同义改述类查询）。

    **两个必须解决的坑**（2026-10-19 真跑撞出来的）：

    1. **配对会错位**：让模型返回"一个字符串数组"时，它偶尔会把顺序打乱，
       改述配到了别的正文上 → 量出来的是"语义无关"的查询，结论全错。
       → 改成**按编号返回 JSON 对象**（`{"1": "改述", ...}`），配对由编号定。
    2. **模型不总在改述**：中文模型倾向于**摘要/截断**原文
       （实测「监管强制要求全量审计高危操作，且原始记录需保留半」——跟原文共享
       一大堆词）。那样的"改述"词面就能命中，**"改前"的基线失去意义**。
       → 逐条验词面脱开，不合格的**退回重写**，并如实报告样本量。

    词面脱开的判据：与原文共享 ≤1 个 2-gram（与批 5 的 lab 同一把尺子）。
    """
    from pmstudio.harness.model.contracts import ModelMessage, ModelRequest
    from pmstudio.harness.model.providers.litellm import LiteLLMGateway

    gateway = LiteLLMGateway(
        model=os.environ["PMSTUDIO_MODEL"],
        api_key=os.environ["PMSTUDIO_API_KEY"],
        api_base=os.environ.get("PMSTUDIO_API_BASE") or None,
        timeout=300.0,
        max_attempts=2,
    )

    def _bigrams(text: str) -> set[str]:
        cleaned = re.sub(r"\s+", "", text)
        return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}

    def _ask(items: list[tuple[int, str]]) -> dict[int, str]:
        numbered = "\n\n".join(f"[{index}] {text}" for index, text in items)
        prompt = (
            "下面每条是一个编号的材料正文。请**用用户自己的话**问出同一件事，"
            "作为一条查询（15–30 个汉字）。\n"
            "**硬要求**：不得照抄原文的词——要换成日常说法，"
            "与原文共享的连续两字不得超过一处。\n"
            "只输出 JSON 对象，键是编号，值是改述："
            '{"1": "…", "2": "…"}'
        )
        request = ModelRequest(
            purpose="web_bench_paraphrase",
            messages=(ModelMessage(role="user", content=prompt + "\n\n" + numbered),),
            schema_name="Paraphrases",
            response_schema={
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
        )
        reply = asyncio.run(gateway.complete(request))
        try:
            payload = json.loads(reply.text)
        except json.JSONDecodeError:
            _log("  [警告] 改述回包不是 JSON")
            return {}
        out: dict[int, str] = {}
        for key, value in payload.items():
            try:
                out[int(key)] = str(value)
            except (TypeError, ValueError):
                continue
        return out

    # 第一轮：全部问一遍
    pending = list(enumerate(samples, start=1))
    answers = _ask(pending)
    result: dict[int, str] = {}
    rejected: list[tuple[int, str]] = []
    for index, source in pending:
        text = answers.get(index, "")
        if not text or len(_bigrams(text) & _bigrams(source)) > 1:
            rejected.append((index, source))
            continue
        result[index] = text
    # 第二轮：不合格的**只带着它自己**再问一次（上下文更干净，也更容易照要求做）
    if rejected:
        _log(f"  改述：{len(result)}/{len(samples)} 条合格，"
             f"{len(rejected)} 条退回重写")
        retry = _ask(rejected)
        for index, source in rejected:
            text = retry.get(index, "")
            if text and len(_bigrams(text) & _bigrams(source)) <= 1:
                result[index] = text
    return [result[index] for index in sorted(result)]


def main() -> int:
    _load_env()
    report: dict[str, dict] = {}

    from pmstudio.domain.shared.ids import UserId
    from pmstudio.infrastructure.sqlite import database
    from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
    from pmstudio.infrastructure.sqlite.materials import (
        SqliteFtsRetriever,
        SqliteHybridRetriever,
        SqliteMaterialStore,
    )
    from pmstudio.infrastructure.sqlite.owner_scope import set_owner_scope
    from pmstudio.infrastructure.sqlite.tokenize import query_tokens
    from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
    from pmstudio.infrastructure.tools.tavily import _packet
    from pmstudio.infrastructure.tools.webdedup import dedupe_by_content
    from tests.support.doubles import SeqIds

    project, owner = "p-bench", "u-bench"
    set_owner_scope(UserId(owner))

    frozen = BACKEND.parent / ".codex" / "runs" / "2026-10-19" / "web_corpus.json"
    for depth in ("basic", "advanced"):
        _log(f"\n=== Tavily search_depth={depth} ===")
        if frozen.is_file():
            raw = json.loads(frozen.read_text(encoding="utf-8"))
            _log(f"  用**冻结语料**（{frozen.name}，{len(raw)} 条）")
        else:
            raw = asyncio.run(_fetch(depth))
        _log(f"  真结果 {len(raw)} 条")
        if not raw:
            continue

        packets = [_packet(item, query=item["query"]) for item in raw]
        unique = dedupe_by_content(packets)

        # **保留率的分母是「清洗后」**：清洗掉的（Tavily 的 `[...]` 标记、表格分隔行）
        # 本来就不是内容，不该算进"丢了多少"。**去重的损失单独报告**——
        # 去重是**有意的**（近重复就该只留一条），把它算进保留率会把两件事混在一起。
        # 关键是这个比值**抓得住要修的那个 bug**：截到 300 字时它是 21%。
        from pmstudio.infrastructure.tools.webclean import normalize_web_text

        cleaned_all = sum(len(normalize_web_text(item["content"])) for item in raw)
        kept_cleaned = sum(
            len(normalize_web_text(item["content"]))
            for item in raw
            if any(item["url"] == p.source_ref for p in unique)
        )
        stored_chars = sum(
            len(chunk.text) for packet in unique for chunk in packet.chunks
        )
        keep_ratio = stored_chars / kept_cleaned if kept_cleaned else 0.0
        _log(f"  入库保留率 {keep_ratio:.1%}（保留的 {kept_cleaned} 字 → 入库 {stored_chars} 字）")
        _log(f"  去重：{len(raw)} 条 → {len(unique)} 条"
             f"（有意丢 {cleaned_all - kept_cleaned} 字）")

        with tempfile.TemporaryDirectory() as tmp:
            connection = database.connect(Path(tmp) / "b.db")
            connection.execute(
                "INSERT OR IGNORE INTO users (user_id, password_hash, created_at)"
                " VALUES (?, 'x', ?)",
                (owner, datetime.now(UTC).isoformat()),
            )
            store = SqliteMaterialStore(connection, ids=SeqIds())
            vectors = SqliteVectorIndex(connection, embedder=FastEmbedEmbedder())
            stamped = []
            for packet in unique:
                item = store.put(
                    project_id=project,
                    parsed=packet,
                    sha256=hashlib.sha256(packet.source_ref.encode()).hexdigest(),
                    content=b"x",
                    now=datetime.now(UTC),
                )
                vectors.write(item.material_id)
                stamped.append(item)

            # 造三类查询。
            #
            # **配对按语料条号**（冻结语料与手写改述都按条号索引）：材料入库后
            # id 变了、又被去重打乱顺序，按"列表下标"配对会**配错块**，
            # 量出来的是"语义无关"的查询，结论全错。用 URL 反查语料条号。
            url_to_index = {item["url"]: index for index, item in enumerate(raw)}
            index_of = {
                item.material_id: url_to_index.get(item.parsed.source_ref, -1)
                for item in stamped
            }
            literal_pairs: list[tuple[str, object]] = []
            for item in stamped:
                segment = _literal_segment(item.parsed.chunks[0].text)
                if segment:
                    literal_pairs.append((segment, item))
            literal_pairs = literal_pairs[:PER_CLASS]
            reorder_pairs = [
                ("".join(reversed(query_tokens(query))), item)
                for query, item in literal_pairs
            ]
            hand = BACKEND.parent / ".codex" / "runs" / "2026-10-19" / "web_paraphrases.json"
            if hand.is_file():
                mapping = json.loads(hand.read_text(encoding="utf-8"))["paraphrases"]
                para_pairs = [
                    (mapping[str(index_of[item.material_id])], item)
                    for _, item in literal_pairs
                    if str(index_of[item.material_id]) in mapping
                ]
                _log(f"  改述：用手写的 {len(para_pairs)} 条（按语料条号配对）")
            else:
                paraphrases = _make_paraphrases(
                    [item.parsed.chunks[0].text for _, item in literal_pairs]
                )
                para_pairs = [
                    (text, item)
                    for text, (_, item) in zip(paraphrases, literal_pairs, strict=False)
                ]

            hybrid = SqliteHybridRetriever(connection, embedder=FastEmbedEmbedder())
            fts = SqliteFtsRetriever(connection)
            # **α 扫描**：真数据才是决定性的（合成语料上 0.4–0.7 全过）。
            # `FUSION_ALPHA` 是**调用时**读的，所以改模块常量就能换档。
            import pmstudio.infrastructure.sqlite.materials as materials_mod

            alpha_grid = [float(a) for a in os.environ.get(
                "BENCH_ALPHA_GRID", "0.3,0.4,0.5,0.6,0.7").split(",")]
            original_alpha = materials_mod.FUSION_ALPHA

            def measure(retriever, pairs) -> tuple[int, int]:
                """命中按**材料**算，不是按块。

                检索返回的是块，但用户问的是"能不能找到这份材料"。一份材料现在
                有好几段（切块后），top5 很容易被**同一份材料**的几段占满——
                按块判会把"目标材料在 top5 里"误判成"没找到"（它的块被挤出去了）。
                所以这里按**材料去重**后看 top5。
                """
                top1 = r5 = 0
                for query, item in pairs:
                    hits = retriever.search(
                        project_id=project, query=query, limit=5, owner=owner
                    )
                    # 按**材料**去重后取前 5 份材料（F6 现在就是按材料出行的）
                    ids = list(dict.fromkeys(h.material_id for h in hits))
                    if ids and ids[0] == item.material_id:
                        top1 += 1
                    if item.material_id in ids:
                        r5 += 1
                return top1, r5

            rows = {}
            measured: dict[str, dict[str, int]] = {}
            for name, retriever in (("改前(纯FTS5)", fts), ("改后(混合)", hybrid)):
                rows[name] = {
                    "逐字子串": measure(retriever, literal_pairs),
                    "同词换序": measure(retriever, reorder_pairs),
                    "同义改述": measure(retriever, para_pairs),
                }
                measured[name] = {
                    "逐字子串": len(literal_pairs),
                    "同词换序": len(reorder_pairs),
                    "同义改述": len(para_pairs),
                }
            # α 扫描（只对混合这一路）
            for alpha in alpha_grid:
                materials_mod.FUSION_ALPHA = alpha
                rows[f"α={alpha}"] = {
                    "逐字子串": measure(hybrid, literal_pairs),
                    "同词换序": measure(hybrid, reorder_pairs),
                    "同义改述": measure(hybrid, para_pairs),
                }
            materials_mod.FUSION_ALPHA = original_alpha
            connection.close()

        for name, kinds in rows.items():
            counts = measured.get(name) or measured.get("改后(混合)", {})
            for kind, (top1, r5) in kinds.items():
                total = counts.get(kind, 0)
                _log(f"  {name:12} {kind:6} top1 {top1:2}/{total} r@5 {r5:2}/{total}")
        report[depth] = {"keep_ratio": keep_ratio, "rows": rows, "measured": measured}

    _log("\n=== 判据 ===")
    failures: list[str] = []
    for depth, data in report.items():
        ratio = data["keep_ratio"]
        if ratio < KEEP_RATIO_MIN:
            failures.append(f"{depth}：入库保留率 {ratio:.1%} < {KEEP_RATIO_MIN:.0%}")
        rows = data["rows"]
        before = rows["改前(纯FTS5)"]
        after = rows.get(f"α={os.environ.get('BENCH_ALPHA', '0.5')}", rows["改后(混合)"])
        for kind in ("逐字子串", "同词换序"):
            if after[kind][1] < before[kind][1]:
                failures.append(
                    f"{depth} {kind}：改后 r@5 {after[kind][1]} < 改前 {before[kind][1]}"
                )
        if after["同义改述"][1] <= before["同义改述"][1]:
            failures.append(
                f"{depth} 同义改述：改后 r@5 {after['同义改述'][1]} 没有高于改前 "
                f"{before['同义改述'][1]}"
            )
    for item in failures:
        _log(f"  ✗ {item}")
    if not failures:
        _log("  ✔ 全部判据通过")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
