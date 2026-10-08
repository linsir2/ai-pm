"""开题调研 live 验收（M13）：先跑一轮调研 → 假设当开题底稿带进讨论。

只验模型相关的那段未知：探索出候选 → 起线程带 `finding_exploration_id` → 底稿快照进线程 →
说一轮时**候选原文真的进了提示词**（留样可查）。不起轮、不写文档。

跑法（先备份，不污染真库；留样打开，好查提示词）：

```bash
sqlite3 backend/data/pmstudio.db ".backup /tmp/m13-findings.db"
cd backend
set -a && . ./.env && set +a
PMSTUDIO_DB=/tmp/m13-findings.db PMSTUDIO_TRACE_MODEL=1 \
  python3 scripts/discussion_findings_smoke.py <project_id>
rm -f /tmp/m13-findings.db
```

退出码非 0 表示失败，失败时打印**哪一步**、**什么原因**。
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

TOPIC = "导出要不要按客户名筛"


def show(step: str, detail: str) -> None:
    print(f"[{step}] {detail}", flush=True)


def _thread_findings(db_path: str, discussion_id: str) -> list[dict]:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = db.execute(
            "SELECT payload FROM discussions WHERE discussion_id = ?", (discussion_id,)
        ).fetchone()
    finally:
        db.close()
    if row is None:
        return []
    return json.loads(row[0]).get("findings") or []


def _say_prompt_hits(db_path: str, discussion_id: str, needle: str) -> int:
    """留样里说这一轮的提示词包含底稿原文的行数（>=1 才算真的进了提示词）。"""

    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = db.execute(
            "SELECT count(*) FROM model_calls WHERE purpose = 'discussion' AND"
            " prompt_text LIKE ?",
            (f"%{needle}%",),
        ).fetchone()
    finally:
        db.close()
    return int(row[0]) if row else 0


async def run(client: httpx.AsyncClient, project_id: str, db_path: str) -> int:
    started = time.monotonic()
    explored = await client.post(
        f"/api/projects/{project_id}/explorations",
        json={"kind": "inquiry", "scope": {"kind": "document", "text": TOPIC}},
    )
    if explored.status_code != 200:
        show("explore", f"失败 {explored.status_code} {explored.text[:300]}")
        return 1
    exploration = explored.json()
    xid = exploration["exploration_id"]
    candidates = exploration["candidates"]
    if exploration["status"] != "done" or not candidates:
        show("explore", f"没跑成/没候选：status={exploration['status']} 候选={len(candidates)}")
        return 1
    show("explore", f"{xid} 跑完：{len(candidates)} 条候选（焦点：{TOPIC}）")

    opened = await client.post(
        f"/api/projects/{project_id}/discussions",
        json={
            "topic": TOPIC,
            "role_id": "dlg.challenger",
            "finding_exploration_id": xid,
        },
    )
    if opened.status_code != 200:
        show("start", f"失败 {opened.status_code} {opened.text[:300]}")
        return 1
    thread = opened.json()
    did = thread["thread_id"]
    if thread["findings_count"] != len(candidates):
        show("start", f"findings_count={thread['findings_count']} 与候选数 {len(candidates)} 不符")
        return 1
    show("start", f"{did} 开聊，底稿 {thread['findings_count']} 条")

    findings = _thread_findings(db_path, did)
    if len(findings) != len(candidates):
        show("store", f"库里的 findings 不对：{findings}")
        return 1
    show("store", f"round-trip：findings 落库 {len(findings)} 条（只引用）")

    said = await client.post(
        f"/api/discussions/{did}/say", json={"text": "先说说这些假设靠不靠谱"}
    )
    if said.status_code != 200:
        show("say", f"失败 {said.status_code} {said.text[:300]}")
        return 1
    turn = said.json()["turns"][-1]
    show("say", f"{turn['text'][:90]}…（依据 {turn['based_on'] or '（无）'}）")

    needle = candidates[0]["statement"][:18]
    hits = _say_prompt_hits(db_path, did, needle)
    if hits < 1:
        show("prompt", f"留样里说这一轮的提示词查不到底稿原文（{needle!r}）——没真的进提示词")
        return 1
    show("prompt", f"提示词命中底稿原文：{needle!r}（{hits} 行留样）")

    elapsed = time.monotonic() - started
    print(
        f"OK exploration={xid} thread={did} findings={len(findings)} "
        f"candidates={len(candidates)} elapsed={elapsed:.1f}s",
        flush=True,
    )
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id")
    args = parser.parse_args()
    settings = Settings.from_env()
    os.environ.setdefault("PMSTUDIO_DB", str(settings.db_path))
    show("boot", f"模型 {settings.model}；库 {settings.db_path}")
    app = build_container(settings).app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://smoke", timeout=300.0
    ) as client:
        return await run(client, args.project_id, str(settings.db_path))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
