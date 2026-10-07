"""模拟评测真跑（M11 验收）：草案卡 → 评测 → 减提案进草案 → SIMULATED 落账。

与 `sim_inquiry_smoke.py` 同款：ASGI 直连、临时库、留样开、退出码非 0 即失败。
问卷出现就脚本化作答（首选项＋短文本）。评测对象是**当前已确认文档**里的功能——
`--project` 要指一个有功能子树的项目（如狗粮项目 p-851a63a6）。

```bash
sqlite3 backend/data/pmstudio.db ".backup /tmp/pmstudio-m11.db"
cd backend && set -a && . ./.env && set +a
PMSTUDIO_DB=/tmp/pmstudio-m11.db PMSTUDIO_TRACE_MODEL=1 \
  python3 scripts/sim_review_smoke.py --project p-851a63a6
# 迭代时续跑已有草案轮（不重开整轮，省十几分钟）：
PMSTUDIO_DB=/tmp/pmstudio-m11.db PMSTUDIO_TRACE_MODEL=1 \
  python3 scripts/sim_review_smoke.py --resume r-xxxx
```

退出码非 0 表示失败，打印哪一步、什么原因。
"""

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pmstudio.bootstrap.container import build_container  # noqa: E402
from pmstudio.bootstrap.settings import Settings  # noqa: E402
from pmstudio.infrastructure.personas.catalog import load_personas  # noqa: E402

INPUT = "把模拟评测的走查结果接进草案卡：模拟使用者指出哪里走不通，产出删减建议。"


def show(step: str, detail: str) -> None:
    print(f"[{step}] {detail}", flush=True)


def document_features(client_view: dict) -> list[str]:
    """文档视图里的功能序（上到下、去重）。"""

    slugs: list[str] = []

    def walk(block: dict) -> None:
        slug = block.get("feature_slug")
        if slug and slug not in slugs:
            slugs.append(slug)
        for child in block.get("children") or ():
            walk(child)

    for section in client_view.get("sections") or ():
        for block in section.get("blocks") or ():
            walk(block)
    return slugs


def sim_eids(db_path: str) -> list[str]:
    """账本里 simulation:review 来路的 SIMULATED 编号（直查库，不走 HTTP）。"""

    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    found: list[str] = []
    for (payload,) in db.execute("SELECT payload FROM evidence_ledgers"):
        for item in json.loads(payload):
            ref = str((item.get("source") or {}).get("external_ref") or "")
            if item.get("kind") == "simulated" and ref.startswith("simulation:review:"):
                found.append(item["eid"])
    return sorted(found)


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


async def drive_to_draft(client: httpx.AsyncClient, project_id: str) -> tuple[str, dict]:
    started = await client.post(
        f"/api/projects/{project_id}/rounds", json={"input": INPUT}
    )
    if started.status_code != 200:
        show("start", f"失败 {started.status_code} {started.text[:300]}")
        raise SystemExit(1)
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
        raise SystemExit(1)
    body = opened.json()
    body = await answer_until_draft(client, round_id, body)
    if body["waiting_on"] != "draft_card" or body["draft"] is None:
        show("draft", f"没等到草案卡：waiting_on={body['waiting_on']}")
        raise SystemExit(1)
    show("draft", f"草案 {len(body['draft']['proposals'])} 条")
    return round_id, body


async def run(client: httpx.AsyncClient, db_path: str, args) -> int:
    if args.resume:
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
        show("resume", f"{round_id} 已在草案卡")
    else:
        if not args.project:
            show("boot", "要么 --project（有功能子树的项目），要么 --resume（草案轮）")
            return 2
        project_id = args.project
        round_id, _ = await drive_to_draft(client, project_id)
        show("project", project_id)

    # 评测对象：当前已确认文档里的功能（前 2 个）＋ 目录前 3 个 persona
    document = await client.get(f"/api/projects/{project_id}/document")
    if document.status_code != 200:
        show("document", f"失败 {document.status_code} {document.text[:200]}")
        return 1
    features = document_features(document.json())[:2]
    if not features:
        show("features", "文档里没有功能子树——评测对象是已确认文档里的功能，换个项目")
        return 1
    personas = load_personas()[:3]
    payload = {
        "persona_ids": [item.persona_id for item in personas],
        "feature_slugs": features,
    }
    show("review", f"{'、'.join(item.title for item in personas)} × {'、'.join(features)}")

    reviewed = await client.post(f"/api/rounds/{round_id}/sim-review", json=payload)
    if reviewed.status_code != 200:
        show("review", f"失败 {reviewed.status_code} {reviewed.text[:400]}")
        return 1
    view = reviewed.json()
    review = view.get("sim_review")
    if review is None:
        show("review", "sim_review 是 null——跑失败了？")
        return 1
    summary = review["summary"]
    show(
        "review",
        f"顺 {summary['smooth']} / 有摩擦 {summary['friction']} / 走不通 {summary['blocked']}"
        f" · 基于文档 v{review['evaluated_version']}",
    )
    for persona in review["personas"]:
        for feature in persona["features"]:
            show(
                "review",
                f"  {persona['title']}·{feature['feature_slug']} → {feature['verdict']}"
                + ("" if feature["started"] else "（开不了头）"),
            )
            for item in feature["frictions"]:
                show("review", f"      [{item['kind']}] {item['note'][:60]}")
    for item in review["advisories"]:
        show("advisory", item)

    sim_props = [
        item for item in view["draft"]["proposals"] if item.get("simulation_based")
    ]
    for item in sim_props:
        show("proposal", f"  {item['proposal_id']} {item['op']} {item['text'][:50]}")
    if not sim_props:
        show("proposal", "没有减提案（全体走通/未触发规则——如实，不算失败）")

    eids = sim_eids(db_path)
    show("ledger", f"simulation:review 来路的 SIMULATED 编号：{eids or '（无）'}")
    if sim_props and not eids:
        show("ledger", "有提案却没有编号——按需发号那一行没生效？")
        return 1

    if os.environ.get("PMSTUDIO_TRACE_MODEL") == "1":
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT purpose, count(*), sum(ok) FROM model_calls WHERE round_id = ?"
            " GROUP BY purpose ORDER BY purpose",
            (round_id,),
        ).fetchall()
        for purpose, count, ok in rows:
            show("trace", f"{purpose}：{count} 次 / {ok} 成功")

    print(f"OK project={project_id} round={round_id}", flush=True)
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=None, help="有功能子树的项目 id（新开一轮到草案卡）")
    parser.add_argument("--resume", default=None, help="已有草案轮 id（跳过起轮）")
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
