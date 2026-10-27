"""真跑数据量「一次整合的回包 token」与「单条提案的 token」。

## 为什么量（批 14，M5）

`max_tokens` **代码里根本没设** → 撞的是**供应商默认**输出上限；
而 60 条是 **schema 硬上限**（`application/agents/schemas.py:491`）→
**17% 的整合调用写了 >60 条** → 事后真截断 → **最多一次丢 16 条提案，用户拿不到**
（只在留痕里）——`02` §11.0.42。

## 量法（不估、不拍）

应用自己的库 `backend/data/pmstudio.db` 的 `model_calls` 表：
- `reply_text` = 模型的回包原文 → 逐条切出提案的 JSON 长度 ✓
- `usage_json` = **供应商报的实际 token 数** → 换算系数 = token ÷ 字符 ✓

→ **单条提案的 P95 token**（= P95 字符 × 换算系数）、
**一次整合的回包 P99 token**（= `max_tokens` 的下限依据）、
**条数上限 N** = min(60, max_tokens ÷ P95 单条 token) ✓

## 用法

    cd backend && uv run python -u scripts/measure_integration_output.py
"""

import json
import sqlite3
import statistics
from pathlib import Path

DB = Path("data/pmstudio.db")


def percentile(values: list[float], q: float) -> float:
    """P{q}（线性插值）——样本少的时候比"取第几个"稳。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def main() -> None:
    if not DB.exists():
        print(f"没有 {DB} —— 先真跑一轮（应用自己的库）")
        return
    connection = sqlite3.connect(DB)
    rows = connection.execute(
        "SELECT purpose, reply_text, usage_json FROM model_calls"
        " WHERE reply_text IS NOT NULL AND reply_text != ''"
    ).fetchall()
    connection.close()

    reply_tokens: list[float] = []
    per_proposal_chars: list[float] = []
    per_call_tokens: list[float] = []
    counts: list[int] = []
    used = 0
    for _purpose, reply_text, usage_json in rows:
        if '"proposals"' not in reply_text:
            continue
        try:
            packet = json.loads(reply_text)
        except json.JSONDecodeError:
            continue
        proposals = packet.get("proposals")
        if not isinstance(proposals, list) or not proposals:
            continue
        try:
            usage = json.loads(usage_json or "{}")
        except json.JSONDecodeError:
            usage = {}
        completion = usage.get("completion_tokens")
        if not isinstance(completion, int) or completion <= 0:
            continue
        used += 1
        reply_tokens.append(float(completion))
        counts.append(len(proposals))
        raw = json.dumps(proposals, ensure_ascii=False)
        per_call_tokens.append(completion / max(1, len(raw)))
        for proposal in proposals:
            per_proposal_chars.append(len(json.dumps(proposal, ensure_ascii=False)))

    print(f"有提案的回包 {used} 次（库里共 {len(rows)} 次调用）\n")
    if not used:
        print("没有可量的样本 —— 真跑一轮再来")
        return
    print(f"回包 token（供应商报的实际值）：P50 {percentile(reply_tokens, 0.5):.0f}"
          f" · P95 {percentile(reply_tokens, 0.95):.0f}"
          f" · P99 {percentile(reply_tokens, 0.99):.0f}"
          f" · max {max(reply_tokens):.0f}")
    print(f"单条提案字符：P50 {percentile(per_proposal_chars, 0.5):.0f}"
          f" · P95 {percentile(per_proposal_chars, 0.95):.0f}"
          f" · P99 {percentile(per_proposal_chars, 0.99):.0f}"
          f" · max {max(per_proposal_chars):.0f}")
    ratio = statistics.median(per_call_tokens) if per_call_tokens else 1.0
    print(f"token/字符 换算系数（中位）：{ratio:.3f}")
    p95_single = percentile(per_proposal_chars, 0.95) * ratio
    p99_reply = percentile(reply_tokens, 0.99)
    print(f"\n→ 单条提案 P95 ≈ {p95_single:.0f} token")
    print(f"→ max_tokens 建议 = P99 × 1.2 = {p99_reply * 1.2:.0f}"
          f"（P99 回包 {p99_reply:.0f}）")
    print(f"→ 条数上限 N = min(60, max_tokens ÷ P95单条) = "
          f"{min(60, int((p99_reply * 1.2) // max(1.0, p95_single)))}")
    print(f"   （本次样本的条数：中位 {statistics.median(counts):.0f}"
          f" · 最大 {max(counts)}）")


if __name__ == "__main__":
    main()
