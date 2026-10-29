"""P3b-1 真跑冒烟：真模型 + 真 Tavily + 真 PostgreSQL，走生产 HTTP 路径。

它验的是**项目级研究**这一条：

1. `POST /projects/{id}/research` 在请求内跑完（工具循环 → 综合 → 一个 UoW 落库）；
2. 一条来源一个 E 号，编号能按 `/evidence/{eid}` 查回原话（= 来源卡）；
3. 来源材料 `origin=web_research`、块数 > 0，且**不被检索召回**（`/search` 里没有它）；
4. 把来源卡挂到当前轮：`POST /rounds/{id}/research-sources`，轮次 payload 里
   出现快照（`research_sources`），且**没有**写进 `materials`（不进材料池）。

**不伪造成功**：模型 / Tavily 失败、零来源、挂载失败、检索里召回研究来源，
都写进证据并返回非 0。

跑法（从 `backend/`；配置读 `backend/.env`，不会打印 key）：

    cd backend
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/p3b_research_smoke.py

证据落到 `.codex/runs/2026-10-29/p3b/`（gitignored）：`report.json` + `summary.md`。
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import os
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT / "src"))
sys.path.insert(0, str(BACKEND_ROOT))

from scripts.pg_scratch import drop_scratch, make_scratch  # noqa: E402

from pmstudio.bootstrap.container import build_container  # noqa: E402
from pmstudio.bootstrap.settings import Settings  # noqa: E402

ENV_FILE = BACKEND_ROOT / ".env"
ENV_OPS_FILE = BACKEND_ROOT / ".env.ops"
DEFAULT_OUT = REPO_ROOT / ".codex" / "runs" / "2026-10-29" / "p3b"
SECRET_VALUES: list[str] = []

TOPIC = "同类产品（项目管理 / 工单系统）的批量导出：常见的数据量上限与实现方式"


def load_env_file(path: Path) -> dict[str, str]:
    """极简 `.env` 读取（与 `scripts/dev.py` 同语义），不打印值。"""

    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line == "" or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _redact(value):  # type: ignore[no-untyped-def]
    if isinstance(value, str):
        for secret in SECRET_VALUES:
            value = value.replace(secret, "<redacted>")
        return value
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def save(report: dict, out: Path) -> None:
    report = _redact(report)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# P3b-1 研究真跑冒烟",
        "",
        f"- 时间：{report.get('started_at', '')}",
        f"- 模型：`{report.get('model', '')}`",
        f"- Tavily 配置：{'是' if report.get('tavily_configured') else '否'}",
        f"- PostgreSQL schema：`{report.get('db_schema', '')}`",
        f"- 退出码：{report.get('exit_code', '（运行中）')}",
        f"- 耗时：{report.get('elapsed_seconds', '?')} 秒",
        "",
        "## 研究",
        "",
        f"- 项目 / 探索：`{report.get('project_id')}` / `{report.get('exploration_id')}`",
        f"- 状态：{report.get('status')}；失败原因：{report.get('failure_reason')}",
        f"- 来源 {report.get('source_count')} 条；结论 {report.get('finding_count')} 条；"
        f"开放问题 {len(report.get('open_questions') or ())} 个",
        f"- 一条来源一个 E 号：{report.get('one_source_one_eid')}",
        f"- E 号可查：{report.get('evidence_resolvable')}",
        f"- 来源材料：{report.get('db_source_materials')} 份 / "
        f"{report.get('db_source_chunks')} 块",
        "- 全局搜索能搜到研究来源："
        f"{report.get('global_search_finds_research_source')}（应为 True）",
        "- 建轮检索召回了研究来源："
        f"{report.get('rag_includes_research_source')}（应为 False）",
        "",
        "## 挂到当前轮",
        "",
        f"- 轮次：`{report.get('round_id')}`",
        f"- 挂载状态码：{report.get('attach_status_code')}",
        f"- 轮上来源卡：{report.get('round_research_sources')}",
        f"- 轮上没有材料块（应为 0）：{report.get('round_materials_after_attach')}",
    ]
    for source in report.get("sources") or ():
        lines.append(
            f"- `{source.get('eid')}` {source.get('title')} — {source.get('url')}"
        )
    if report.get("error"):
        lines += ["", "## 失败原文摘要", "", f"```text\n{report['error']}\n```"]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def log(report: dict, out: Path, step: str, detail: str) -> None:
    print(f"[{step}] {detail}", flush=True)
    report.setdefault("steps", []).append({"step": step, "detail": detail})
    save(report, out)


def db_evidence(db_url: str, project_id: str, round_id: str | None) -> dict:
    from pmstudio.infrastructure.postgres.database import connect

    with connect(db_url) as connection:
        materials = connection.execute(
            "SELECT count(*) AS n FROM materials"
            " WHERE project_id = %s AND origin = 'web_research'",
            (project_id,),
        ).fetchone()["n"]
        chunks = connection.execute(
            """
            SELECT count(*) AS n FROM material_chunks c
            JOIN materials m ON m.material_id = c.material_id
            WHERE m.project_id = %s AND m.origin = 'web_research'
            """,
            (project_id,),
        ).fetchone()["n"]
        ids = [
            str(row["material_id"])
            for row in connection.execute(
                "SELECT material_id FROM materials"
                " WHERE project_id = %s AND origin = 'web_research'",
                (project_id,),
            ).fetchall()
        ]
        result: dict = {
            "db_source_materials": int(materials),
            "db_source_chunks": int(chunks),
            "research_material_ids": ids,
        }
        if round_id is not None:
            row = connection.execute(
                "SELECT payload FROM rounds WHERE round_id = %s", (round_id,)
            ).fetchone()
            payload = row["payload"] if row is not None else {}
            result["round_payload_research_sources"] = payload.get("research_sources") or []
            result["round_payload_materials"] = payload.get("materials") or []
        return result


async def run(report: dict, out: Path) -> int:  # noqa: C901 —— 证据脚本按步骤线性写
    env = {**load_env_file(ENV_FILE), **load_env_file(ENV_OPS_FILE)}
    # 已导出的环境变量优先（与 `scripts/pg_scratch.load_env_files` 同一条语义）：
    # 本机跑时用它把库指到本地容器，CI 里由 workflow 注入。
    env.update({key: value for key, value in os.environ.items() if key.startswith("PMSTUDIO_")})
    if not env.get("PMSTUDIO_API_KEY"):
        report["error"] = "backend/.env 缺 PMSTUDIO_API_KEY"
        return 2
    if not env.get("PMSTUDIO_TAVILY_API_KEY"):
        report["error"] = "backend/.env 缺 PMSTUDIO_TAVILY_API_KEY"
        return 2
    if not (env.get("PMSTUDIO_FIRST_ADMIN_USER") and env.get("PMSTUDIO_FIRST_ADMIN_PASSWORD")):
        report["error"] = "backend/.env 缺首管理员凭据"
        return 2

    out.mkdir(parents=True, exist_ok=True)
    database = make_scratch(
        prefix="p3b",
        env=env,
        admin_user=env.get("PMSTUDIO_FIRST_ADMIN_USER"),
        admin_password=env.get("PMSTUDIO_FIRST_ADMIN_PASSWORD"),
    )
    atexit.register(drop_scratch, database)

    run_env = dict(env)
    run_env["PMSTUDIO_DATABASE_URL"] = database.app_url
    run_env.pop("PMSTUDIO_TRACE_MODEL", None)
    for key, value in run_env.items():
        os.environ[key] = value
    SECRET_VALUES.clear()
    SECRET_VALUES.extend(
        value
        for key, value in env.items()
        if any(marker in key for marker in ("KEY", "SECRET", "PASSWORD", "TOKEN"))
        and len(value) >= 4
    )

    import httpx

    settings = replace(Settings.from_env(run_env), database_url=database.app_url)
    report.update(
        {
            "model": settings.model,
            "tavily_configured": settings.tavily_api_key is not None,
            "db_schema": database.schema,
            "started_at": datetime.now(UTC).isoformat(),
            "topic": TOPIC,
        }
    )
    log(report, out, "config", f"模型 {settings.model}；Tavily 已配置；schema {database.schema}")

    container = build_container(settings)
    transport = httpx.ASGITransport(app=container.app)
    timeout = httpx.Timeout(1800.0, connect=10.0)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://p3b-smoke", timeout=timeout
    ) as client:
        log(report, out, "health", str((await client.get("/api/health")).json()))
        login = await client.post(
            "/api/auth/login",
            json={
                "username": env["PMSTUDIO_FIRST_ADMIN_USER"],
                "password": env["PMSTUDIO_FIRST_ADMIN_PASSWORD"],
            },
        )
        if login.status_code != 200:
            report["error"] = f"登录失败 {login.status_code} {login.text[:200]}"
            return 1
        log(report, out, "login", "已登录首管理员")

        limits = (await client.get("/api/schema")).json().get("limits") or {}
        report["schema_limits"] = {
            "max_research_topic_chars": limits.get("max_research_topic_chars"),
            "max_research_sources": limits.get("max_research_sources"),
        }

        created = await client.post("/api/projects", json={"title": "P3b-1 研究冒烟"})
        if created.status_code != 201:
            report["error"] = f"建项目失败 {created.status_code} {created.text[:300]}"
            return 1
        project_id = created.json()["project_id"]
        report["project_id"] = project_id
        log(report, out, "project", project_id)

        started = time.monotonic()
        response = await client.post(
            f"/api/projects/{project_id}/research", json={"topic": TOPIC}
        )
        elapsed = round(time.monotonic() - started, 1)
        if response.status_code != 200:
            report["error"] = f"研究请求失败 {response.status_code} {response.text[:500]}"
            return 1
        view = response.json()
        report.update(
            {
                "exploration_id": view.get("exploration_id"),
                "status": view.get("status"),
                "failure_reason": view.get("failure_reason"),
                "research_seconds": elapsed,
            }
        )
        research = view.get("research") or {}
        sources = research.get("sources") or []
        findings = research.get("findings") or []
        report.update(
            {
                "source_count": len(sources),
                "finding_count": len(findings),
                "open_questions": research.get("open_questions") or [],
                "sources": sources,
            }
        )
        log(
            report,
            out,
            "research",
            f"status={view.get('status')} 来源 {len(sources)} 条 / 结论 {len(findings)} 条 / "
            f"{elapsed}s",
        )

        if view.get("status") != "done" or not sources:
            report["error"] = f"研究没有跑出可验收的结果：{view.get('failure_reason')}"
            return 1

        eids = [item.get("eid") for item in sources]
        report["one_source_one_eid"] = len(set(eids)) == len(eids) == len(sources)

        resolvable = True
        for eid in eids:
            detail = await client.get(f"/api/projects/{project_id}/evidence/{eid}")
            if detail.status_code != 200:
                resolvable = False
                log(report, out, "evidence", f"{eid} → {detail.status_code}")
        report["evidence_resolvable"] = resolvable

        db = db_evidence(database.direct_url, project_id, None)
        report.update(
            {
                "db_source_materials": db["db_source_materials"],
                "db_source_chunks": db["db_source_chunks"],
            }
        )

        # **人用的全局搜索**要搜得到研究来源（材料库里看得见，顶栏搜不到就是丢数据）
        probe = (sources[0].get("title") or "")[:12]
        hits = (
            await client.get(f"/api/projects/{project_id}/search", params={"q": probe})
        ).json()
        found = [
            hit
            for hit in hits.get("hits") or []
            if hit.get("kind") == "material" and hit.get("origin") == "web_research"
        ]
        report["global_search_finds_research_source"] = bool(found)
        log(
            report,
            out,
            "search",
            f"全局搜索命中研究来源：{len(found)} 条（应为 ≥1）",
        )

        # 挂到当前轮：开一轮（真模型出主张）→ 挂第一张来源卡
        round_response = await client.post(
            f"/api/projects/{project_id}/rounds",
            json={"input": "我们要做批量导出，先按研究到的上限定范围"},
        )
        if round_response.status_code != 200:
            report["error"] = (
                f"开轮失败 {round_response.status_code} {round_response.text[:400]}"
            )
            return 1
        round_view = round_response.json()
        round_id = round_view["round_id"]
        report["round_id"] = round_id
        report["round_materials_after_attach"] = len(round_view.get("materials") or ())
        # **RAG 那条边**：建轮自动检索的命中里不许出现研究来源
        research_ids = set(db.get("research_material_ids") or ())
        rag_hits = [
            hit.get("material_id")
            for trace in round_view.get("retrievals") or ()
            for hit in trace.get("hits") or ()
        ]
        report["rag_retrievals"] = len(rag_hits)
        report["rag_includes_research_source"] = bool(research_ids & set(rag_hits))
        log(
            report,
            out,
            "rag",
            f"建轮检索命中 {len(rag_hits)} 条；含研究来源："
            f"{report['rag_includes_research_source']}（应为 False）",
        )

        attached = await client.post(
            f"/api/rounds/{round_id}/research-sources",
            json={"exploration_id": view["exploration_id"], "eids": eids[:1]},
        )
        report["attach_status_code"] = attached.status_code
        if attached.status_code != 200:
            report["error"] = f"挂载失败 {attached.status_code} {attached.text[:300]}"
            return 1
        attached_view = attached.json()
        report["round_research_sources"] = [
            item.get("eid") for item in attached_view.get("research_sources") or ()
        ]
        report["round_materials_after_attach"] = len(attached_view.get("materials") or ())
        log(
            report,
            out,
            "attach",
            f"挂上 {report['round_research_sources']}；"
            f"轮内材料块 {report['round_materials_after_attach']}（应为 0）",
        )

        db2 = db_evidence(database.direct_url, project_id, round_id)
        report["round_payload_research_sources"] = [
            item.get("eid") for item in db2["round_payload_research_sources"]
        ]
        report["round_payload_materials"] = len(db2["round_payload_materials"])

    problems = []
    if not report.get("one_source_one_eid"):
        problems.append("一条来源一个 E 号不成立")
    if not report.get("evidence_resolvable"):
        problems.append("E 号查不回来")
    if not report.get("global_search_finds_research_source"):
        problems.append("全局搜索搜不到研究来源（材料库里看得见却搜不到）")
    if report.get("rag_includes_research_source"):
        problems.append("建轮自动检索召回了研究来源（RAG 应排除）")
    if report.get("db_source_materials") != report.get("source_count"):
        problems.append("库里的来源材料数与视图不一致")
    if report.get("round_materials_after_attach") != 0:
        problems.append("挂来源卡写了 Round.materials（应只写 research_sources）")
    if report.get("round_payload_materials") != 0:
        problems.append("轮次 payload 里出现了材料块")
    if not report.get("round_research_sources"):
        problems.append("挂载后轮上没有来源卡")
    if problems:
        report["error"] = "；".join(problems)
        return 1
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="P3b-1 真模型 + 真 Tavily 研究冒烟")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"started_at": datetime.now(UTC).isoformat()}
    started = time.monotonic()
    code = 1
    try:
        code = await run(report, out)
    except BaseException as error:  # noqa: BLE001 —— 证据脚本：任何失败都要落盘
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        report["exit_code"] = code
        report["elapsed_seconds"] = round(time.monotonic() - started, 1)
        save(report, out)
        print(
            f"证据：{out / 'report.json'} / {out / 'summary.md'}；exit={code}",
            flush=True,
        )
    return code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
