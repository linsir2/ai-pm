"""探索冒烟（M8.1）：在**真模型**上把调研闭环跑一遍，但不起轮（起轮烧席位调用）。

只验模型相关的那段未知：提示词能渲染 → 网关能调通 → 报文能过三道校验 →
候选能收下（种子/材料各一条）→ based_on 编号能在账本里查到。
种子→起轮的 HTTP 缝由 `tests/api/test_round_seed_ids_api.py` 钉死（假模型全栈），
这里不再花真调用重验。

用法（先备份再跑，不污染真库）：

```bash
sqlite3 backend/data/pmstudio.db ".backup /tmp/m81-live-copy.db"
cd backend
PMSTUDIO_DB=/tmp/m81-live-copy.db python3 scripts/exploration_smoke.py <project_id>
rm -f /tmp/m81-live-copy.db
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


def show(step: str, detail: str) -> None:
    print(f"[{step}] {detail}", flush=True)


async def run(client: httpx.AsyncClient, project_id: str) -> int:
    workspace = await client.get(f"/api/projects/{project_id}/workspace")
    if workspace.status_code != 200:
        show("workspace", f"读工作区失败 {workspace.status_code} {workspace.text[:200]}")
        return 1
    sections = workspace.json()["document"]["sections"]
    filled = [
        section["section_id"]
        for section in sections
        if any(block["entries"] for block in section["blocks"])
    ]
    if not filled:
        show("workspace", "文档是空的：调研没有锚点，先跑一轮再来")
        return 1
    show("workspace", f"有内容的章节：{'、'.join(filled)}")

    started = await client.post(
        f"/api/projects/{project_id}/explorations",
        json={"kind": "inquiry", "scope": {"kind": "document"}},
    )
    if started.status_code != 200:
        show("inquiry", f"失败 {started.status_code} {started.text[:300]}")
        return 1
    body = started.json()
    if body["status"] != "done":
        show("inquiry", f"没跑完：status={body['status']}，原因={body['failure_reason']}")
        return 1
    xid = body["exploration_id"]
    hyps = [c for c in body["candidates"] if c["kind"] == "hypothesis"]
    spots = [c for c in body["candidates"] if c["kind"] == "blind_spot"]
    show("inquiry", f"{xid} done：假设 {len(hyps)} 条 / 盲区 {len(spots)} 条")
    if not hyps:
        show("inquiry", "一条假设都没有：空结果不算成功")
        return 1
    first = hyps[0]
    if not first["falsification"] or not first["evidence_needed"]:
        show("inquiry", "假设缺证伪两行：领域校验漏过去了")
        return 1
    for ref in first["based_on"]:
        lookup = await client.get(f"/api/projects/{project_id}/evidence/{ref}")
        if lookup.status_code != 200:
            show("grounding", f"{ref} 在账本里查不到 {lookup.status_code}")
            return 1
    basis = first["based_on"] or ["（凭空推演，无据）"]
    show("grounding", f"{first['candidate_id']} 的 based_on 全落在账本里：{basis}")

    keep_seed = await client.post(
        f"/api/explorations/{xid}/candidates/{first['candidate_id']}/keep",
        json={"as": "seed"},
    )
    if keep_seed.status_code != 200:
        show("keep-seed", f"失败 {keep_seed.status_code} {keep_seed.text[:200]}")
        return 1
    show("keep-seed", f"{first['candidate_id']} 已收下为下一轮输入")

    target = spots[0] if spots else (hyps[1] if len(hyps) > 1 else None)
    if target is None:
        show("keep-material", "只有一条候选，跳过材料路（种子路已验）")
    else:
        keep_mat = await client.post(
            f"/api/explorations/{xid}/candidates/{target['candidate_id']}/keep",
            json={"as": "material"},
        )
        if keep_mat.status_code != 200:
            show("keep-material", f"失败 {keep_mat.status_code} {keep_mat.text[:200]}")
            return 1
        ref = next(
            c["kept_ref"]
            for c in keep_mat.json()["candidates"]
            if c["candidate_id"] == target["candidate_id"]
        )
        if not ref:
            show("keep-material", "收下了但没落材料 id")
            return 1
        show("keep-material", f"{target['candidate_id']} 落成材料 {ref}")

    listed = await client.get(f"/api/projects/{project_id}/explorations")
    if listed.status_code != 200 or xid not in [
        item["exploration_id"] for item in listed.json()
    ]:
        show("list", "列表里找不到刚跑完的这一趟")
        return 1
    show("list", "过目台列表读回正常")

    print(f"OK exploration={xid} hyps={len(hyps)} spots={len(spots)}", flush=True)
    return 0


async def main() -> int:
    if len(sys.argv) < 2:
        print("用法：exploration_smoke.py <project_id>", flush=True)
        return 2
    settings = Settings.from_env()
    os.environ.setdefault("PMSTUDIO_DB", str(settings.db_path))
    show("boot", f"模型 {settings.model}；库 {settings.db_path}")
    app = build_container(settings).app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://smoke", timeout=300.0
    ) as client:
        return await run(client, sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
