"""真模型冒烟：不打开浏览器，把主闭环在**真 LiteLLM**上跑一遍。

它不是测试（不进 CI），是给作者一条"链路通不通"的命令：

```bash
cd backend
PMSTUDIO_MODEL=openai/qwen3.8-flash \
PMSTUDIO_API_KEY=sk-… \
PMSTUDIO_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1 \
PMSTUDIO_DB=/tmp/pmstudio-smoke.db \
python3 scripts/smoke.py
```

退出码非 0 表示失败，失败时打印**哪一步**、**什么原因**。
"""

import asyncio
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pmstudio.bootstrap.container import build_container  # noqa: E402
from pmstudio.bootstrap.settings import Settings  # noqa: E402

INPUT = (
    "想做个订单导出的功能，运营每天都要用，最好能按时间筛选，数据量大概几万。"
    "另外登录页太丑了，顺便改一下，支持微信登录。月底必须上线。"
)


def show(step: str, detail: str) -> None:
    print(f"[{step}] {detail}", flush=True)


async def run(client: httpx.AsyncClient) -> int:
    created = await client.post("/api/projects", json={"title": "订单中台（冒烟）"})
    if created.status_code != 201:
        show("project", f"建项目失败 {created.status_code} {created.text[:200]}")
        return 1
    project_id = created.json()["project_id"]
    show("project", f"{project_id} 已建（13 章空骨架）")

    started = await client.post(
        f"/api/projects/{project_id}/rounds", json={"input": INPUT}
    )
    if started.status_code != 200:
        show("claims", f"失败 {started.status_code} {started.text[:300]}")
        return 1
    round_body = started.json()
    round_id = round_body["round_id"]
    for claim in round_body["claims"]:
        show("claim", f"{claim['eid']} {claim['label']} → {claim['target_label']}：{claim['text']}")
    show("claims", f"共 {len(round_body['claims'])} 条，waiting_on={round_body['waiting_on']}")

    seats = [seat["seat_id"] for seat in round_body["nominated_seats"]]
    show("seats", "提名：" + "、".join(seats))
    opened = await client.post(
        f"/api/rounds/{round_id}/opening", json={"seats": seats, "claim_edits": []}
    )
    if opened.status_code != 200:
        show("seat_round", f"失败 {opened.status_code} {opened.text[:300]}")
        return 1
    body = opened.json()
    for packet in body["seat_packets"]:
        show(
            "seat",
            f"{packet['seat_id']} 判断 {packet['judgment_count']} 条 / 疑问 "
            f"{packet['question_count']} 个；依据：{packet['basis'][:40]}",
        )
    show("integrate", f"waiting_on={body['waiting_on']}，题目 {len(body['questions'])} 道")
    if body.get("dropped_attributions"):
        show("attribution", f"归因没对上号：{'、'.join(body['dropped_attributions'])}")

    # 一轮里可能问不止一次（最多 3 次席位轮）：一直答到出草案卡为止
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
                f"{question['kind']} {question['title']} → 选 "
                f"{options[0][1] if options else '（开放题）'}"
                f"〔依据 {question['eids'] or '（无）'} · 谁问的 "
                f"{question['asked_by'] or '（没认出来）'}〕",
            )
        answered = await client.post(
            f"/api/rounds/{round_id}/answers",
            json={"form_id": body["form_id"], "answers": answers, "skipped": []},
        )
        if answered.status_code != 200:
            show("answers", f"失败 {answered.status_code} {answered.text[:300]}")
            return 1
        body = answered.json()
        show(
            "answers",
            f"答完 → waiting_on={body['waiting_on']}，席位轮 {body['seat_round_count']}",
        )

    if body["waiting_on"] != "draft_card" or body["draft"] is None:
        show(
            "draft",
            f"没等到草案卡：waiting_on={body['waiting_on']}，{body.get('failure_reason')}",
        )
        return 1
    for title, proposals in body["draft"]["groups"]:
        show("draft", f"{title}：{len(proposals)} 条")
        for proposal in proposals[:3]:
            show("  ·", f"{proposal['target_label']} {proposal['text'][:60]} {proposal['eids']}")
    if body["draft"]["gaps"]:
        show("gaps", "；".join(body["draft"]["gaps"][:3]))
    if body["advisories"]:
        show("advisories", "；".join(body["advisories"][:3]))

    accepted = [
        proposal["proposal_id"]
        for proposal in body["draft"]["proposals"]
        if proposal["origin_type"] != "proposed"
    ]
    confirmed = await client.post(
        f"/api/rounds/{round_id}/confirm", json={"accepted": accepted}
    )
    if confirmed.status_code != 200:
        show("confirm", f"失败 {confirmed.status_code} {confirmed.text[:300]}")
        return 1
    version = confirmed.json()["version_no"]
    workspace = (await client.get(f"/api/projects/{project_id}/workspace")).json()
    filled = [
        section["section_id"]
        for section in workspace["document"]["sections"]
        if section["blocks"]
    ]
    show("confirm", f"写入成功，版本 v{version}；有内容的章节：{'、'.join(filled)}")
    show("preference", f"偏好区 {len(workspace['document']['preference_entries'])} 条")
    print(f"OK version={version} round={round_id} project={project_id}", flush=True)
    return 0


async def main() -> int:
    settings = Settings.from_env()
    os.environ.setdefault("PMSTUDIO_DB", str(settings.db_path))
    show("boot", f"模型 {settings.model}；库 {settings.db_path}")
    app = build_container(settings).app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://smoke") as client:
        health = await client.get("/api/health")
        show("health", str(health.json()))
        return await run(client)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
