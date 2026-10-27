"""模拟调研真跑（M10 验收）：草案卡 → 跑一趟 → 收下 → 带进下一轮 → SIMULATED 落账。

与 `discussion_round_smoke.py` 同款：ASGI 直连、临时库、留样开、退出码非 0 即失败。
问卷出现就脚本化作答（首选项＋短文本）。

```bash
sqlite3 backend/data/pmstudio.db ".backup /tmp/pmstudio-m10.db"
cd backend && set -a && . ./.env && set +a
PMSTUDIO_DB=/tmp/pmstudio-m10.db PMSTUDIO_TRACE_MODEL=1 \
  python3 scripts/sim_inquiry_smoke.py
# 迭代时续跑已有轮（不重开整轮，省十几分钟）：
PMSTUDIO_DB=/tmp/pmstudio-m10.db PMSTUDIO_TRACE_MODEL=1 \
  python3 scripts/sim_inquiry_smoke.py --resume r-xxxx
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
    "运营每天都要用订单导出：按时间筛选、支持大批量、导出字段可配置，月底上线。"
)


def show(step: str, detail: str) -> None:
    print(f"[{step}] {detail}", flush=True)


def sim_eids(db_path: str) -> set[str]:
    """账本里 simulation 来路的 SIMULATED 编号（直查库，不走 HTTP）。"""

    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    found: set[str] = set()
    for (payload,) in db.execute("SELECT payload FROM evidence_ledgers"):
        import json

        for item in json.loads(payload):
            source = item.get("source") or {}
            if item.get("kind") == "simulated" and str(
                source.get("external_ref") or ""
            ).startswith("simulation:"):
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
    if args.resume:
        # 续跑已有轮次（迭代用）：跳过建项目/起轮/答问卷，直接从草案卡跑模拟
        current = await client.get(f"/api/rounds/{args.resume}")
        if current.status_code != 200:
            show("resume", f"读轮失败 {current.status_code} {current.text[:200]}")
            return 1
        body = current.json()
        round_id = args.resume
        project_id = body["project_id"]
        if body["waiting_on"] != "draft_card" or body["draft"] is None:
            show("resume", f"不是草案卡状态：waiting_on={body['waiting_on']}")
            return 1
        show("resume", f"{round_id} 已在草案卡（模型档案 {args.resume}）")
    else:
        # 建项目
        created = await client.post("/api/projects", json={"title": "模拟调研真跑"})
        if created.status_code not in (200, 201):
            show("create", f"失败 {created.status_code} {created.text[:300]}")
            return 1
        project_id = created.json()["project_id"]
        show("create", f"项目 {project_id}")

        # 起轮
        started = await client.post(
            f"/api/projects/{project_id}/rounds",
            json={"input": INPUT},
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

        # 开场卡
        opened = await client.post(
            f"/api/rounds/{round_id}/opening", json={"seats": seats, "claim_edits": []}
        )
        if opened.status_code != 200:
            show("opening", f"失败 {opened.status_code} {opened.text[:300]}")
            return 1
        body = opened.json()
        show("opening", f"waiting_on={body['waiting_on']}")

        # 答到草案卡
        body = await answer_until_draft(client, round_id, body)
        if body["waiting_on"] != "draft_card" or body["draft"] is None:
            show("draft", f"没等到草案卡：waiting_on={body['waiting_on']}")
            return 1
        show("draft", f"草案 {len(body['draft']['proposals'])} 条")

    # 跑一趟模拟
    sim = await client.post(f"/api/rounds/{round_id}/sim-inquiry", json={})
    if sim.status_code != 200:
        show("sim", f"失败 {sim.status_code} {sim.text[:300]}")
        return 1
    sim_body = sim.json()
    sim_view = sim_body.get("sim_inquiry")
    if sim_view is None:
        show("sim", "sim_inquiry 是 null——跑失败了？")
        return 1
    show(
        "sim",
        f"切片 {len(sim_view['slices'])} / 假设 {len(sim_view['hypotheses'])}"
        f" / 盲区 {len(sim_view['unknowns'])}",
    )
    for h in sim_view["hypotheses"]:
        show("sim", f"  {h['local_id']} [{h['grounding']}] {h['statement'][:50]}")
    for u in sim_view["unknowns"]:
        show("sim", f"  {u['local_id']} {u['statement'][:50]}")

    # 收下第一条假设
    if not sim_view["hypotheses"]:
        show("sim", "没有假设可收——跳过收下")
        return 1
    chosen_id = sim_view["hypotheses"][0]["local_id"]
    kept = await client.post(
        f"/api/rounds/{round_id}/sim-inquiry/keep",
        json={"chosen": [chosen_id]},
    )
    if kept.status_code != 200:
        show("keep", f"失败 {kept.status_code} {kept.text[:300]}")
        return 1
    kept_body = kept.json()
    show("keep", f"收下 {kept_body['kept']} → 材料 {kept_body['material_id']}")

    # 下一轮带上
    started2 = await client.post(
        f"/api/projects/{project_id}/rounds",
        json={"input": "按模拟的假设继续", "material_ids": [kept_body["material_id"]]},
    )
    if started2.status_code != 200:
        show("start2", f"失败 {started2.status_code} {started2.text[:300]}")
        return 1
    body2 = started2.json()
    round2_id = body2["round_id"]
    show("start2", f"{round2_id} 已开，带材料 {kept_body['material_id']}")

    # 账本里应该有 SIMULATED
    sim_ledger = sim_eids(db_path)
    show("ledger", f"simulation 来路的 SIMULATED 编号：{sorted(sim_ledger) or '（无！）'}")
    if not sim_ledger:
        show("ledger", "材料进轮了却没有 SIMULATED 行——`_material_kind` 那一行没生效？")
        return 1

    # 留样
    if os.environ.get("PMSTUDIO_TRACE_MODEL") == "1":
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT purpose, count(*), sum(ok) FROM model_calls WHERE round_id IN (?, ?)"
            " GROUP BY purpose ORDER BY purpose",
            (round_id, round2_id),
        ).fetchall()
        total = sum(count for _, count, _ in rows)
        for purpose, count, ok in rows:
            show("trace", f"{purpose}：{count} 次 / {ok} 成功")
        show("trace", f"两轮共 {total} 次模型调用")

    print(f"OK project={project_id} round1={round_id} round2={round2_id}", flush=True)
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--resume", default=None, help="已有轮次 id：跳过建项目与答问卷，直接跑模拟"
    )
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
