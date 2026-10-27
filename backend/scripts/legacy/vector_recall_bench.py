#!/usr/bin/env python3
"""批 5 量化验收：三个检索器在同一语料上的 top1 / recall@5 对照表。

**判据（先注册，跑完不许改）**：

| 查询类 | 判据 |
|---|---|
| ①逐字子串 | v3(真模型) 的 top1 与 recall@5 **逐档 ≥ v2** |
| ②同词换序 | v3(真模型) 的 top1 与 recall@5 **逐档 ≥ v2** |
| ③同义改述 | v3(真模型) 的 recall@5 **≥ 60%**，且 v2 同类 **≤ 10%** |

**语料**：46 条**真** observations（真中文产品文本）+ 100 条**生成**的材料块。
真库 0 份材料，所以"真"只有那 46 条；生成的那 100 条是为了配"同义改述"——
真观察没有人工改述，硬造会失真。**这条限制必须跟着数字一起被引用**
（见 `docs/plans/2026-10-19-B组-批5-量化与端到端验收.md`）。

**三个检索器**：
- `SqliteFtsRetriever` —— v2 词法基线（批 5 之前的世界）；
- `SqliteHybridRetriever` + `FastEmbedEmbedder` —— v3 真模型（**验收对象**）；
- `SqliteHybridRetriever` + `FakeEmbedder` —— v3 假模型，**只为对照"向量路到底
  有没有起作用"**，不作为验收对象。

**跑法**：

```bash
cd backend && HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \\
  set -a && . ./.env && set +a && \\
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/vector_recall_bench.py
```

退出码非 0 = 判据没过（数字原样打 stderr，**不许调阈值**）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
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
    SqliteHybridRetriever,
    SqliteMaterialStore,
)
from pmstudio.infrastructure.sqlite.owner_scope import set_owner_scope  # noqa: E402
from pmstudio.infrastructure.sqlite.tokenize import query_tokens  # noqa: E402

OWNER = "bench-owner"
PROJECT = "p-bench"

#: 每类查询条数（批 5 计划预注册：各 40 条）。
PER_CLASS = 40

#: 生成多少条合成材料块。
SYNTH_COUNT = 100

#: 合成改述与原文**最多**允许共享几个 2-gram。
#:
#: 为什么不是 0：中文里"退货""时限"这类词天然会被改述借去一两个，零重合会把
#: 合格改述也筛掉（实测会筛掉一大半），样本就凑不满。**硬保证不在这里**——
#: 在下面那条注册判据「v2 在同一批改述上的 recall@5 ≤ 10%」：那是直接量
#: "词法到底漏不漏"，比 2-gram 代理准。筛不干净的语料会让 v2 基线抬高，
#: 那时判据会自己红掉。
MAX_SHARED_BIGRAMS = 1

#: **判据阈值**（注册在这里，跑完不许改）。
RECALL_SYNTH_MIN = 0.60      # ③同义改述：v3 真模型 recall@5 ≥ 60%
RECALL_SYNTH_V2_MAX = 0.10    # ③同义改述：v2 基线必须 ≤ 10%（≈0，否则说明造词失真）

SYNTH_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "paraphrase": {"type": "string"},
                },
                "required": ["title", "text", "paraphrase"],
            },
        }
    },
    "required": ["items"],
}

SYNTH_PROMPT = """\
你是产品文档的作者。下面要写 {n} 段**互不相关**的中文业务材料，每段来自一个不同的
业务场景。场景限定在这一组里，一个场景一段：{scenes}

每段给三个字段：
- `title`：8 到 14 个汉字的短标题。
- `text`：40 到 120 个汉字的正文，写清楚一件事的规矩、步骤或条件。
- `paraphrase`：**同一个意思，但用完全不同的说法问出来**（15 到 30 个汉字）。
  关键要求：它**不得与 title 或 text 有超过一个连续汉字相同**——它是"用户用另一种
  说法问这件事"，不是把原文换个说法。举例说明差别：
  - 原文「机柜内网线走背板槽位」→「机器内部的线要贴着后面板走」✔（用词几乎全换）
  - 原文「机柜内网线走背板槽位」→「机柜内网线走背板槽位」✘（照抄，不算改述）

只输出 JSON。"""

#: 分批生成的场景池（每批取一段，保证批次之间不重复）。
SCENES = [
    "客服话术 / 退换货规则",
    "仓储盘点 / 库位分配",
    "财务对账 / 发票开具",
    "排班规则 / 考勤口径",
    "权限审批 / 角色矩阵",
    "物流签收 / 异常件处理",
    "质检标准 / 抽检比例",
    "会员等级 / 权益差异",
    "设备保养 / 巡检周期",
    "供应商准入 / 资质审核",
    "价格策略 / 折扣叠加",
    "客诉升级 / 时效承诺",
    "数据导出 / 脱敏口径",
    "工单流转 / 派单规则",
    "退款风控 / 拦截条件",
    "库存预警 / 补货触发",
    "合同归档 / 签署流程",
    "培训认证 / 上岗要求",
    "预约排期 / 冲突处理",
    "积分兑换 / 有效期",
]


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


def _real_observations() -> list[tuple[str, str]]:
    """真库只读：46 条真 observations（title + detail）。"""
    db = os.environ.get("PMSTUDIO_DB") or str(BACKEND / "data" / "pmstudio.db")
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    try:
        rows = src.execute("SELECT title, detail FROM observations").fetchall()
    finally:
        src.close()
    return [(str(r["title"]), str(r["detail"])) for r in rows]


def _generate_synth(count: int) -> list[dict[str, str]]:
    """真模型生成合成语料（**分批**，一批一场景）。

    一次要 100 条会把输出撑到 150 秒超时（实测 138 秒出 94 条、再一次就超时）。
    分批（每批 10 条、场景不重）单次输出短、稳；批数与实际条数都记进证据。
    失败就抛——不许静默换假数据。

    **语料落缓存**（`.codex/runs/2026-10-19/bench_corpus.json`，gitignore）：
    重新生成一次要 9 分钟模型调用，而**两次跑必须是同一份语料**才可比
    （改一行代码就重跑的话，语料一变，前后数字就没法对照了）。
    """
    from pmstudio.harness.model.contracts import ModelMessage, ModelRequest
    from pmstudio.harness.model.providers.litellm import LiteLLMGateway

    cache = BACKEND.parent / ".codex" / "runs" / "2026-10-19" / "bench_corpus.json"
    if cache.is_file():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if len(cached) >= PER_CLASS:
            _log(f"  用缓存语料（{cache}，{len(cached)} 条）")
            return cached[:count]

    gateway = LiteLLMGateway(
        model=os.environ["PMSTUDIO_MODEL"],
        api_key=os.environ["PMSTUDIO_API_KEY"],
        api_base=os.environ.get("PMSTUDIO_API_BASE") or None,
        # 这次输出大，用 .env 里的 150 秒会超时（实测 138 秒就到顶）——这里单独放宽。
        timeout=300.0,
        max_attempts=2,
    )
    items: list[dict[str, str]] = []
    batches = max(1, -(-count // 10))
    for batch in range(batches):
        scenes = "、".join(SCENES[batch * 10 : (batch + 1) * 10])
        request = ModelRequest(
            purpose="bench_synth",
            messages=(
                ModelMessage(
                    role="user",
                    content=SYNTH_PROMPT.format(n=10, scenes=scenes),
                ),
            ),
            schema_name="SynthCorpus",
            response_schema=SYNTH_SCHEMA,
        )
        reply = asyncio.run(gateway.complete(request))
        try:
            payload = json.loads(reply.text)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"第 {batch + 1} 批模型回包不是 JSON：{reply.text[:300]}") from error
        got = payload.get("items") or []
        if len(got) < 5:
            raise RuntimeError(f"第 {batch + 1} 批只给了 {len(got)} 条，太少")
        for item in got:
            items.append(
                {
                    "title": str(item["title"]),
                    "text": str(item["text"]),
                    "paraphrase": str(item["paraphrase"]),
                }
            )
        _log(f"  第 {batch + 1}/{batches} 批：+{len(got)} 条（累计 {len(items)}）")
    if len(items) < PER_CLASS:
        raise RuntimeError(f"合计只生成了 {len(items)} 条，至少要 {PER_CLASS} 条")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _log(f"  语料已存 {cache}")
    return items[:count]


def _bigrams(text: str) -> set[str]:
    kept = "".join(ch for ch in text if ch.isalnum())
    return {kept[i : i + 2] for i in range(len(kept) - 1)}


def _literal_segment(text: str) -> str:
    """从正文切一段 8–14 字的连续中文（与 `retrieval_acceptance.py` 同一把尺子）。"""
    for part in re.split(r"[。，、；：（）【】?？!！\s]+", text):
        part = re.sub(r"[A-Za-z0-9_./·—\-]+", "", part)
        if 8 <= len(part) <= 14:
            return part
    return ""


def _seed(connection, store, blocks: list[tuple[str, str]]) -> dict[str, str]:
    """把块灌进库（**经真材料仓**，索引由生产代码建），返回 {块文本指纹: material_id}。"""
    from pmstudio.application.dto import MaterialChunk, ParsedMaterial

    mapping: dict[str, str] = {}
    for index, (title, text) in enumerate(blocks):
        parsed = ParsedMaterial(
            origin="upload",
            title=title,
            source_ref=f"attachment:bench-{index}",
            media_type="text/markdown",
            bytes_len=1,
            chunks=(
                MaterialChunk(
                    chunk_id="local#c00",
                    ordinal=0,
                    text=text,
                    heading_path=(),
                    anchor="第 1 段",
                ),
            ),
            parser_id="local-text",
            parser_version="v1",
        )
        packet = store.put(
            project_id=PROJECT,
            parsed=parsed,
            # 去重键必须真唯一。**别用 `f"x-{i}".ljust(64, "0")`**：填充字符跟
            # 序号里的数字同字符时会撞车——"b-1"+61个0 与 "b-10"+60个0 是**同一个
            # 字符串**（实测 101 条里撞掉 10 条，去重把材料并成 91 份，
            # 向量表随之少 10 行、KNN 静默少召回）。
            sha256=hashlib.sha256(f"bench-{index}".encode()).hexdigest(),
            content=b"x",
            now=datetime.now(UTC),
        )
        mapping[f"{index:04d}"] = packet.material_id
    return mapping


def _hit(retriever, query: str, material_id: str, limit: int = 5) -> tuple[bool, bool]:
    """返回 (top1 命中, recall@5 命中)。"""
    hits = retriever.search(
        project_id=PROJECT, query=query, limit=limit, owner=OWNER
    )
    ids = [h.material_id for h in hits]
    return (bool(ids) and ids[0] == material_id, material_id in ids)


def main() -> int:
    _load_env()
    set_owner_scope(_owner())

    _log("[0] 取语料…")
    real = _real_observations()
    _log(f"  真 observations {len(real)} 条")
    _log(f"[1] 真模型生成 {SYNTH_COUNT} 条合成块…")
    synth = _generate_synth(SYNTH_COUNT)
    _log(f"  合成 {len(synth)} 条")

    blocks: list[tuple[str, str]] = [(title, detail) for title, detail in real]
    real_count = len(blocks)
    blocks += [(item["title"], item["text"]) for item in synth]

    with tempfile.TemporaryDirectory() as tmp:
        connection = database.connect(Path(tmp) / "bench.db")
        store = SqliteMaterialStore(connection, ids=_SeqIds())
        mapping = _seed(connection, store, blocks)
        _log(f"  灌库完成：{len(blocks)} 份材料（索引由真材料仓建）")

        # **写向量**：材料仓只建 FTS 索引，向量是用例在**提交后**另写
        # （批 5 Task 5 的接线）。这里直接调生产组件 `SqliteVectorIndex.write()`
        # ——不写的话 vec 表是空的，KNN 分支全程无参与，三个检索器会给出
        # **完全相同**的数字（第一版就是这么白跑了一轮）。
        from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
        from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex

        embedder = FastEmbedEmbedder()
        vector_index = SqliteVectorIndex(connection, embedder=embedder)
        for material_id in mapping.values():
            vector_index.write(material_id)
        written = connection.execute("SELECT count(*) FROM material_chunks_vec").fetchone()[0]
        _log(f"  向量已写：{written} 条（KNN 分支有东西可查了）")
        if written != len(blocks):
            raise RuntimeError(
                f"向量写了 {written} 条，材料 {len(blocks)} 份——对不上，KNN 不可信"
            )

        # ── 造三类查询 ────────────────────────────────────────────────
        cases: dict[str, list[tuple[str, str]]] = {
            "逐字子串": [],
            "同词换序": [],
            "同义改述": [],
        }
        dropped = 0
        for index in range(min(PER_CLASS, real_count)):
            title, text = real[index]
            segment = _literal_segment(text)
            if not segment:
                continue
            cases["逐字子串"].append((segment, f"{index:04d}"))
            cases["同词换序"].append(("".join(reversed(query_tokens(segment))), f"{index:04d}"))
        # 改述候选**按词面重合从少到多排序**再取前 PER_CLASS 条：
        # 重合越少越是"真改述"。先排序再截断，比"边走边取"更干净——
        # 后者取到的是"生成顺序里靠前的那几条"，跟质量无关。
        candidates = []
        for index, item in enumerate(synth):
            # **对照的是"被索引的内容"**：`index_source` = 标题 + 祖先标题 + 正文，
            # 所以重合要对着 title + text 查——只查正文会漏掉"改述借了标题里的词"
            # 这种（第一版就是这么让 v2 基线虚高到 30% 的）。
            indexed = f"{item['title']}{item['text']}"
            shared = len(_bigrams(item["paraphrase"]) & _bigrams(indexed))
            candidates.append((shared, index, item["paraphrase"]))
        candidates.sort(key=lambda row: (row[0], row[1]))
        for shared, index, paraphrase in candidates:
            if shared > MAX_SHARED_BIGRAMS:
                dropped += 1        # 词面重合太多 → 它不是"同义改述"，混进来会抬高基线
                continue
            if len(cases["同义改述"]) >= PER_CLASS:
                continue
            cases["同义改述"].append((paraphrase, f"{real_count + index:04d}"))
        _log(
            f"[2] 查询：逐字 {len(cases['逐字子串'])} 条、换序 {len(cases['同词换序'])} 条、"
            f"同义改述 {len(cases['同义改述'])} 条（弃用 {dropped} 条：与原文有 2-gram 重合）"
        )
        if dropped:
            _log(
                f"  ⚠ 弃用 {dropped} 条（与标题/正文的 2-gram 重合 > "
                f"{MAX_SHARED_BIGRAMS}）——同义改述类实得 {len(cases['同义改述'])} 条"
            )
        if len(cases["同义改述"]) < PER_CLASS:
            _log(
                f"  ⚠ 同义改述类不足 {PER_CLASS} 条（实得 {len(cases['同义改述'])}），"
                "样本偏小，这一条的数字要打折看"
            )

        # ── 三个检索器 ────────────────────────────────────────────────
        from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
        from tests.support.embedding import FakeEmbedder

        retrievers = {
            "v2 词法": SqliteFtsRetriever(connection),
            "v3 混合·真模型": SqliteHybridRetriever(
                connection, embedder=FastEmbedEmbedder()
            ),
            "v3 混合·假模型": SqliteHybridRetriever(
                connection, embedder=FakeEmbedder(dimensions=512)
            ),
        }

        stats: dict[str, dict[str, dict[str, int]]] = {
            name: {kind: {"top1": 0, "r5": 0, "n": 0} for kind in cases}
            for name in retrievers
        }
        for kind, items in cases.items():
            for name, retriever in retrievers.items():
                for query, key in items:
                    # key 是块在 `blocks` 里的序号；ground truth 是它**入库后**的 material_id
                    top1, r5 = _hit(retriever, query, mapping[key])
                    stats[name][kind]["top1"] += int(top1)
                    stats[name][kind]["r5"] += int(r5)
                    stats[name][kind]["n"] += 1
            _log(f"  [{kind}] 量完")

        # ── 出表 ──────────────────────────────────────────────────────
        lines = [
            "| 查询类型 | n | v2 top1 | v2 r@5 | v3真 top1 | v3真 r@5 | v3假 top1 | v3假 r@5 |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for kind in cases:
            s2 = stats["v2 词法"][kind]
            s3 = stats["v3 混合·真模型"][kind]
            sf = stats["v3 混合·假模型"][kind]
            lines.append(
                f"| {kind} | {s2['n']} | {s2['top1']} | {s2['r5']} | "
                f"{s3['top1']} | {s3['r5']} | {sf['top1']} | {sf['r5']} |"
            )
        totals = {
            name: {
                "top1": sum(stats[name][k]["top1"] for k in cases),
                "r5": sum(stats[name][k]["r5"] for k in cases),
                "n": sum(stats[name][k]["n"] for k in cases),
            }
            for name in retrievers
        }
        t2, t3, tf = totals["v2 词法"], totals["v3 混合·真模型"], totals["v3 混合·假模型"]
        lines.append(
            f"| **合计** | {t2['n']} | {t2['top1']} | {t2['r5']} | "
            f"{t3['top1']} | {t3['r5']} | {tf['top1']} | {tf['r5']} |"
        )
        table = "\n".join(lines)
        _log("\n" + table)

        # ── 判据 ──────────────────────────────────────────────────────
        failures: list[str] = []
        for kind in ("逐字子串", "同词换序"):
            s2, s3 = stats["v2 词法"][kind], stats["v3 混合·真模型"][kind]
            if s3["top1"] < s2["top1"]:
                failures.append(
                    f"①/②零倒退失败：{kind} 的 v3 top1 {s3['top1']} < v2 {s2['top1']}"
                )
            if s3["r5"] < s2["r5"]:
                failures.append(
                    f"①/②零倒退失败：{kind} 的 v3 recall@5 {s3['r5']} < v2 {s2['r5']}"
                )
        syn2 = stats["v2 词法"]["同义改述"]
        syn3 = stats["v3 混合·真模型"]["同义改述"]
        if syn3["n"] == 0:
            failures.append("同义改述类一条查询都没有——造词全被弃用了")
        else:
            rate3 = syn3["r5"] / syn3["n"]
            rate2 = syn2["r5"] / syn2["n"]
            if rate3 < RECALL_SYNTH_MIN:
                failures.append(
                    f"③同义改述 recall@5 {rate3:.0%} < 预注册的 {RECALL_SYNTH_MIN:.0%}"
                )
            if rate2 > RECALL_SYNTH_V2_MAX:
                failures.append(
                    f"③同义改述的 v2 基线 {rate2:.0%} > {RECALL_SYNTH_V2_MAX:.0%}"
                    "——说明造词失真（改述跟原文有词面重合），这条数不能用"
                )

        _log("")
        if failures:
            _log("判据未过：")
            for item in failures:
                _log(f"  ✗ {item}")
            return 1
        _log("判据全过 ✔")
        return 0


def _owner():
    from pmstudio.domain.shared.ids import UserId

    return UserId(OWNER)


if __name__ == "__main__":
    raise SystemExit(main())
