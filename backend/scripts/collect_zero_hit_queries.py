"""从应用日志里抽出「材料零命中」的查询 → 攒成判据样本。

## 为什么要有这个（批 12，R8）

检索的判据**全是「从文档反推」的合成查询**——真实的用户查询分布是**空的**
→ 语义召回的价值没法证明（R9：真数据 r@5 9/11 与纯 FTS5 持平，"改述增量"一直是 0）。
要看到增量，得先有**真查询**。

`SearchProject` 现在会在「非空查询 + 一个材料都没命中」时打一行：

    material_search_zero_hit project=p1 query='导出权限谁来定' total_hits=0

这脚本把那行抽成 JSON；攒够了，它们就是**真查询判据集**（R9 的输入）。

## 用法

    cd backend && python scripts/collect_zero_hit_queries.py /path/to/app.log \
        -o ../.codex/runs/<今天>/zero_hit_queries.json

默认输出到 `.codex/runs/<今天>/zero_hit_queries.json`（**gitignored**，不入库）。
"""

import argparse
import ast
import json
import re
from datetime import date
from pathlib import Path

#: 那行日志的形状（`logger.info` 的 %r → Python 的 repr，用 `ast.literal_eval` 还原）
PATTERN = re.compile(
    r"material_search_zero_hit project=(?P<project>\S+) query=(?P<query>'(?:[^'\\]|\\.)*')"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="抽出零命中的材料检索查询")
    parser.add_argument("logs", nargs="+", type=Path, help="应用日志文件")
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=Path(f"../.codex/runs/{date.today().isoformat()}/zero_hit_queries.json"),
        help="输出 JSON（默认落在 gitignored 的 runs 目录）",
    )
    args = parser.parse_args()

    samples: dict[tuple[str, str], dict[str, str]] = {}
    for path in args.logs:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = PATTERN.search(line)
            if not match:
                continue
            query = ast.literal_eval(match.group("query"))
            project = match.group("project")
            samples[(project, query)] = {"project": project, "query": query}

    payload = {
        "count": len(samples),
        "samples": [samples[key] for key in sorted(samples)],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"抽出 {len(samples)} 条零命中查询 → {args.out}")


if __name__ == "__main__":
    main()
