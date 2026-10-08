"""讨论区 live 验收（M9）：在**真模型**上把一条灵感线程跑完，不起轮。

只验模型相关的那段未知：两段提示词能渲染 → 网关能调通 → 报文能过三道校验 →
卡片 id 由服务端盖、grounding 规则守住 → 收下为材料 → 留样按线程 id 查得到。

不起轮（起轮烧席位调用，而"材料进轮后成 `SIMULATED` 编号"由假模型 e2e 钉着：
`tests/e2e/test_discussion_loop.py`）。

跑法（先备份，不污染真库；留样打开，好数调用次数）：

```bash
sqlite3 backend/data/pmstudio.db ".backup /tmp/m9-live.db"
cd backend
set -a && . ./.env && set +a
PMSTUDIO_DB=/tmp/m9-live.db PMSTUDIO_TRACE_MODEL=1 python3 scripts/discussion_smoke.py <project_id>
rm -f /tmp/m9-live.db
```

`--turns 6` 跑满 12 条并探一次上限（第 7 说必须 409，线程原样）——M13 补 03 的 ⬜ 用。

退出码非 0 表示失败，失败时打印**哪一步**、**什么原因**。
"""

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pmstudio.bootstrap.container import build_container  # noqa: E402
from pmstudio.bootstrap.settings import Settings  # noqa: E402

SAY_ROUNDS = 3

#: 满轮验收目标：后端 `domain/discussion/thread.py` 的 `MAX_DISCUSSION_TURNS`。
#: 只在这里当"跑到它就开始探上限"的靶子；值本身由后端用 T4 拒回时说话（不抄判据）。
FULL_TURNS = 12


def show(step: str, detail: str) -> None:
    print(f"[{step}] {detail}", flush=True)


async def run(client: httpx.AsyncClient, project_id: str, say_rounds: int) -> int:
    schema = await client.get("/api/schema")
    roles = schema.json()["discussion_roles"]
    ids = "、".join(role["role_id"] for role in roles)
    show("schema", f"角色表随 /schema 下来 {len(roles)} 个：{ids}")

    workspace = await client.get(f"/api/projects/{project_id}/workspace")
    if workspace.status_code != 200:
        show("workspace", f"读工作区失败 {workspace.status_code}")
        return 1
    document = workspace.json()["document"]
    filled = [
        section["section_id"]
        for section in document["sections"]
        if any(block["entries"] for block in section["blocks"])
    ]
    if not filled:
        show("workspace", "文档是空的：说开轮要挂依据，先跑一轮再来")
        return 1
    show("workspace", f"有内容的章节：{'、'.join(filled)}")

    started = time.monotonic()
    opened = await client.post(
        f"/api/projects/{project_id}/discussions",
        json={
            "topic": "导出除了按时间筛，还能有什么筛法",
            "role_id": "dlg.challenger",
        },
    )
    if opened.status_code != 200:
        show("start", f"失败 {opened.status_code} {opened.text[:300]}")
        return 1
    thread = opened.json()
    did = thread["thread_id"]
    show("start", f"{did} 开聊（对方：{thread['role_title']}，轮上限 12）")

    for index in range(1, say_rounds + 1):
        said = await client.post(
            f"/api/discussions/{did}/say",
            json={"text": f"我觉得按客户名筛最直观（第 {index} 次说）"},
        )
        if said.status_code != 200:
            show(f"say{index}", f"失败 {said.status_code} {said.text[:300]}")
            return 1
        thread = said.json()
        turn = thread["turns"][-1]
        if turn["role"] != "assistant":
            show(f"say{index}", "最后一轮不是助手回话——线程顺序坏了")
            return 1
        refs = turn["based_on"]
        show(
            f"say{index}",
            f"{turn['text'][:90]}…（依据 {refs or '（无，凭空推演）'}）",
        )
        for ref in refs:
            lookup = await client.get(f"/api/projects/{project_id}/evidence/{ref}")
            if lookup.status_code != 200:
                show(f"say{index}", f"引用的 {ref} 在账本里查不到 {lookup.status_code}")
                return 1
    turns = len(thread["turns"])
    if turns != say_rounds * 2:
        show("say", f"说 {say_rounds} 轮后 turns 应是 {say_rounds * 2}，实际 {turns}")
        return 1
    show("say", f"说了 {say_rounds} 轮 = {turns} 条 turn（user/assistant 成对交替）")

    if turns >= FULL_TURNS:
        # 满轮探测（M13 补 03 的 ⬜）：再说一次必须被 T4 拒，且线程原样。
        capped = await client.post(
            f"/api/discussions/{did}/say", json={"text": "满了还硬说一句"}
        )
        if capped.status_code != 409:
            show("cap", f"满轮后再说应是 409，实际 {capped.status_code} {capped.text[:200]}")
            return 1
        code = capped.json().get("code", "")
        after = await client.get(f"/api/projects/{project_id}/discussions")
        kept = next((t for t in after.json() if t["thread_id"] == did), None)
        after_turns = None if kept is None else len(kept["turns"])
        if not code.startswith("invariant:") or after_turns != turns:
            show("cap", f"满轮拒绝的形状不对：code={code}，turns={after_turns}")
            return 1
        show("cap", f"满轮后再说被拒：{code}（线程仍是 {turns} 条 turn，原样）")

    assembled = await client.post(f"/api/discussions/{did}/cards")
    if assembled.status_code != 200:
        show("cards", f"失败 {assembled.status_code} {assembled.text[:300]}")
        return 1
    thread = assembled.json()
    cards = thread["cards"]
    if not cards:
        show("cards", "一张卡都没有：空结果不算成功")
        return 1
    stamped = "、".join(card["card_id"] for card in cards)
    show("cards", f"总结出 {len(cards)} 张卡（服务端盖的 id：{stamped}）")
    for card in cards:
        if card["grounding"] == "document" and not card["based_on"]:
            show("cards", f"{card['card_id']} 标 document 却没带依据编号")
            return 1
        if card["grounding"] == "invented" and card["based_on"]:
            show("cards", f"{card['card_id']} 标 invented 却带了依据编号")
            return 1
        show("cards", f"  · {card['card_id']} [{card['grounding']}] {card['statement'][:70]}")

    kept = await client.post(
        f"/api/discussions/{did}/cards/{cards[0]['card_id']}/keep",
        json={"as": "material"},
    )
    if kept.status_code != 200:
        show("keep", f"失败 {kept.status_code} {kept.text[:300]}")
        return 1
    ref = next(
        c["kept_ref"] for c in kept.json()["cards"] if c["card_id"] == cards[0]["card_id"]
    )
    if not ref:
        show("keep", "收下了但没落材料 id")
        return 1
    show("keep", f"{cards[0]['card_id']} 落成材料 {ref}")

    materials = await client.get(f"/api/projects/{project_id}/attachments")
    hit = next((m for m in materials.json() if m["material_id"] == ref), None)
    if hit is None:
        show("keep", f"材料库里找不到 {ref}")
        return 1
    show("keep", f"材料库可见：origin={hit['origin']}（界面按原值显示，标签不加）")

    listed = await client.get(f"/api/projects/{project_id}/discussions")
    if listed.status_code != 200 or did not in [t["thread_id"] for t in listed.json()]:
        show("list", "列表里找不到刚开的那条线程")
        return 1
    show("list", "过目列表读回正常")

    if os.environ.get("PMSTUDIO_TRACE_MODEL") == "1":
        import sqlite3

        db = sqlite3.connect(str(Settings.from_env().db_path))
        rows = db.execute(
            "SELECT purpose, count(*), sum(ok) FROM model_calls WHERE round_id = ?"
            " GROUP BY purpose ORDER BY purpose",
            (did,),
        ).fetchall()
        for purpose, count, ok in rows:
            show("trace", f"{purpose}：{count} 次尝试 / {ok} 次成功 → 留样按 {did} 查得到")
        if not rows:
            show("trace", f"留样里查不到 {did} 的任何一行（中立 trace_id 没生效？）")
            return 1

    elapsed = time.monotonic() - started
    print(
        f"OK thread={did} turns={turns} cards={len(cards)} kept={ref} "
        f"elapsed={elapsed:.1f}s",
        flush=True,
    )
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id")
    parser.add_argument(
        "--turns",
        type=int,
        default=SAY_ROUNDS,
        help=f"说几轮（一到一对；6 = 跑满 {FULL_TURNS} 条并探上限）",
    )
    args = parser.parse_args()
    settings = Settings.from_env()
    os.environ.setdefault("PMSTUDIO_DB", str(settings.db_path))
    show("boot", f"模型 {settings.model}；库 {settings.db_path}")
    app = build_container(settings).app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://smoke", timeout=300.0
    ) as client:
        return await run(client, args.project_id, args.turns)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
