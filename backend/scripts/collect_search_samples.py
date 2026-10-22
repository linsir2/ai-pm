"""从应用日志里抽出**材料检索的观测行** → 攒成真实查询样本。

## 为什么（批 12 的 R8 → 批 13 扩到每次检索）

检索的判据**全是「从文档反推」的合成查询**——真实的用户查询分布是**空的**
→ 语义召回的增量没法证明（R9：真数据 r@5 9/11 与纯 FTS5 持平，"改述增量"一直是 0）。
要看到增量，得先有**真查询**。

`SqliteHybridRetriever.search` 现在每次检索都记一行**分解**：

    material_search project=p1 query='导出权限谁来定' fts_candidates=20 \
        vec_candidates=12 fts_hits=3 hybrid_hits=5

这些字段足够算**救回率**（词面 0 命中而混合命中 → 语义把它捞了回来）与
**挤掉率**（词面命中而混合 0 命中 → 融合把好结果掀翻了）。

## 用法

    cd backend && python scripts/collect_search_samples.py /path/to/app.log \
        -o ../.codex/runs/<今天>/search_samples.json

默认输出到 `.codex/runs/<今天>/search_samples.json`（**gitignored**，不入库）。
攒够了，它们就是**真查询判据集**（R9 的输入）。
"""

import argparse
import ast
import json
import re
from datetime import date
from pathlib import Path

#: 观测行的形状（`logger.info` 的 %r → Python 的 repr，用 `ast.literal_eval` 还原）
PATTERN = re.compile(
    r"material_search project=(?P<project>\S+) query=(?P<query>'(?:[^'\\]|\\.)*')"
    r"(?P<rest>.*)"
)


def parse_line(line: str) -> dict | None:
    match = PATTERN.search(line)
    if not match:
        return None
    fields = dict(
        re.findall(
            r"(fts_candidates|vec_candidates|fts_hits|hybrid_hits)=(\d+)",
            match.group("rest"),
        )
    )
    if not fields:
        return None
    return {
        "project": match.group("project"),
        "query": ast.literal_eval(match.group("query")),
        **{name: int(value) for name, value in fields.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="抽出材料检索的观测行")
    parser.add_argument("logs", nargs="+", type=Path, help="应用日志文件")
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=Path(f"../.codex/runs/{date.today().isoformat()}/search_samples.json"),
        help="输出 JSON（默认落在 gitignored 的 runs 目录）",
    )
    args = parser.parse_args()

    samples: dict[tuple[str, str], dict] = {}
    for path in args.logs:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            item = parse_line(line)
            if item is None:
                continue
            samples[(item["project"], item["query"])] = item

    payload = {"count": len(samples), "samples": [samples[key] for key in sorted(samples)]}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"抽出 {len(samples)} 条检索样本 → {args.out}")


if __name__ == "__main__":
    main()
