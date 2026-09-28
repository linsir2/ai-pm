"""真模型 eval：把 `evals/cases/*.json` 逐条跑一遍，打印断言结果与通过率。

它不是测试（不进 CI）：真模型慢、要 key、会抖。用法：

```bash
cd backend
uv run python scripts/eval.py                     # 全跑
uv run python scripts/eval.py --case case-a-orders
```

退出码 0 = 全通过。判断逻辑在 `evals/assertions.py`（纯函数，有离线测试）。

**为什么直接调用用例、不走 HTTP**：eval 要测的是"提示词与编排在真模型下有没有退化"，
不是传输层（传输层由 `tests/api` 与 `scripts/smoke.py` 管）。走用例拿到的是真
`RoundView`，省掉一整层 JSON 反序列化。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "src"))
sys.path.insert(0, str(BACKEND_ROOT))

from evals.assertions import EVAL_ASSERTIONS, check  # noqa: E402

from pmstudio.application.commands import RoundCommand  # noqa: E402
from pmstudio.bootstrap.container import build_container  # noqa: E402
from pmstudio.bootstrap.settings import Settings  # noqa: E402

CASES_DIR = BACKEND_ROOT / "evals" / "cases"


def load_cases(only: str | None) -> list[dict]:
    cases = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(CASES_DIR.glob("*.json"))
    ]
    if only is None:
        return cases
    return [case for case in cases if case["name"] == only]


def show(stage: str, detail: str = "") -> None:
    print(f"  [{stage}] {detail}".rstrip(), flush=True)


async def run_case(services, case: dict) -> bool:  # type: ignore[no-untyped-def]
    """跑一条用例：建项目 → 一轮走到底（问卷一律跳过，题目本身也是断言对象）。"""

    print(f"\n=== {case['name']} · {case['note']}", flush=True)
    project = await services.create_project(title=f"eval · {case['name']}")
    project_id = project.project_id

    view = await services.runner.run(
        RoundCommand(kind="start", project_id=str(project_id), user_input=case["input"])
    )
    show("主张", f"{len(view.claims)} 条 · 提名 {len(view.nominated_seats)} 个席位")

    view = await services.runner.run(
        RoundCommand(
            kind="submit_opening",
            round_id=view.round_id,
            seats=tuple(seat.seat_id for seat in view.nominated_seats),
        )
    )
    show("席位", f"{len(view.seat_packets)} 个 · 留痕 {len(view.dropped)} 条")

    while view.waiting_on == "questionnaire":
        show("问卷", f"{len(view.questions)} 题（全部按跳过走）")
        view = await services.runner.run(
            RoundCommand(
                kind="submit_answers",
                round_id=view.round_id,
                form_id=view.form_id or "",
                answers=(),
                skipped=tuple(question.question_id for question in view.questions),
            )
        )

    if view.step == "failed":
        print(f"  [失败] {view.failure_reason}", flush=True)
        return False

    passed = True
    for assertion in case.get("assertions", EVAL_ASSERTIONS):
        failure = check(assertion, view)
        if failure is None:
            show("PASS", assertion["type"])
        else:
            passed = False
            show("FAIL", f"{assertion['type']}：{failure}")
    return passed


async def main() -> int:
    parser = argparse.ArgumentParser(description="真模型 eval")
    parser.add_argument("--case", default=None, help="只跑某一条（按 name）")
    args = parser.parse_args()

    cases = load_cases(args.case)
    if not cases:
        print(f"没有这条用例：{args.case}", file=sys.stderr)
        return 2

    db_path = Path(os.environ.get("PMSTUDIO_EVAL_DB", "/tmp/pmstudio-eval.db"))
    if db_path.exists():
        db_path.unlink()  # 每次从干净库开始：eval 要可重复
    services = build_container(Settings.from_env(default_db=db_path)).app.state.services

    results: list[tuple[str, bool]] = []
    for case in cases:
        results.append((case["name"], await run_case(services, case)))

    passed = sum(1 for _, ok in results if ok)
    print(f"\n通过 {passed}/{len(results)}", flush=True)
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}", flush=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
