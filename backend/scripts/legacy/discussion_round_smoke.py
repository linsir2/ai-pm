"""带讨论材料真起轮（M9 真跑验收 P2）：把收下的卡带进一轮，验 `SIMULATED` 链不断。

不断的是这三段（设计 `02` §11.0.45 的验收口径）：
1. 材料块进账本是 `SIMULATED`，`external_ref` 前缀 `discussion:{did}#`；
2. 只引这些编号的提案 `origin_type == proposed`（默认不勾由前端纯函数定，vitest 已覆）；
3. confirm 后文档多一版。

问卷出现就脚本化作答（首选项＋短文本，`smoke.py` 同款）；`--stop-after questionnaire`
停在问卷态供 P3 拍照，`--resume ROUND` 从已有轮次继续（不重开）：

```bash
sqlite3 backend/data/pmstudio.db ".backup /tmp/pmstudio-m9/db.db"
cd backend && set -a && . ./.env && set +a
PMSTUDIO_DB=/tmp/pmstudio-m9/db.db PMSTUDIO_TRACE_MODEL=1 \
  python3 scripts/discussion_round_smoke.py p-851a63a6 <material_id> --stop-after questionnaire
# …拍照…
PMSTUDIO_DB=/tmp/pmstudio-m9/db.db PMSTUDIO_TRACE_MODEL=1 \
  python3 scripts/discussion_round_smoke.py p-851a63a6 <material_id> --resume <round_id>
```

退出码非 0 表示失败，打印哪一步、什么原因。
"""

import argparse
import asyncio
import os
import sqlite3
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pmstudio.bootstrap.container import build_container  # noqa: E402
from pmstudio.bootstrap.settings import Settings  # noqa: E402

INPUT = (
    "讨论区那条结论说导出筛选是范围蔓延，先把这条缺口记进文档：导出功能缺 PRD，"
    "讨论里收下的那份材料就是依据。"
)


def show(step: str, detail: str) -> None:
    print(f"[{step}] {detail}", flush=True)


def simulated_eids(db_path: str) -> set[str]:
    """账本里 discussion 来路的 SIMULATED 编号（直查库，不走 HTTP——断言用，诚实直接）。"""

    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    found: set[str] = set()
    for (payload,) in db.execute("SELECT payload FROM evidence_ledgers"):
        import json

        for item in json.loads(payload):
            source = item.get("source") or {}
            if item.get("kind") == "simulated" and str(
                source.get("external_ref") or ""
            ).startswith("discussion:"):
                found.add(item["eid"])
    return found


async def answer_until_draft(client: httpx.AsyncClient, round_id: str, body: dict) -> dict:
    while body["waiting_on"] == "questionnaire":
        answers = []
        for question in body["questions"]:
            options = question["options"]
            answers.append(
                {
                    "question_id": question["question_id"],
                    "selected": [options[0][0]] if options else [],
                    "text": "" if options else "先按最简单的方式实现",
                }
            )
            show(
                "question",
                f"{question['kind']} {question['title'][:40]} → "
                f"{options[0][1] if options else '（开放题，短文本）'}",
            )
        answered = await client.post(
            f"/api/rounds/{round_id}/answers",
            json={"form_id": body["form_id"], "answers": answers, "skipped": []},
        )
        if answered.status_code != 200:
            show("answers", f"失败 {answered.status_code} {answered.text[:300]}")
            raise SystemExit(1)
        body = answered.json()
        show("answers", f"答完 → waiting_on={body['waiting_on']}")
    return body


async def run(client: httpx.AsyncClient, db_path: str, args) -> int:
    versions_before = await client.get(
        f"/api/projects/{args.project_id}/versions"
    )
    # `/versions` 回的是 {"versions": [...], "current": …}——数 list，不数 dict 的键
    #（数错过一次：dict 恒有 2 个键，写了 v3 也显示"2 → 2"，修过）。
    body_before = versions_before.json() if versions_before.status_code == 200 else {}
    if "versions" not in body_before:
        show("confirm", f"读版本列表失败 {versions_before.status_code}——版本数无从比对")
        return 1
    count_before = len(body_before["versions"])

    if args.resume:
        round_id = args.resume
        show("resume", f"从已有轮次 {round_id} 继续（不重开）")
        current = await client.get(f"/api/rounds/{round_id}")
        if current.status_code != 200:
            show("resume", f"读轮失败 {current.status_code}")
            return 1
        body = current.json()
    else:
        started = await client.post(
            f"/api/projects/{args.project_id}/rounds",
            json={"input": INPUT, "material_ids": [args.material_id]},
        )
        if started.status_code != 200:
            show("start", f"失败 {started.status_code} {started.text[:300]}")
            return 1
        body = started.json()
        round_id = body["round_id"]
        for claim in body["claims"]:
            show("claim", f"{claim['eid']} {claim['label']}：{claim['text'][:50]}")
        seats = [seat["seat_id"] for seat in body["nominated_seats"]]
        show("start", f"{round_id} 已开，提名：{'、'.join(seats)}")
        opened = await client.post(
            f"/api/rounds/{round_id}/opening", json={"seats": seats, "claim_edits": []}
        )
        if opened.status_code != 200:
            show("opening", f"失败 {opened.status_code} {opened.text[:300]}")
            return 1
        body = opened.json()
        n_questions = len(body.get("questions", []))
        show("opening", f"waiting_on={body['waiting_on']}，题目 {n_questions} 道")

    if args.stop_after == "questionnaire":
        show("stop", f"停在 waiting_on={body['waiting_on']}（round_id={round_id}，供拍照）")
        print(f"STOPPED round={round_id} waiting_on={body['waiting_on']}", flush=True)
        return 0

    body = await answer_until_draft(client, round_id, body)
    if body["waiting_on"] != "draft_card" or body["draft"] is None:
        show("draft", f"没等到草案卡：waiting_on={body['waiting_on']}")
        return 1

    sim = simulated_eids(db_path)
    show("ledger", f"discussion 来路的 SIMULATED 编号：{sorted(sim) or '（无！）'}")
    if not sim:
        show("ledger", "材料进轮了却没有 SIMULATED 行——`_material_kind` 那一行没生效？")
        return 1
    proposals = body["draft"]["proposals"]
    sim_only = [
        proposal
        for proposal in proposals
        if proposal["eids"] and set(proposal["eids"]) <= sim
    ]
    show("draft", f"草案 {len(proposals)} 条，其中只引 SIMULATED 的 {len(sim_only)} 条")
    bad = [p for p in sim_only if p["origin_type"] != "proposed"]
    if bad:
        leaked = [p["proposal_id"] for p in bad]
        show("draft", f"这 {len(bad)} 条只引 SIMULATED 却不是 proposed：{leaked}")
        return 1
    if sim_only:
        show("draft", "只引 SIMULATED 的提案全是 proposed（默认不勾由前端纯函数定，vitest 已覆）")
    else:
        show("draft", "注意：本轮没有只引 SIMULATED 的提案——'默认不勾'无真实例，只记不判")

    accepted = [p["proposal_id"] for p in proposals if p["origin_type"] != "proposed"]
    confirmed = await client.post(
        f"/api/rounds/{round_id}/confirm", json={"accepted": accepted}
    )
    if confirmed.status_code != 200:
        show("confirm", f"失败 {confirmed.status_code} {confirmed.text[:300]}")
        return 1
    version = confirmed.json()["version_no"]
    versions_after = await client.get(f"/api/projects/{args.project_id}/versions")
    body_after = versions_after.json() if versions_after.status_code == 200 else {}
    if "versions" not in body_after:
        show("confirm", f"读版本列表失败 {versions_after.status_code}——版本数无从比对")
        return 1
    count_after = len(body_after["versions"])
    show("confirm", f"写入成功 v{version}；版本数 {count_before} → {count_after}")
    if count_after != count_before + 1:
        show("confirm", "版本数没涨 1——写入语义不对")
        return 1

    if os.environ.get("PMSTUDIO_TRACE_MODEL") == "1":
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT purpose, count(*), sum(ok) FROM model_calls WHERE round_id = ?"
            " GROUP BY purpose ORDER BY purpose",
            (round_id,),
        ).fetchall()
        total = sum(count for _, count, _ in rows)
        for purpose, count, ok in rows:
            show("trace", f"{purpose}：{count} 次 / {ok} 成功")
        show("trace", f"本轮共 {total} 次模型调用")
    print(f"OK round={round_id} version={version}", flush=True)
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id")
    parser.add_argument("material_id")
    parser.add_argument("--resume", default=None)
    parser.add_argument("--stop-after", default=None)
    args = parser.parse_args()

    settings = Settings.from_env()
    os.environ.setdefault("PMSTUDIO_DB", str(settings.db_path))
    show("boot", f"模型 {settings.model}；库 {settings.db_path}")
    started = time.monotonic()
    app = build_container(settings).app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://smoke", timeout=300.0
    ) as client:
        code = await run(client, str(settings.db_path), args)
    show("wall", f"共耗时 {(time.monotonic() - started):.1f}s")
    return code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
