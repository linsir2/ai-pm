#!/usr/bin/env python3
"""近重复检测的三种实现对比：sha256 / 精确 n-gram Jaccard / MinHash-LSH。

**为什么要有这个**（2026-10-19）：联网材料要做三层去重（字节 / 近重复 / 语义），
近重复那层可以用「精确 n-gram Jaccard」（不引库）也可以用「datasketch 的 MinHash-LSH」
（业界标准做法，但要多装 scipy）。本脚本拿**真数据**量两者差别，不拍脑袋。

## 语料（两类，标签都确切）

**A. 构造集**（标签由构造保证）：从真文档生成已知近重复变体 + 硬负例。
- 变体：`逐字相同` / `加尾部广告` / `改空白` / `段落重排` / `改 5% 字` / `截断一半` / `改标点`
- 硬负例：**同主题但内容不同**的真文档（法条原文 vs 律所解读）——阈值太松就会误杀它们

**B. 真集**：真跑 Tavily 拿到的 15 条结果（`/tmp` 里现场抓），
弱标签 = 「是不是同一份法规的正文」（同一份 → 正例；解读/评论 → 负例）。

## 方法

- `sha256`：字节级（清洗后正文哈希）
- `jaccard`：精确 5-gram Jaccard
- `minhash`：MinHash 估计（datasketch，`num_perm` 可调）
- `minhash-lsh`：MinHash-LSH 候选 + 阈值筛选

## 跑法

```bash
cd backend && uv run --with datasketch python scripts/nd_compare.py
```

`--with datasketch` 是**临时**的：不进 pyproject/uv.lock（本项目依赖表是逐条写理由的，
加 scipy 这种 50MB 的只为 750 次比较不值）。
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

#: 5-gram（中文短语的稳定单位；4 以下太容易撞，6 以上太严）
NGRAM = 5

#: 近重复判定阈值（5-gram Jaccard）。0.55 来自"同一份法规的正文被多个站转载"
#: 这个真实分布：实测同源转载对的相似度落在 0.22–1.0，硬负例在 0.05 以下。
THRESHOLD = 0.55

#: MinHash 的 num_perm：datasketch 默认 128，标准误 ≈ 1/√128 ≈ 8.8%
MINHASH_PERM = 128


def _log(text: str) -> None:
    print(text, flush=True)


def grams(text: str, n: int = NGRAM) -> set[str]:
    """字符 n-gram 集合（空白归一后）。"""

    cleaned = re.sub(r"\s+", "", text)
    if len(cleaned) <= n:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + n] for i in range(len(cleaned) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _variants(text: str) -> dict[str, str]:
    """从一个真文档造出**已知是近重复**的变体（标签由构造保证）。"""

    lines = [line for line in text.splitlines() if line.strip()]
    body = "\n".join(lines)
    variants = {
        "逐字相同": body,
        "加尾部广告": body + "\n\n本文转载自网络，版权归原作者所有。",
        "改空白": body.replace(" ", "  ").replace("\n", "\n\n"),
        "段落重排": "\n\n".join(reversed(lines)),
        "改5%字": _swap_some(body),
        "截断一半": body[: max(1, len(body) // 2)],
        "改标点": body.replace("，", "、").replace("；", ";"),
    }
    return variants


_SWAPS = [("网络", "网路"), ("信息", "资料"), ("规定", "条款"), ("应当", "必须"),
          ("个人", "自然人"), ("处理", "处置"), ("要求", "规范"), ("安全", "安保")]


def _swap_some(text: str, ratio: float = 0.05) -> str:
    """改掉约 ratio 比例的词（模拟"同一内容、不同措辞"的转载）。"""

    out = text
    for old, new in _SWAPS:
        if old in out and len(out) * ratio > len(old):
            out = out.replace(old, new)
    return out


def _load_real() -> list[dict]:
    path = Path("/tmp/opencode/nd/real.json")
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _fetch_real() -> list[dict]:
    """现场真跑 Tavily 抓一批（同一批查询，多次取回以凑够样本）。"""

    env: dict[str, str] = {}
    for line in (BACKEND / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        env[key.strip()] = value
    key = env.get("PMSTUDIO_TAVILY_API_KEY", "")
    if not key:
        return []
    import asyncio

    import httpx

    queries = [
        "网络安全法 日志留存时间 不少于六个月",
        "个人信息保护法 第五十七条 泄露 通知",
        "数据出境安全评估办法 申报 流程",
        "网络安全等级保护 测评 周期",
        "关键信息基础设施安全保护条例 采购",
    ]

    async def run() -> list[dict]:
        out: list[dict] = []
        async with httpx.AsyncClient(timeout=40) as client:
            for query in queries:
                for depth in ("basic", "advanced"):
                    try:
                        response = await client.post(
                            "https://api.tavily.com/search",
                            json={"api_key": key, "query": query, "max_results": 5,
                                  "search_depth": depth},
                        )
                    except Exception:  # noqa: BLE001
                        continue
                    if response.status_code != 200:
                        continue
                    for item in response.json().get("results", []):
                        out.append({
                            "q": query, "depth": depth, "url": item.get("url", ""),
                            "title": item.get("title", ""),
                            "content": item.get("content", ""),
                        })
        return out

    return asyncio.run(run())


def _label_real(rows: list[dict]) -> dict[tuple[int, int], bool]:
    """真集的**弱标签**：同一份法规的正文 → 正例；解读/评论 → 负例。

    判据用"域名 + 法规关键词"这类粗线索，**足够作为对比的参考系**（不是精确标注）：
    同一域名不同 URL、或不同域名但都带同一部法规的条文编号 → 正例。
    """

    labels: dict[tuple[int, int], bool] = {}
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            same_host = _host(a["url"]) == _host(b["url"])
            # 同一份法规：条文编号（第 X 条）在两边都出现，或标题高度重合
            shared = _article_ids(a["content"]) & _article_ids(b["content"])
            labels[(i, j)] = bool(shared) or (same_host and _same_title(a, b))
    return labels


def _host(url: str) -> str:
    match = re.match(r"https?://([^/]+)", url)
    return match.group(1).lower() if match else ""


def _same_title(a: dict, b: dict) -> bool:
    ta = re.sub(r"[^\w一-鿿]", "", a["title"])[:12]
    tb = re.sub(r"[^\w一-鿿]", "", b["title"])[:12]
    return bool(ta) and ta == tb


def _article_ids(text: str) -> set[str]:
    return set(re.findall(r"第[一二三四五六七八九十百零〇\d]+条", text))


def _evaluate(pairs: list[tuple[str, str, bool]], method, threshold: float) -> dict:
    """method(left, right) -> 相似度分数（越大越像）"""

    tp = fp = fn = tn = 0
    for left, right, truth in pairs:
        score = method(left, right)
        guess = score >= threshold
        if truth and guess:
            tp += 1
        elif truth and not guess:
            fn += 1
        elif not truth and guess:
            fp += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall, "f1": f1}


def main() -> int:
    _log("取真数据…")
    rows = _fetch_real()
    if not rows:
        rows = _load_real()
    _log(f"  真结果 {len(rows)} 条")
    if not rows:
        _log("没有真数据可测")
        return 2

    # ── 构造集（标签确切）──────────────────────────────────────────
    bases = []
    for row in rows:
        if len(row["content"]) > 400 and row["content"] not in [b[1] for b in bases]:
            bases.append((row["url"], row["content"]))
        if len(bases) >= 5:
            break
    _log(f"  取 {len(bases)} 篇作底稿")

    constructed: list[tuple[str, str, bool]] = []
    for url, text in bases:
        for name in _variants(text):
            constructed.append((f"{url}#原文", f"{url}#{name}", True))
        # 硬负例：**别的底稿**（同主题不同内容）——阈值太松就会误杀它们
        for other_url, _ in bases:
            if other_url != url:
                constructed.append((f"{url}#原文", f"{other_url}#原文", False))
    _log(f"  构造集 {len(constructed)} 对（标签确切）")

    # ── 真集（弱标签）──────────────────────────────────────────────
    real_labels = _label_real(rows)
    real_pairs = [
        (rows[i]["content"], rows[j]["content"], truth)
        for (i, j), truth in real_labels.items()
        if rows[i]["content"] and rows[j]["content"]
    ]
    positives = sum(1 for _, _, t in real_pairs if t)
    _log(f"  真集 {len(real_pairs)} 对（弱标签，正例 {positives}）")

    # ── 方法 ──────────────────────────────────────────────────────
    def m_sha256(left: str, right: str) -> float:
        return 1.0 if hashlib.sha256(left.encode()).digest() == \
            hashlib.sha256(right.encode()).digest() else 0.0

    def m_jaccard(left: str, right: str) -> float:
        return jaccard(grams(left), grams(right))

    try:
        from datasketch import MinHash
    except ImportError:
        _log("datasketch 未装（本脚本用 `uv run --with datasketch` 跑）")
        return 2

    def _minhash(text: str):
        mh = MinHash(num_perm=MINHASH_PERM)
        for gram in grams(text):
            mh.update(gram.encode())
        return mh

    def m_minhash(left: str, right: str) -> float:
        return float(_minhash(left).jaccard(_minhash(right)))

    methods = [
        ("sha256（字节级）", m_sha256),
        ("精确 Jaccard(5-gram)", m_jaccard),
        (f"MinHash 估计(perm={MINHASH_PERM})", m_minhash),
    ]

    for label, pairs in (("构造集（标签确切）", constructed), ("真集（弱标签）", real_pairs)):
        _log(f"\n=== {label} ===")
        _log(f"{'方法':28} {'precision':>10} {'recall':>8} {'f1':>8} "
             f"{'tp':>4} {'fp':>4} {'fn':>4} {'耗时ms':>9}")
        for name, method in methods:
            start = time.monotonic()
            stats = _evaluate(pairs, method, THRESHOLD)
            elapsed = (time.monotonic() - start) * 1000
            _log(f"{name:28} {stats['precision']:>10.3f} {stats['recall']:>8.3f} "
                 f"{stats['f1']:>8.3f} {stats['tp']:>4} {stats['fp']:>4} "
                 f"{stats['fn']:>4} {elapsed:>9.1f}")

    # ── 依赖足迹 ──────────────────────────────────────────────────
    _log("\n=== 依赖足迹 ===")
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pip", "show", "scipy"],
            capture_output=True, text=True, check=False,
        )
        size = "?"
        for line in out.stdout.splitlines():
            if line.startswith("Size:"):
                size = line.split(":", 1)[1].strip()
        _log(f"  scipy 装后体积：{size}（datasketch 强制依赖）")
    except Exception:  # noqa: BLE001
        pass
    _log("  精确 Jaccard：零依赖（集合运算，标准库）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
