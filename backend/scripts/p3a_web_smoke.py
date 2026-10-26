"""P3a 真跑冒烟：真模型 + 真 Tavily + 真 SQLite，走一次生产 HTTP 路径。

它验的是**主闭环里的联网申请**：

1. Analyzer 真报文里的结构化 `web_requests`（没有就回退旧 `web_queries`，
   再没有才用一条**显式标注的**强制 query）；
2. opening 里批准即执行（真 Tavily 出网、真材料/E 号落库）；
3. `allow_web` 是否让席位/整合者的申请在下一步自动执行；
4. 若还有 `proposed` 的席位/整合者申请，走一次 `decision` 批准（真执行）。
   第一轮名额满导致 409 时，自动再跑一轮 `allow_web=False` 的决策回合。

**不伪造成功**：模型/Tavily 失败、配额满、没有真实出网，都写进证据并返回非 0。

跑法（从 `backend/`；配置读 `backend/.env`，不会打印 key）：

    cd backend
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/p3a_web_smoke.py

证据落到 `.codex/runs/2026-10-26/p3a/`（gitignored）：`report.json` + `summary.md`。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
ENV_FILE = BACKEND_ROOT / ".env"
#: 一次性证据目录（日期固定）。复跑用 `--out` 指到新目录，别覆盖旧证据。
DEFAULT_OUT = REPO_ROOT / ".codex" / "runs" / "2026-10-26" / "p3a"
#: 写盘前要替换掉的敏感值（从 `.env` 的 *KEY* / *SECRET* / *PASSWORD* / *TOKEN* 收集）。
SECRET_VALUES: list[str] = []

INPUT = (
    "我们要做一款面向大学生的时间管理 App。先查一下 2025 年中国大学生平均每天"
    "使用手机多少小时、以及 Forest 和番茄ToDo 这两个竞品最近公开的活跃用户规模，"
    "用这些外部事实来定目标和范围。除此之外还要能按课程表自动生成专注计划。"
)
FORCED_QUERY = "2025 年中国大学生平均每天手机使用时长"


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


def _redact(value):
    """写盘前把 `.env` 里的敏感值替换掉（错误文本也可能带上游回包）。"""

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
        "# P3a 联网申请真跑冒烟",
        "",
        f"- 时间：{report.get('started_at', '')}",
        f"- 模型：`{report.get('model', '')}`",
        f"- Tavily 配置：{'是' if report.get('tavily_configured') else '否'}",
        f"- SQLite：`{report.get('db_path', '')}`",
        f"- 退出码：{report.get('exit_code', '（运行中）')}",
    ]
    for slot in report.get("rounds") or ():
        lines += [
            "",
            f"## {slot.get('label')}",
            "",
            f"- 项目 / 轮次：`{slot.get('project_id')}` / `{slot.get('round_id')}`",
            f"- 申请来源：{slot.get('request_path')}",
            f"- allow_web：{slot.get('allow_web')}",
            f"- opening：step={slot.get('opening_step')} "
            f"waiting_on={slot.get('opening_waiting_on')}",
            f"- 申请状态（opening）：{slot.get('opening_state_counts')}",
            f"- 申请状态（final）：{slot.get('final_state_counts')}",
            f"- 自动批准触发：{slot.get('auto_approval_triggered')}；"
            f"席位/整合执行：{len(slot.get('seat_integrator_executed') or ())} 条",
            f"- 一条结果一个 E 号：{slot.get('one_result_one_eid')}",
            f"- E 号可查：{slot.get('evidence_resolvable')}",
            f"- 决策：{slot.get('decision_status_code')} "
            f"{slot.get('decision_state') or slot.get('decision_error') or '（无待决策申请）'}",
        ]
        if slot.get("decision_new_selected_materials") is not None:
            lines.append(
                f"- 决策新增选中材料：{slot['decision_new_selected_materials']} 份；"
                f"真库 web 材料 {slot.get('decision_db_web_materials_before')}→"
                f"{slot.get('decision_db_web_materials_after')} 份"
            )
        lines += [
            f"- SQLite：web 材料 {slot.get('sqlite', {}).get('web_materials')} 份 / "
            f"{slot.get('sqlite', {}).get('web_chunks')} 块；"
            f"web_selection={slot.get('sqlite', {}).get('web_selection_count')} 项",
        ]
        requests = slot.get("final_web_requests") or ()
        if requests:
            lines += [
                "",
                "| id | origin | state | query | note |",
                "|---|---|---|---|---|",
            ]
            for item in requests:
                lines.append(
                    f"| {item.get('request_id')} | {item.get('origin')} | "
                    f"{item.get('state')} | {str(item.get('query') or '')[:40]} | "
                    f"{str(item.get('result_note') or '')[:60]} |"
                )
    if report.get("error"):
        lines += ["", "## 失败原文摘要", "", f"```text\n{report['error']}\n```"]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def log(report: dict, out: Path, step: str, detail: str) -> None:
    print(f"[{step}] {detail}", flush=True)
    report.setdefault("steps", []).append({"step": step, "detail": detail})
    save(report, out)


def state_counts(round_view: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in round_view.get("web_requests") or ():
        state = str(item.get("state") or "?")
        counts[state] = counts.get(state, 0) + 1
    return counts


def web_materials(round_view: dict) -> list[dict]:
    result = []
    for item in round_view.get("materials") or ():
        if item.get("origin") == "web_search":
            result.append(
                {
                    "material_id": item.get("material_id"),
                    "title": item.get("title"),
                    "source_ref": item.get("source_ref"),
                    "chunks": len(item.get("chunks") or ()),
                    "eids": sorted(
                        {chunk.get("eid") for chunk in item.get("chunks") or ()}
                    ),
                }
            )
    return result


def request_summary(round_view: dict) -> list[dict]:
    return [
        {
            "request_id": item.get("request_id"),
            "origin": item.get("origin"),
            "state": item.get("state"),
            "query": item.get("query"),
            "why": item.get("why"),
            "seat_id": item.get("seat_id"),
            "pass_index": item.get("pass_index"),
            "result_note": item.get("result_note"),
        }
        for item in round_view.get("web_requests") or ()
    ]


def db_evidence(db_path: Path, project_id: str, round_id: str) -> dict:
    """直接读 SQLite（真库证据）：材料/块数、round payload 的申请与 selection。"""

    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        materials = connection.execute(
            "SELECT count(*) AS n FROM materials WHERE project_id = ? AND origin = 'web_search'",
            (project_id,),
        ).fetchone()["n"]
        chunks = connection.execute(
            """
            SELECT count(*) AS n FROM material_chunks c
            JOIN materials m ON m.material_id = c.material_id
            WHERE m.project_id = ? AND m.origin = 'web_search'
            """,
            (project_id,),
        ).fetchone()["n"]
        row = connection.execute(
            "SELECT payload FROM rounds WHERE round_id = ?", (round_id,)
        ).fetchone()
        payload = json.loads(row["payload"]) if row is not None else {}
        selected = payload.get("web_selection")
        return {
            "web_materials": int(materials),
            "web_chunks": int(chunks),
            "web_requests_in_payload": payload.get("web_requests") or [],
            "allow_web_in_payload": payload.get("allow_web"),
            "web_selection": selected,
            "web_selection_count": None if selected is None else len(selected),
        }
    finally:
        connection.close()


def verify_one_result_one_eid(round_view: dict) -> tuple[bool, list[str]]:
    """一条联网结果只发一个 E 号——按材料分组看 EID 是否唯一。"""

    problems: list[str] = []
    for material in web_materials(round_view):
        if len(material["eids"]) != 1:
            problems.append(
                f"{material['material_id']} 有 {len(material['eids'])} 个 E 号："
                f"{material['eids']}"
            )
    return not problems, problems


async def run_round(  # noqa: C901 —— 冒烟脚本按步骤线性写
    client,
    report: dict,
    out: Path,
    db_path: Path,
    *,
    label: str,
    allow_web: bool,
) -> dict:
    """跑一轮：开轮 → opening（真联网）→ 必要时 decision（真联网）→ 真库核对。"""

    slot: dict = {"label": label, "allow_web": allow_web}
    created = await client.post(
        "/api/projects", json={"title": f"P3a 联网申请冒烟（{label}）"}
    )
    if created.status_code != 201:
        slot["error"] = f"建项目失败 {created.status_code} {created.text[:300]}"
        return slot
    project_id = created.json()["project_id"]
    slot["project_id"] = project_id
    log(report, out, f"{label}:project", f"{project_id} 已建")

    started = await client.post(
        f"/api/projects/{project_id}/rounds", json={"input": INPUT}
    )
    if started.status_code != 200:
        slot["error"] = f"开轮失败 {started.status_code} {started.text[:500]}"
        return slot
    round_view = started.json()
    round_id = round_view["round_id"]
    slot["round_id"] = round_id
    slot["web_search_available"] = round_view.get("web_search_available")
    slot["web_search_network_access"] = round_view.get("web_search_network_access")
    slot["analyzer_web_requests"] = request_summary(round_view)
    slot["legacy_web_queries"] = round_view.get("web_queries") or []
    seats = [item["seat_id"] for item in round_view.get("nominated_seats") or ()]
    slot["seats"] = seats
    log(
        report,
        out,
        f"{label}:claims",
        f"主张 {len(round_view.get('claims') or ())} 条；席位 {seats}；"
        f"结构化申请 {len(slot['analyzer_web_requests'])} 条；"
        f"旧检索词 {len(slot['legacy_web_queries'])} 条",
    )
    if not seats:
        slot["error"] = "分析者没有提名任何席位，无法走 opening"
        return slot

    analyzer_requests = [
        item
        for item in slot["analyzer_web_requests"]
        if item["origin"] == "analyzer" and item["state"] == "proposed"
    ]
    approved_ids: list[str] = []
    legacy_queries: list[str] = []
    if analyzer_requests:
        approved_ids = [analyzer_requests[0]["request_id"]]
        slot["request_path"] = "structured-analyzer"
        slot["chosen_request"] = analyzer_requests[0]
    elif slot["legacy_web_queries"]:
        legacy_queries = [slot["legacy_web_queries"][0]["query"]]
        slot["request_path"] = "legacy-nominated"
        slot["chosen_query"] = legacy_queries[0]
    else:
        legacy_queries = [FORCED_QUERY]
        slot["request_path"] = "legacy-forced"
        slot["chosen_query"] = FORCED_QUERY
        slot["forced_legacy"] = True

    opening_body = {
        "seats": seats,
        "claim_edits": [],
        "web_queries": legacy_queries,
        "approved_request_ids": approved_ids,
        "allow_web": allow_web,
    }
    slot["opening_request"] = opening_body
    log(
        report,
        out,
        f"{label}:opening",
        f"发起：{slot['request_path']}；allow_web={allow_web}；"
        f"approved={approved_ids} legacy={legacy_queries}（模型调用可能较慢）",
    )
    opened = await client.post(
        f"/api/rounds/{round_id}/opening", json=opening_body
    )
    if opened.status_code != 200:
        slot["error"] = f"opening 失败 {opened.status_code} {opened.text[:500]}"
        return slot
    round_view = opened.json()
    slot["opening_step"] = round_view.get("step")
    slot["opening_waiting_on"] = round_view.get("waiting_on")
    slot["opening_web_requests"] = request_summary(round_view)
    slot["opening_web_materials"] = web_materials(round_view)
    slot["opening_state_counts"] = state_counts(round_view)
    log(
        report,
        out,
        f"{label}:opening-done",
        f"step={round_view.get('step')} waiting_on={round_view.get('waiting_on')} "
        f"申请状态={slot['opening_state_counts']} "
        f"web 材料 {len(slot['opening_web_materials'])} 份",
    )

    # 真实出网的最低判据：至少一条申请 done 且 note 说搜到了 >0 条，
    # 或至少落下一份 web 材料。失败/空结果照实记，不判 pass。
    done_notes = [
        item
        for item in slot["opening_web_requests"]
        if item["state"] == "done" and item.get("result_note")
    ]
    executed_ok = bool(slot["opening_web_materials"]) or any(
        "搜到 " in str(item.get("result_note") or "")
        and not str(item.get("result_note") or "").startswith("搜到 0")
        for item in done_notes
    )
    failed_notes = [
        item for item in slot["opening_web_requests"] if item["state"] == "failed"
    ]
    if failed_notes:
        slot.setdefault("tavily_failures", []).extend(failed_notes)
    if not executed_ok:
        slot["error"] = (
            "真实联网没有成功产出："
            f"states={slot['opening_state_counts']} "
            f"notes={[item.get('result_note') for item in slot['opening_web_requests']]}"
        )
        return slot
    log(report, out, f"{label}:tavily", "真实联网执行成功，材料已落库")

    # 有 proposed 的席位/整合者申请就走一次 decision 批准（优先非 analyzer）。
    proposed = [
        item for item in slot["opening_web_requests"] if item["state"] == "proposed"
    ]
    proposed.sort(key=lambda item: 0 if item["origin"] in ("seat", "integrator") else 1)
    if proposed:
        target = proposed[0]
        slot["decision_request"] = target
        log(
            report,
            out,
            f"{label}:decision",
            f"批准 {target['request_id']}（{target['origin']}）：可能再出网一次",
        )
        selected_before = len(slot["opening_web_materials"])
        db_before = db_evidence(db_path, project_id, round_id)
        slot["decision_db_web_materials_before"] = db_before["web_materials"]
        decided = await client.post(
            f"/api/rounds/{round_id}/web-requests/{target['request_id']}/decision",
            json={"decision": "approve"},
        )
        slot["decision_status_code"] = decided.status_code
        if decided.status_code == 200:
            round_view = decided.json()
            slot["decision_web_requests"] = request_summary(round_view)
            slot["decision_web_materials"] = web_materials(round_view)
            slot["decision_state"] = next(
                (
                    item["state"]
                    for item in slot["decision_web_requests"]
                    if item["request_id"] == target["request_id"]
                ),
                None,
            )
            slot["decision_new_selected_materials"] = (
                len(slot["decision_web_materials"]) - selected_before
            )
            db_after = db_evidence(db_path, project_id, round_id)
            slot["decision_db_web_materials_after"] = db_after["web_materials"]
            log(
                report,
                out,
                f"{label}:decision-done",
                f"{target['request_id']} → {slot['decision_state']}；"
                f"新增选中材料 {slot['decision_new_selected_materials']} 份；"
                f"真库 web 材料 {slot['decision_db_web_materials_before']}"
                f"→{slot['decision_db_web_materials_after']} 份",
            )
        else:
            body = decided.text[:400]
            slot["decision_error"] = body
            log(
                report,
                out,
                f"{label}:decision-refused",
                f"{decided.status_code} {body}",
            )
    else:
        slot["decision_request"] = None
        log(
            report,
            out,
            f"{label}:decision",
            "没有待决策的申请（allow_web 已自动执行或没有提案）",
        )

    final_view = (await client.get(f"/api/rounds/{round_id}")).json()
    slot["final_step"] = final_view.get("step")
    slot["final_waiting_on"] = final_view.get("waiting_on")
    slot["final_web_requests"] = request_summary(final_view)
    slot["final_web_materials"] = web_materials(final_view)
    slot["final_state_counts"] = state_counts(final_view)

    one_eid_ok, one_eid_problems = verify_one_result_one_eid(final_view)
    slot["one_result_one_eid"] = one_eid_ok
    slot["one_result_one_eid_problems"] = one_eid_problems

    eid_checks: list[dict] = []
    for material in slot["final_web_materials"]:
        for eid in material["eids"]:
            checked = await client.get(f"/api/projects/{project_id}/evidence/{eid}")
            eid_checks.append({"eid": eid, "status": checked.status_code})
    slot["evidence_checks"] = eid_checks
    slot["evidence_resolvable"] = bool(eid_checks) and all(
        item["status"] == 200 for item in eid_checks
    )

    db = db_evidence(db_path, project_id, round_id)
    slot["sqlite"] = db
    slot["web_selection"] = db.get("web_selection")
    # 只数“席位/整合者被批准或执行过”的条目；其中可能是 decision 手动批的，
    # 所以不叫 auto。自动批准触发没触发另记（opening 阶段就 done/failed 的
    # seat/integrator 只可能来自 allow_web 自动批准）。
    slot["seat_integrator_executed"] = [
        item
        for item in slot["final_web_requests"]
        if item["origin"] in ("seat", "integrator")
        and item["state"] in ("done", "failed", "approved")
    ]
    slot["auto_approval_triggered"] = any(
        item["origin"] in ("seat", "integrator")
        and item["state"] in ("done", "failed")
        for item in slot["opening_web_requests"]
    )
    log(
        report,
        out,
        f"{label}:sqlite",
        f"真库：web 材料 {db['web_materials']} 份 / {db['web_chunks']} 块；"
        f"web_selection={db['web_selection_count']} 项；"
        f"one_result_one_eid={one_eid_ok}；"
        f"evidence_resolvable={slot['evidence_resolvable']}",
    )
    return slot


def round_problems(slot: dict) -> list[str]:
    if slot.get("error"):
        return [str(slot["error"])]
    problems: list[str] = []
    if not slot.get("one_result_one_eid"):
        problems.extend(slot.get("one_result_one_eid_problems") or ["E 号不唯一"])
    if not slot.get("evidence_resolvable"):
        problems.append(f"E 号查不到：{slot.get('evidence_checks')}")
    if (slot.get("sqlite") or {}).get("web_selection") is None:
        problems.append("落库 payload 没有 web_selection（旧回退不应出现在新轮）")
    return problems


async def run(report: dict, out: Path) -> int:
    env = load_env_file(ENV_FILE)
    if not env:
        report["error"] = f"{ENV_FILE} 不存在或为空"
        return 2
    if not env.get("PMSTUDIO_API_KEY"):
        report["error"] = "backend/.env 缺 PMSTUDIO_API_KEY"
        return 2
    if not env.get("PMSTUDIO_TAVILY_API_KEY"):
        report["error"] = "backend/.env 缺 PMSTUDIO_TAVILY_API_KEY"
        return 2
    if not (
        env.get("PMSTUDIO_FIRST_ADMIN_USER")
        and env.get("PMSTUDIO_FIRST_ADMIN_PASSWORD")
    ):
        report["error"] = "backend/.env 缺首管理员凭据（PMSTUDIO_FIRST_ADMIN_USER/_PASSWORD）"
        return 2

    db_path = out / "p3a-web-smoke.db"
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(db_path) + suffix)
        if candidate.exists():
            candidate.unlink()
    out.mkdir(parents=True, exist_ok=True)

    run_env = dict(env)
    run_env["PMSTUDIO_DB"] = str(db_path)
    # 本脚本是证据，不是逐调用排查；不写 model_calls/tool_calls（与默认生产一致）。
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

    sys.path.insert(0, str(BACKEND_ROOT / "src"))
    import httpx

    from pmstudio.bootstrap.container import build_container
    from pmstudio.bootstrap.settings import Settings

    settings = Settings.from_env(run_env)
    report.update(
        {
            "model": settings.model,
            "tavily_configured": settings.tavily_api_key is not None,
            "db_path": str(db_path),
            "started_at": datetime.now(UTC).isoformat(),
        }
    )
    log(report, out, "config", f"模型 {settings.model}；Tavily 已配置；临时 SQLite 已建")

    container = build_container(settings)
    transport = httpx.ASGITransport(app=container.app)
    timeout = httpx.Timeout(1800.0, connect=10.0)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://p3a-smoke", timeout=timeout
    ) as client:
        health = await client.get("/api/health")
        log(report, out, "health", f"{health.status_code} {health.json()}")

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

        schema = (await client.get("/api/schema")).json()
        limits = schema.get("limits") or {}
        limit = limits.get("max_web_executions")
        report["schema_max_web_executions"] = limit
        # 生效值 = `min(tool.max_queries, MAX_WEB_EXECUTIONS)`。shipped Tavily
        # 默认 max_queries=3，所以这份默认配置下是 3；脚本只要求 >0，不把 3
        # 当产品契约（合法配置 max_queries=1/2 时不应该判脚本失败）。
        report["schema_limit_note"] = (
            "生效值 = min(tool.max_queries, MAX_WEB_EXECUTIONS)；"
            "默认 Tavily max_queries=3 时为 3，脚本只要求 >0"
        )
        log(
            report,
            out,
            "schema",
            f"max_web_executions={limit}（默认 Tavily 假设；脚本只要求 >0）",
        )
        if not isinstance(limit, int) or limit <= 0:
            report["error"] = (
                "schema.limits.max_web_executions 不是正数："
                f"{limit!r}（默认 Tavily 配置下应为 3）"
            )
            return 1

        first = await run_round(
            client, report, out, db_path, label="round1", allow_web=True
        )
        report.setdefault("rounds", []).append(first)
        report["web_search_available"] = first.get("web_search_available")
        report["web_search_network_access"] = first.get("web_search_network_access")
        report["seat_integrator_executed"] = first.get("seat_integrator_executed")
        report["auto_approval_triggered"] = first.get("auto_approval_triggered")
        problems = round_problems(first)
        if problems:
            report["error"] = "第一轮：" + "；".join(problems)
            return 1

        # 第一轮 allow_web 通常会把名额用满；成功决策要靠一轮 allow_web=False。
        second: dict | None = None
        if first.get("decision_status_code") != 200:
            second = await run_round(
                client, report, out, db_path, label="round2", allow_web=False
            )
            report["rounds"].append(second)
            problems = round_problems(second)
            if problems:
                report["error"] = "第二轮：" + "；".join(problems)
                return 1

    report["auto_approval_triggered"] = any(
        slot.get("auto_approval_triggered") for slot in report["rounds"]
    )
    attempted = [slot for slot in report["rounds"] if slot.get("decision_request")]
    succeeded = [
        slot for slot in attempted if slot.get("decision_status_code") == 200
    ]
    report["decision_attempted"] = len(attempted)
    report["decision_succeeded"] = len(succeeded)
    if attempted and not succeeded:
        report["error"] = "有席位/整合申请，但 decision 批准一次都没成功"
        return 1
    if not attempted:
        report["decision_note"] = "两轮都没有待决策的席位/整合申请（模型没提案）"
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="P3a 真模型 + 真 Tavily 冒烟")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="证据目录；默认是一次性日期目录，复跑建议用 --out 指到新目录",
    )
    args = parser.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"started_at": datetime.now(UTC).isoformat()}
    started = time.monotonic()
    try:
        code = await run(report, out)
    except BaseException as error:  # noqa: BLE001 —— 证据脚本：任何失败都要落盘
        report["error"] = f"{type(error).__name__}: {error}"
        code = 1
        raise
    finally:
        report["exit_code"] = code if "code" in locals() else 1
        report["elapsed_seconds"] = round(time.monotonic() - started, 1)
        save(report, out)
        print(
            f"证据：{out / 'report.json'} / {out / 'summary.md'}；"
            f"exit={report['exit_code']}",
            flush=True,
        )
    return code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
