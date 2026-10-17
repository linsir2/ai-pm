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
from dataclasses import replace
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "src"))
sys.path.insert(0, str(BACKEND_ROOT))

from evals.assertions import EVAL_ASSERTIONS, check  # noqa: E402

from pmstudio.application.commands import RoundCommand  # noqa: E402
from pmstudio.bootstrap.container import build_container  # noqa: E402
from pmstudio.bootstrap.settings import Settings  # noqa: E402
from pmstudio.domain.shared.ids import ProjectId, RoundId, UserId  # noqa: E402
from pmstudio.harness.errors import ApplicationError  # noqa: E402
from pmstudio.infrastructure.sqlite import owner_scope  # noqa: E402

CASES_DIR = BACKEND_ROOT / "evals" / "cases"
#: eval 默认跑在这个库上：**永远不碰工作库**（`Settings.from_env()` 里 PMSTUDIO_DB 优先级更高，
#: 所以这里必须显式覆盖，不能只靠 default_db）。
DEFAULT_EVAL_DB = Path("/tmp/pmstudio-eval.db")

#: 一轮失败时最多再点几次「继续」。
#:
#: 为什么驱动要会点：**产品本来就支持续跑**（`Round.resume()` + `POST /retry`，用户在界面上
#: 就是这么做的），而驱动原来一次都不点——模型偶发"回包不是 JSON"就把整条用例判死
#: （P35 真踩到两次：整合、席位各一次，同输入重跑就过）。点几次不是放宽判据：
#: 断言照旧跑在最终视图上，日志里每一次重试都打出来（`[重试] …`），要查得到。
MAX_RETRIES = 2


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


async def run_step(  # type: ignore[no-untyped-def]
    services, command: RoundCommand, *, project_id: str | None = None
):  # type: ignore[no-untyped-def]
    """跑一个动作；**网关类失败不往外抛**——把这一轮现在的样子读回来。

    为什么：超时 / "回包不是 JSON"这类失败会**先把这一轮标成 `failed` 再抛异常**
    （`fail_round(...)` + `raise`）。异常冒出去，驱动就既看不到 `failure_reason`、
    也拿不到 `round_id`，只能把整条用例报成"崩溃"——而产品侧本来就是"失败 → 用户点继续"。
    所以这里接住，再从轮次表把视图读回来（`get_round`；`start` 那次从 `list_rounds` 找最新一轮）。
    """

    try:
        return await services.runner.run(command)
    except ApplicationError:
        round_id = command.round_id
        if round_id is None and project_id is not None:
            heads = await services.list_rounds(project_id=ProjectId(project_id))
            round_id = heads[0].round_id if heads else None  # 最新在前
        if round_id is None:
            raise
        return await services.get_round(round_id=RoundId(round_id))


def resolve_eval_db(chosen: str | None, *, working_db: Path) -> Path:
    """eval 用哪个库：默认 /tmp，且**拒绝**跑在工作库上（会往里建项目和轮次）。"""

    eval_db = Path(chosen) if chosen else DEFAULT_EVAL_DB
    if eval_db.resolve() == working_db.resolve():
        raise SystemExit(
            f"拒绝：eval 不能跑在工作库上（{eval_db}）。换个路径，或用默认的 /tmp。"
        )
    return eval_db


async def run_case(services, case: dict) -> bool:  # type: ignore[no-untyped-def]
    """跑一条用例：建项目 → 一轮走到底（问卷一律跳过，题目本身也是断言对象）。"""

    print(f"\n=== {case['name']} · {case['note']}", flush=True)
    project = await services.create_project(title=f"eval · {case['name']}")
    project_id = project.project_id

    view = await run_step(
        services,
        RoundCommand(kind="start", project_id=str(project_id), user_input=case["input"]),
        project_id=str(project_id),
    )
    show("主张", f"{len(view.claims)} 条 · 提名 {len(view.nominated_seats)} 个席位")

    if view.step != "failed":
        view = await run_step(
            services,
            RoundCommand(
                kind="submit_opening",
                round_id=view.round_id,
                seats=tuple(seat.seat_id for seat in view.nominated_seats),
            ),
        )
        show("席位", f"{len(view.seat_packets)} 个 · 留痕 {len(view.dropped)} 条")

    retries = 0
    while True:
        while view.waiting_on == "questionnaire":
            show("问卷", f"{len(view.questions)} 题（全部按跳过走）")
            view = await run_step(
                services,
                RoundCommand(
                    kind="submit_answers",
                    round_id=view.round_id,
                    form_id=view.form_id or "",
                    answers=(),
                    skipped=tuple(question.question_id for question in view.questions),
                ),
            )
        if view.step != "failed" or retries >= MAX_RETRIES:
            break
        retries += 1
        show(
            "重试",
            f"第 {retries} 次：{view.failure_reason or '（没说原因）'}"
            f"（从 {view.retry_from or '?'} 接着跑）",
        )
        view = await run_step(
            services, RoundCommand(kind="retry", round_id=view.round_id)
        )

    if view.step == "failed":
        print(f"  [失败] 点了 {retries} 次继续仍然失败：{view.failure_reason}", flush=True)
        return False

    passed = True
    # `assertions` 是整份替换；`extra_assertions` 是在这条用例上**再加**一条
    # （如矛盾用例额外查 kind=conflict）
    assertions = list(case.get("assertions", EVAL_ASSERTIONS)) + list(
        case.get("extra_assertions", ())
    )
    for assertion in assertions:
        failure = check(assertion, view)
        if failure is None:
            show("PASS", assertion["type"])
        else:
            passed = False
            show("FAIL", f"{assertion['type']}：{failure}")
    return passed


async def run_one(services, case: dict) -> tuple[bool, str]:  # type: ignore[no-untyped-def]
    """跑一条用例，**把它的异常兜住**，返回 `(过没过, 崩溃原因或空串)`。

    真踩过：case-a 第一次真模型运行时复述对不上原文（`PacketParseError`）——那时异常直接
    冒到 `main`，8 条用例在第 1 条就整体退出，连一份"哪几条红了"的报告都拿不到。
    断言失败只是这一条红，模型抖动也是这一条红；两种都记成 FAIL，剩下的照跑。
    """

    try:
        return await run_case(services, case), ""
    except Exception as error:  # noqa: BLE001 —— 兜住才叫"全跑完再报"
        return False, f"{type(error).__name__}: {error}"


async def main() -> int:
    parser = argparse.ArgumentParser(description="真模型 eval")
    parser.add_argument("--case", default=None, help="只跑某一条（按 name）")
    parser.add_argument(
        "--db", default=None, help=f"eval 用的库（默认 {DEFAULT_EVAL_DB}，不会碰工作库）"
    )
    args = parser.parse_args()

    cases = load_cases(args.case)
    if not cases:
        print(f"没有这条用例：{args.case}", file=sys.stderr)
        return 2

    settings = Settings.from_env()
    eval_db = resolve_eval_db(
        args.db or os.environ.get("PMSTUDIO_EVAL_DB"), working_db=settings.db_path
    )
    if eval_db.exists():
        eval_db.unlink()  # 每次从干净库开始：eval 要可重复
    settings = replace(settings, db_path=eval_db)  # 不许被 .env 的 PMSTUDIO_DB 覆盖回去
    print(
        f"库：{eval_db} · 模型 {settings.model} · 超时 {settings.model_timeout}s"
        f" · 留样 {'开' if settings.trace_model else '关'}",
        flush=True,
    )
    services = build_container(settings).app.state.services
    # 多用户改造后内容仓储读写要 owner：eval 是单用户跑法，固定一个 eval 身份
    #（与 e2e 的 E2E_OWNER 同理，id 不同避免混淆）。不设会崩在第一轮。
    owner_scope.set_owner_scope(UserId("u-eval"))

    results: list[tuple[str, bool]] = []
    for case in cases:
        ok, crash = await run_one(services, case)
        if crash:
            show("崩溃", crash)  # 这一轮没跑完，没有断言可看，原因就是报告本身
        results.append((case["name"], ok))

    passed = sum(1 for _, ok in results if ok)
    print(f"\n通过 {passed}/{len(results)}", flush=True)
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}", flush=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
