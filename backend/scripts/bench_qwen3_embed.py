"""换嵌入模型的**实测**：Qwen3-Embedding-0.6B 在本机 CPU 上到底多慢（批 7，2026-10-21）。

## 为什么要量

换模型能**彻底解决 B1**（32K 上下文 → 不用管 512 的窗口）且 MTEB 中文检索分
61.77 → 71.03。但它是 0.6B 参数 = `bge-small-zh` 的 **18 倍** → 推算单块
49ms → ~0.9s → 联网路每轮 120 块 → **~108 秒/轮**（不可接受）。

**推算不算数**，所以实测：判据是**单块 ≤ 150ms**（每轮 120 块 ≤ 18s）才继续谈；
否则正式归档，理由带实测数字写进 `04-未决.md`。

跑法（首次会下 ~1.1GB 的 int8 ONNX）：
    cd backend && HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \\
        uv run --with "fastembed>=0.9" python -u scripts/bench_qwen3_embed.py
"""

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

MODEL = "Qwen/Qwen3-Embedding-0.6B-Q"   # int8 版（1.12GB）；fastembed 里的名字带 org 前缀
OUT = Path("../.codex/runs/2026-10-21")
BUDGET_MS = 150.0
N_BLOCKS = 20


def blocks() -> list[str]:
    """用**真实**的联网材料块当输入（不是 lorem ipsum）。"""
    from pmstudio.infrastructure.tools.extract import chunk_text
    from pmstudio.infrastructure.tools.webclean import normalize_web_text

    corpus = Path("../.codex/runs/2026-10-19/tavily_raw/C raw_content-净.json")
    if not corpus.exists():
        raise SystemExit("冻结语料不在，先跑 scripts/tavily_raw_bench.py")
    items = json.loads(corpus.read_text(encoding="utf-8"))
    out: list[str] = []
    for item in items:
        for chunk in chunk_text(
            normalize_web_text(item["text"]), parser_id="tavily", max_chars=400
        ):
            out.append(chunk.text)
            if len(out) >= N_BLOCKS:
                return out
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    from fastembed import TextEmbedding

    texts = blocks()
    print(f"模型 {MODEL} · {len(texts)} 个真实块（400 字）")
    try:
        model = TextEmbedding(model_name=MODEL)
    except Exception as error:
        print(f"fastembed 不支持这个模型（或下载失败）：{type(error).__name__}: {error}")
        print("→ 归档（fastembed 不支持 = 要自己导 ONNX + 自己写池化，改造面一个量级）")
        return

    vectors = model.embed(texts[:2])          # 预热（首次含模型加载）
    dims = len(vectors[0])
    print(f"维度 {dims}")

    samples = []
    for index, text in enumerate(texts):
        start = time.perf_counter()
        model.embed([text])
        samples.append((time.perf_counter() - start) * 1000)
    median = statistics.median(samples)
    print(f"\n单块耗时：中位 {median:.0f} ms · 均值 {statistics.mean(samples):.0f} ms"
          f" · 最慢 {max(samples):.0f} ms")
    per_round = median * 120
    print(f"每轮 120 块（联网路的向量预算）→ {per_round / 1000:.1f} 秒")
    print(f"判据（≤ {BUDGET_MS:.0f} ms/块）→ {'**通过，值得继续谈**' if median <= BUDGET_MS else '**不通过 → 归档**'}")
    (OUT / "qwen3_embed_bench.json").write_text(
        json.dumps(
            {
                "model": MODEL,
                "dimensions": dims,
                "blocks": len(texts),
                "median_ms": median,
                "mean_ms": statistics.mean(samples),
                "max_ms": max(samples),
                "per_round_120_blocks_s": per_round / 1000,
                "budget_ms": BUDGET_MS,
                "verdict": "pass" if median <= BUDGET_MS else "archive",
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
