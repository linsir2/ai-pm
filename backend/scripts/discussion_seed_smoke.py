"""灵感箱 live 验收（M12）：讨论卡收成种子 → **真模型**起轮 → 种子落到轮上。

只验模型相关的那段未知：卡能收成 seed（不落材料）→ 起轮请求带统一格式的种子 id
（`discussion:{线程}#{卡}`）→ 后端解析 → `Round.seeds` 落 `origin=discussion` 的回链。

跑法（先备份，不污染真库；留样打开，好数调用次数）：

```bash
sqlite3 backend/data/pmstudio.db ".backup /tmp/m12-live.db"
cd backend
set -a && . ./.env && set +a
PMSTUDIO_DB=/tmp/m12-live.db PMSTUDIO_TRACE_MODEL=1 \
  python3 scripts/discussion_seed_smoke.py <project_id>
rm -f /tmp/m12-live.db
```

退出码非 0 表示失败，失败时打印**哪一步**、**什么原因**。
"""

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

SAY_ROUNDS = 2


def show(step: str, detail: str) -> None:
    print(f"[{step}] {detail}", flush=True)


def _round_seeds(db_path: str, round_id: str) -> list[dict]:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = db.execute(
            "SELECT payload FROM rounds WHERE round_id = ?", (round_id,)
        ).fetchone()
    finally:
        db.close()
    if row is None:
        return []
    return json.loads(row[0]).get("seeds") or []


async def run(client: httpx.AsyncClient, project_id: str, db_path: str) -> int:
    workspace = await client.get(f"/api/projects/{project_id}/workspace")
    if workspace.status_code != 200:
        show("workspace", f"读工作区失败 {workspace.status_code}")
        return 1

    started = time.monotonic()
    opened = await client.post(
        f"/api/projects/{project_id}/discussions",
        json={
            "topic": "导出功能还缺什么：按客户名筛一次",
            "role_id": "dlg.challenger",
        },
    )
    if opened.status_code != 200:
        show("start", f"失败 {opened.status_code} {opened.text[:300]}")
        return 1
    did = opened.json()["thread_id"]
    show("start", f"{did} 开聊（对方：{opened.json()['role_title']}）")

    for index in range(1, SAY_ROUNDS + 1):
        said = await client.post(
            f"/api/discussions/{did}/say",
            json={"text": f"我觉得按客户名筛最直观（第 {index} 次说）"},
        )
        if said.status_code != 200:
            show(f"say{index}", f"失败 {said.status_code} {said.text[:300]}")
            return 1
        turn = said.json()["turns"][-1]
        show(f"say{index}", f"{turn['text'][:90]}…（依据 {turn['based_on'] or '（无）'}）")

    assembled = await client.post(f"/api/discussions/{did}/cards")
    if assembled.status_code != 200:
        show("cards", f"失败 {assembled.status_code} {assembled.text[:300]}")
        return 1
    cards = assembled.json()["cards"]
    if not cards:
        show("cards", "一张卡都没有：空结果不算成功")
        return 1
    card_id = cards[0]["card_id"]
    show("cards", f"总结出 {len(cards)} 张卡，拿 {card_id} 当种子")

    seeded = await client.post(
        f"/api/discussions/{did}/cards/{card_id}/keep", json={"as": "seed"}
    )
    if seeded.status_code != 200:
        show("keep-seed", f"失败 {seeded.status_code} {seeded.text[:300]}")
        return 1
    card = next(c for c in seeded.json()["cards"] if c["card_id"] == card_id)
    if (card["state"], card["kept_as"], card["kept_ref"]) != ("kept", "seed", None):
        show("keep-seed", f"收种子后的卡片形状不对：{card}")
        return 1
    materials = await client.get(f"/api/projects/{project_id}/attachments")
    if any(m["material_id"] == card["kept_ref"] for m in materials.json()):
        show("keep-seed", "收种子不该落材料，却落了一份")
        return 1
    show("keep-seed", f"{card_id} 已收下为下一轮输入（未落材料）")

    seed_id = f"discussion:{did}#{card_id}"
    round_started = time.monotonic()
    response = await client.post(
        f"/api/projects/{project_id}/rounds",
        json={
            "input": "按讨论里说的：导出先加按客户名筛，按这个更新功能描述",
            "material_ids": [],
            "seed_ids": [seed_id],
        },
    )
    if response.status_code != 200:
        show("round", f"失败 {response.status_code} {response.text[:300]}")
        return 1
    round_id = response.json()["round_id"]
    show("round", f"{round_id} 起轮成功（{time.monotonic() - round_started:.1f}s）")

    seeds = _round_seeds(db_path, round_id)
    want = {"origin": "discussion", "ref_id": did, "item_id": card_id}
    if seeds != [want]:
        show("round", f"轮上的种子不对：{seeds}（应为 [{want}]）")
        return 1
    show("round", f"round.seeds = {seeds}（回链落库）")

    if os.environ.get("PMSTUDIO_TRACE_MODEL") == "1":
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT purpose, count(*), sum(ok) FROM model_calls WHERE round_id = ?"
            " GROUP BY purpose ORDER BY purpose",
            (round_id,),
        ).fetchall()
        db.close()
        for purpose, count, ok in rows:
            show("trace", f"{purpose}：{count} 次尝试 / {ok} 次成功 → 留样按 {round_id} 查得到")
        if not rows:
            show("trace", f"留样里查不到 {round_id} 的任何一行（中立 trace_id 没生效？）")
            return 1

    elapsed = time.monotonic() - started
    print(
        f"OK thread={did} card={card_id} round={round_id} seed={seed_id} "
        f"elapsed={elapsed:.1f}s",
        flush=True,
    )
    return 0


async def main() -> int:
    if len(sys.argv) < 2:
        print("用法：discussion_seed_smoke.py <project_id>", flush=True)
        return 2
    settings = Settings.from_env()
    os.environ.setdefault("PMSTUDIO_DB", str(settings.db_path))
    show("boot", f"模型 {settings.model}；库 {settings.db_path}")
    app = build_container(settings).app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://smoke", timeout=300.0
    ) as client:
        return await run(client, sys.argv[1], str(settings.db_path))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
