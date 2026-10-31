"""真实模型四轮「精确 replace / append」验收。

它不跑单元测试，也不连接业务库：在 `127.0.0.1:55433/pmstudio_test` 上建一个
随机 schema（前缀固定含 `exact`），跑迁移 + finalize，然后起一个**真实 uvicorn
子进程**，用真实 HTTP、真实首管理员、真实模型走完四轮。

设计要点：

- 只覆盖两个 DSN 的 search_path，绝不读 `.env` 里的业务库地址；
- `PMSTUDIO_TRACE_MODEL=1` 只放进子进程环境，用于从 `model_calls` 读原始回复；
- schema **不自动删除**：输出名字与打码后的 DSN，留给浏览器复核与人工清理；
- 断言失败也继续写 JSON，并把现象、最小输入留在里面，不改成"绿"。

用法（在 backend 下）：

    .venv/bin/python scripts/exact_write_acceptance.py --port 8011

退出码：0 全部断言通过；1 有断言/调用失败；2 配置或启动失败。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "src"))
sys.path.insert(0, str(BACKEND_ROOT))

from scripts.pg_scratch import load_env_files, make_scratch  # noqa: E402

DEFAULT_DB = "postgresql://postgres:postgres@127.0.0.1:55433/pmstudio_test"
PREFIX = "exact_acceptance"
ROUND_INPUTS = (
    "新增订单导出功能。主流程：1. 只筛选最近7天订单；2. 校验运营权限；3. 生成xlsx；4. 提供下载。",
    '把订单导出主流程的第一步从"最近7天"改成"最近30天"，其余三步保持不变，不新增流程。',
    "新增一条规则：导出文件7天后自动删除。不要改动主流程。",
    '把主流程第三步的字样改成"生成 Excel（.xlsx）文件"，其余保持。',
)
FEATURE_SLUG = "order-export"
SECTION_ID = "s5"


class Assertions:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.items.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    def failed(self) -> list[dict[str, Any]]:
        return [item for item in self.items if not item["ok"]]


def default_selected(proposal: dict[str, Any]) -> bool:
    """与前端 `defaultAccepted` 同语义：拟议/模拟/重叠/需过目一律不勾。"""

    return (
        proposal.get("origin_type") != "proposed"
        and proposal.get("simulation_based") is not True
        and proposal.get("overlap") is None
        and proposal.get("requires_review") is not True
    )


def review_categories(proposal: dict[str, Any]) -> list[str]:
    warning = proposal.get("warning") or ""
    categories: list[str] = []
    if "不在本轮去处里" in warning:
        categories.append("outside_round_scope")
    if "几乎相同的新增" in warning:
        categories.append("batch_add_duplicate")
    if "这轮输入没有提到" in warning:
        categories.append("unmentioned_target")
    if proposal.get("overlap") is not None:
        categories.append("overlap")
    if proposal.get("origin_type") == "proposed":
        categories.append("origin_type_proposed")
    if proposal.get("simulation_based") is True:
        categories.append("simulation_based")
    if proposal.get("requires_review") is True and not categories:
        categories.append("other_requires_review")
    return categories


def skip_reasons(proposal: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if proposal.get("origin_type") == "proposed":
        reasons.append("origin_type=proposed（拟议内容默认不勾）")
    if proposal.get("simulation_based") is True:
        reasons.append("simulation_based（模拟评测依据默认不勾）")
    if proposal.get("overlap") is not None:
        reasons.append(f"overlap={proposal.get('overlap')}（与既有内容重叠，默认不勾）")
    if proposal.get("requires_review") is True:
        reasons.append(f"requires_review={proposal.get('warning') or '需要过目'}")
    return reasons


def is_unmentioned_review(proposal: dict[str, Any]) -> bool:
    warning = proposal.get("warning") or ""
    return (
        proposal.get("requires_review") is True
        and "这轮输入没有提到" in warning
    )


def skipped_record(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_id": proposal.get("proposal_id"),
        "op": proposal.get("op"),
        "entry_id": proposal.get("entry_id"),
        "target_label": proposal.get("target_label"),
        "base_version_no": proposal.get("base_version_no"),
        "requires_review": proposal.get("requires_review"),
        "warning": proposal.get("warning"),
        "overlap": proposal.get("overlap"),
        "origin_type": proposal.get("origin_type"),
        "simulation_based": proposal.get("simulation_based"),
        "reasons": skip_reasons(proposal),
        "review_categories": review_categories(proposal),
        "text": proposal.get("text"),
    }


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def redact_dsn(url: str) -> str:
    parts = urlsplit(url)
    if parts.password is None:
        return url
    user = parts.username or ""
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{user}:***@{host}{port}" if user else f"***@{host}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def secrets_from(env: dict[str, str], urls: list[str]) -> list[str]:
    values = [
        env.get("PMSTUDIO_API_KEY", ""),
        env.get("PMSTUDIO_FIRST_ADMIN_PASSWORD", ""),
        env.get("PMSTUDIO_OWNER_PASSWORD", ""),
        env.get("PMSTUDIO_APP_PASSWORD", ""),
        env.get("POSTGRES_PASSWORD", ""),
    ]
    for url in urls:
        parsed = urlsplit(url)
        if parsed.password:
            values.append(
                f"{parsed.username}:{parsed.password}"
                if parsed.username
                else parsed.password
            )
    return [value for value in dict.fromkeys(values) if value and len(value) >= 4]


def scrub(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, dict):
        return {str(key): scrub(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(item, secrets) for item in value]
    if isinstance(value, str):
        text = value
        for secret in secrets:
            text = text.replace(secret, "***")
        return text
    return value


def write_json(path: Path, payload: dict[str, Any], secrets: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(scrub(payload, secrets), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def model_url(env: dict[str, str]) -> str:
    return str(env.get("PMSTUDIO_API_BASE") or "(官方默认)")


def test_database(env: dict[str, str]) -> str:
    value = str(
        env.get("PMSTUDIO_ACCEPTANCE_DB_URL")
        or env.get("PMSTUDIO_TEST_DATABASE_URL")
        or DEFAULT_DB
    )
    parsed = urlsplit(value)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise SystemExit(f"验收库必须是 postgresql://，现在是 {parsed.scheme!r}")
    if (parsed.path or "").rstrip("/") in {"", "/pmstudio"}:
        raise SystemExit("拒绝连接业务库 pmstudio；请用 pmstudio_test")
    return value


def build_uvicorn_env(
    env: dict[str, str], scratch: Any, *, model_timeout: float | None
) -> dict[str, str]:
    merged = {**os.environ}
    for key, value in env.items():
        merged.setdefault(key, value)
    merged.pop("PMSTUDIO_BOOTSTRAP_DATABASE_URL", None)
    merged.update(
        {
            "PYTHONPATH": str(BACKEND_ROOT / "src"),
            "PMSTUDIO_DATABASE_URL": scratch.app_url,
            "PMSTUDIO_DIRECT_DATABASE_URL": scratch.direct_url,
            "PMSTUDIO_TRACE_MODEL": "1",
            "PMSTUDIO_WORKERS": "1",
        }
    )
    if model_timeout is not None:
        merged["PMSTUDIO_MODEL_TIMEOUT"] = str(model_timeout)
    return merged


def start_uvicorn(*, port: int, env: dict[str, str], log_path: Path) -> subprocess.Popen[bytes]:
    log = log_path.open("wb")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "pmstudio.bootstrap.container:build_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(BACKEND_ROOT),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    process._pmstudio_log = log  # type: ignore[attr-defined]
    return process


def stop_uvicorn(process: subprocess.Popen[bytes] | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
    log = getattr(process, "_pmstudio_log", None)
    if log is not None:
        try:
            log.close()
        except Exception:  # noqa: BLE001
            pass


async def wait_healthy(port: int, *, timeout: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=5.0) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(f"http://127.0.0.1:{port}/api/health")
                if response.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
    return False


def target_label(target: dict[str, Any]) -> str:
    if target.get("zone") == "preference":
        return "preference"
    text = str(target.get("section_id") or "")
    if target.get("feature_slug"):
        text = f"{text}/{target['feature_slug']}"
    if target.get("field"):
        text = f"{text}.{target['field']}"
    return text


def parse_reply(reply: str) -> tuple[dict[str, Any] | None, str | None]:
    text = reply.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value, None
    return None, "回复不是 JSON 对象"


def raw_proposals(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    items = payload.get("proposals")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def op_counts(proposals: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(item.get("op") or "(missing)") for item in proposals)
    return dict(sorted(counts.items()))


def read_model_calls(dsn: str, round_id: str) -> list[dict[str, Any]]:
    with psycopg.connect(dsn, autocommit=True) as connection:
        rows = connection.execute(
            "SELECT id, purpose, attempt, ok, error, reply_text"
            " FROM model_calls WHERE round_id = %s AND purpose = 'integration'"
            " ORDER BY id",
            (round_id,),
        ).fetchall()
    calls: list[dict[str, Any]] = []
    for row in rows:
        payload, error = parse_reply(row[5] or "")
        proposals = raw_proposals(payload)
        calls.append(
            {
                "id": row[0],
                "attempt": row[2],
                "ok": row[3],
                "error": row[4],
                "parse_error": error,
                "raw_op_counts": op_counts(proposals),
                "raw_proposals": [
                    {
                        "op": item.get("op"),
                        "entry_id": item.get("entry_id"),
                        "target": item.get("target"),
                        "target_label": target_label(item.get("target") or {}),
                        "text": item.get("text"),
                        "origin_type": item.get("origin_type"),
                        "kind": item.get("kind"),
                    }
                    for item in proposals
                ],
                "reply_excerpt": (row[5] or "")[:1200],
            }
        )
    return calls


def collect_entries(document: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    def walk(
        block: dict[str, Any],
        *,
        section_id: str,
        feature_slug: str | None,
        inherited_field: str | None,
    ) -> None:
        kind = block.get("kind")
        feature = block.get("feature_slug") or feature_slug
        field = block.get("field") or inherited_field
        if kind == "field" and block.get("field"):
            field = block["field"]
        for entry in block.get("entries") or []:
            entries.append(
                {
                    "entry_id": entry.get("entry_id"),
                    "section_id": section_id,
                    "feature_slug": feature,
                    "field": field,
                    "location": (
                        f"{section_id}/{feature}.{field}"
                        if feature is not None and field is not None
                        else section_id
                    ),
                    "text": entry.get("text") or "",
                    "refs": entry.get("refs") or [],
                }
            )
        for child in block.get("children") or []:
            walk(
                child,
                section_id=section_id,
                feature_slug=feature,
                inherited_field=field,
            )

    for section in document.get("sections") or []:
        section_id = str(section.get("section_id"))
        for block in section.get("blocks") or []:
            walk(
                block,
                section_id=section_id,
                feature_slug=None,
                inherited_field=None,
            )
    return entries


def find_feature_block(document: dict[str, Any], slug: str) -> tuple[str, dict[str, Any]] | None:
    for section in document.get("sections") or []:
        for block in section.get("blocks") or []:
            if block.get("kind") == "feature" and block.get("feature_slug") == slug:
                return str(section.get("section_id")), block
    return None


def find_field_block(block: dict[str, Any], field: str) -> dict[str, Any] | None:
    for child in block.get("children") or []:
        if child.get("field") == field:
            return child
    return None


def field_texts(block: dict[str, Any] | None) -> list[str]:
    if block is None:
        return []
    return [entry.get("text") or "" for entry in block.get("entries") or []]


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


_STEP_MARK = re.compile(r"(?:^|[\n\r；;])\s*(\d{1,2})\s*[.、)]\s*")


def parse_steps(text: str) -> list[str]:
    marks = list(_STEP_MARK.finditer(text))
    if len(marks) < 2:
        marks = list(re.finditer(r"(?<!\d)(\d{1,2})\s*[.、)]\s*", text))
    if len(marks) < 2:
        return []
    steps: list[str] = []
    for index, match in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        steps.append(text[match.end() : end].strip(" \n\r；;"))
    return steps


def entry_multiset(entries: list[dict[str, Any]]) -> Counter[tuple[str, str]]:
    return Counter((entry["location"], entry["text"]) for entry in entries)


def by_entry_id(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(entry["entry_id"]): entry for entry in entries}


def duplicates(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: Counter[tuple[str, str]] = Counter()
    for entry in entries:
        text = squash(entry["text"])
        if text:
            seen[(entry["location"], text)] += 1
    return [
        {"location": location, "text": text, "count": count}
        for (location, text), count in seen.items()
        if count > 1
    ]


def diff_entries(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    *,
    exclude_entry_ids: set[str] | None = None,
    exclude_locations: set[str] | None = None,
) -> list[dict[str, Any]]:
    exclude_entry_ids = exclude_entry_ids or set()
    exclude_locations = exclude_locations or set()
    old = Counter(
        (entry["location"], entry["text"])
        for entry in before
        if str(entry["entry_id"]) not in exclude_entry_ids
        and entry["location"] not in exclude_locations
    )
    new = Counter(
        (entry["location"], entry["text"])
        for entry in after
        if str(entry["entry_id"]) not in exclude_entry_ids
        and entry["location"] not in exclude_locations
    )
    changes: list[dict[str, Any]] = []
    for key in old - new:
        changes.append({"change": "removed", "location": key[0], "text": key[1]})
    for key in new - old:
        changes.append({"change": "added", "location": key[0], "text": key[1]})
    return changes


def final_integration_call(
    calls: list[dict[str, Any]], draft: dict[str, Any]
) -> dict[str, Any] | None:
    proposals = draft.get("proposals") or []

    def score(call: dict[str, Any]) -> int:
        value = 0
        used: set[int] = set()
        for proposal in proposals:
            best = 0
            best_index = None
            for index, item in enumerate(call.get("raw_proposals") or []):
                if index in used:
                    continue
                candidate = 0
                if item.get("target_label") == proposal.get("target_label"):
                    candidate += 1
                if (
                    item.get("entry_id")
                    and str(item.get("entry_id"))
                    == str(proposal.get("entry_id") or "")
                ):
                    candidate += 8
                if item.get("op") == proposal.get("op"):
                    candidate += 2
                if squash(item.get("text") or "") == squash(
                    proposal.get("text") or ""
                ):
                    candidate += 4
                if candidate > best:
                    best, best_index = candidate, index
            if best_index is not None and best > 0:
                used.add(best_index)
                value += best
        return value

    best: tuple[int, int, dict[str, Any]] | None = None
    for call in calls:
        candidate = (score(call), call.get("id") or 0, call)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return best[2] if best is not None and best[0] > 0 else None


def classify_origins(
    draft: dict[str, Any],
    final_call: dict[str, Any] | None,
    target_entry_id: str | None,
) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = [
        {**item, "call_id": final_call.get("id"), "index": index, "used": False}
        for index, item in enumerate((final_call or {}).get("raw_proposals") or [])
    ]

    def score(item: dict[str, Any], proposal: dict[str, Any]) -> int:
        value = 0
        if item.get("target_label") == proposal.get("target_label"):
            value += 1
        if (
            item.get("entry_id")
            and str(item.get("entry_id")) == str(proposal.get("entry_id") or "")
        ):
            value += 8
        if item.get("op") == proposal.get("op"):
            value += 2
        if squash(item.get("text") or "") == squash(proposal.get("text") or ""):
            value += 4
        return value

    classified: list[dict[str, Any]] = []
    for proposal in draft.get("proposals") or []:
        candidates = [item for item in raw if not item["used"]]
        candidates.sort(key=lambda item: score(item, proposal), reverse=True)
        origin = (
            candidates[0]
            if candidates and score(candidates[0], proposal) >= 3
            else None
        )
        if origin is not None:
            origin["used"] = True
        fallback = None
        if origin is not None:
            if origin.get("op") == "add" and proposal.get("op") == "replace":
                fallback = "add_to_replace"
            elif origin.get("op") == "add" and proposal.get("overlap"):
                fallback = "overlap"
            elif origin.get("op") != proposal.get("op"):
                fallback = f"{origin.get('op')}->{proposal.get('op')}"
        classified.append(
            {
                "proposal_id": proposal.get("proposal_id"),
                "op": proposal.get("op"),
                "entry_id": proposal.get("entry_id"),
                "target_label": proposal.get("target_label"),
                "text": proposal.get("text"),
                "origin_raw_op": origin.get("op") if origin else None,
                "origin_raw_entry_id": origin.get("entry_id") if origin else None,
                "origin_call_id": origin.get("call_id") if origin else None,
                "origin_matched": origin is not None,
                "fallback": fallback,
                "hits_target_entry": (
                    target_entry_id is not None
                    and str(proposal.get("entry_id")) == str(target_entry_id)
                ),
            }
        )
    unmatched = [item for item in raw if not item["used"]]
    return [
        {
            "final_call_id": (final_call or {}).get("id"),
            "proposals": classified,
            "system_fallbacks": [
                item for item in classified if item.get("fallback")
            ],
            "unmatched_final_call_proposals": [
                {
                    "index": item["index"],
                    "op": item.get("op"),
                    "entry_id": item.get("entry_id"),
                    "target_label": item.get("target_label"),
                    "text": item.get("text"),
                }
                for item in unmatched
            ],
        }
    ]


def annotate_hits(rounds: list[dict[str, Any]], target_entry_id: str | None) -> None:
    for round_report in rounds:
        for call in round_report.get("model_calls") or []:
            for item in call.get("raw_proposals") or []:
                item["hits_F"] = (
                    target_entry_id is not None
                    and str(item.get("entry_id")) == str(target_entry_id)
                )


async def login(client: httpx.AsyncClient, env: dict[str, str]) -> dict[str, Any]:
    username = env.get("PMSTUDIO_FIRST_ADMIN_USER", "")
    password = env.get("PMSTUDIO_FIRST_ADMIN_PASSWORD", "")
    if not username or not password:
        raise RuntimeError("缺首管理员凭据（PMSTUDIO_FIRST_ADMIN_USER / _PASSWORD）")
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    if response.status_code != 200:
        raise RuntimeError(f"登录失败：HTTP {response.status_code} {response.text[:300]}")
    return {"username": response.json().get("username", username)}


async def get_json(client: httpx.AsyncClient, url: str, **kwargs: Any) -> Any:
    response = await client.get(url, **kwargs)
    if response.status_code != 200:
        raise RuntimeError(f"GET {url} → HTTP {response.status_code}: {response.text[:500]}")
    return response.json()


async def post_json(client: httpx.AsyncClient, url: str, payload: dict[str, Any]) -> Any:
    response = await client.post(url, json=payload)
    if response.status_code != 200:
        raise RuntimeError(
            f"POST {url} → HTTP {response.status_code}: {response.text[:800]}"
        )
    return response.json()


async def poll_round(
    client: httpx.AsyncClient,
    round_id: str,
    *,
    deadline: float,
    initial: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = initial
    while True:
        if body is None:
            body = await get_json(client, f"/api/rounds/{round_id}")
        waiting = body.get("waiting_on")
        if waiting in {"draft_card", "questionnaire", "failed"}:
            return body
        if time.monotonic() >= deadline:
            return body
        await asyncio.sleep(3)
        body = None


async def answer_until_draft(
    client: httpx.AsyncClient,
    round_id: str,
    body: dict[str, Any],
    *,
    deadline: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    batches: list[dict[str, Any]] = []
    for _ in range(8):
        body = await poll_round(client, round_id, deadline=deadline, initial=body)
        waiting = body.get("waiting_on")
        if waiting == "draft_card":
            return body, batches
        if waiting == "failed":
            raise RuntimeError(f"轮次失败：{body.get('failure_reason')}")
        if waiting != "questionnaire":
            raise RuntimeError(f"没有停在草案卡或问卷：waiting_on={waiting!r}")
        questions = body.get("questions") or []
        skipped = [item["question_id"] for item in questions]
        batches.append(
            {
                "form_id": body.get("form_id"),
                "questions": [
                    {
                        "question_id": item.get("question_id"),
                        "kind": item.get("kind"),
                        "title": item.get("title"),
                        "options": item.get("options"),
                    }
                    for item in questions
                ],
                "answers": [],
                "skipped": skipped,
            }
        )
        body = await post_json(
            client,
            f"/api/rounds/{round_id}/answers",
            {"form_id": body.get("form_id"), "answers": [], "skipped": skipped},
        )
    raise RuntimeError("连续 8 批问卷仍未出草案卡")


def seed_report(env: dict[str, str], scratch: Any, *, port: int) -> dict[str, Any]:
    return {
        "status": "running",
        "started_at": now_iso(),
        "finished_at": None,
        "schema": scratch.schema,
        "project_id": None,
        "app_dsn_redacted": redact_dsn(scratch.app_url),
        "direct_dsn_redacted": redact_dsn(scratch.direct_url),
        "model": env.get("PMSTUDIO_MODEL"),
        "api_base": model_url(env),
        "uvicorn_port": port,
        "rounds": [],
        "summary": {},
        "errors": [],
    }


async def run_acceptance(
    *,
    env: dict[str, str],
    scratch: Any,
    port: int,
    output: Path,
    secrets: list[str],
    http_timeout: float,
    round_timeout: float,
) -> int:
    report = seed_report(env, scratch, port=port)
    write_json(output, report, secrets)
    process: subprocess.Popen[bytes] | None = None
    log_path = Path(tempfile.gettempdir()) / f"exact-write-acceptance-uvicorn-{port}.log"
    try:
        process = start_uvicorn(
            port=port,
            env=build_uvicorn_env(env, scratch, model_timeout=None),
            log_path=log_path,
        )
        if not await wait_healthy(port):
            tail = log_path.read_text(errors="replace")[-4000:]
            raise RuntimeError(f"uvicorn 没起来，日志尾部：\n{tail}")
        print(f"[boot] uvicorn http://127.0.0.1:{port} · schema {scratch.schema}", flush=True)

        timeout = httpx.Timeout(http_timeout, connect=20.0)
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=timeout, follow_redirects=True
        ) as client:
            report["login"] = await login(client, env)
            print(f"[login] {report['login']['username']}", flush=True)
            created = await client.post(
                "/api/projects", json={"title": "精确写入验收（随机 schema）"}
            )
            if created.status_code != 201:
                raise RuntimeError(
                    f"建项目失败 HTTP {created.status_code}: {created.text[:500]}"
                )
            project_id = created.json()["project_id"]
            report["project_id"] = project_id
            print(f"[project] {project_id}", flush=True)

            previous_document: dict[str, Any] | None = None
            previous_version = 1
            target_entry_id: str | None = None

            for index, user_input in enumerate(ROUND_INPUTS, start=1):
                round_report: dict[str, Any] = {
                    "index": index,
                    "input": user_input,
                    "version_before": previous_version,
                }
                report["rounds"].append(round_report)
                deadline = time.monotonic() + round_timeout
                assertions = Assertions()
                round_report["assertions"] = assertions.items
                write_json(output, report, secrets)
                print(f"[round {index}] 输入：{user_input}", flush=True)

                started = await post_json(
                    client, f"/api/projects/{project_id}/rounds", {"input": user_input}
                )
                round_id = started["round_id"]
                round_report["round_id"] = round_id
                round_report["start"] = {
                    "step": started.get("step"),
                    "waiting_on": started.get("waiting_on"),
                    "nominated_seats": [
                        {
                            "seat_id": item.get("seat_id"),
                            "title": item.get("title"),
                            "why": item.get("why"),
                            "source": item.get("source"),
                        }
                        for item in started.get("nominated_seats") or []
                    ],
                    "claims": [
                        {
                            "claim_id": item.get("claim_id"),
                            "eid": item.get("eid"),
                            "text": item.get("text"),
                            "label": item.get("label"),
                            "target_label": item.get("target_label"),
                        }
                        for item in started.get("claims") or []
                    ],
                }
                print(
                    f"[round {index}] {round_id} "
                    f"waiting_on={started.get('waiting_on')}",
                    flush=True,
                )

                seat_ids = [
                    item["seat_id"]
                    for item in started.get("nominated_seats") or []
                    if item.get("seat_id")
                ]
                assertions.check("开场卡有提名席位", bool(seat_ids), f"{seat_ids}")
                body = await post_json(
                    client,
                    f"/api/rounds/{round_id}/opening",
                    {"seats": seat_ids, "claim_edits": []},
                )
                body, batches = await answer_until_draft(
                    client, round_id, body, deadline=deadline
                )
                round_report["question_batches"] = batches
                if body.get("waiting_on") != "draft_card" or body.get("draft") is None:
                    raise RuntimeError(
                        f"没有草案卡：waiting_on={body.get('waiting_on')} "
                        f"reason={body.get('failure_reason')}"
                    )
                draft = body["draft"]
                round_report["dropped"] = body.get("dropped") or []
                round_report["draft"] = {
                    "draft_id": draft.get("draft_id"),
                    "gaps": draft.get("gaps"),
                    "advisories": body.get("advisories"),
                    "groups": [
                        {
                            "title": title,
                            "proposals": [
                                {
                                    "proposal_id": item.get("proposal_id"),
                                    "op": item.get("op"),
                                    "entry_id": item.get("entry_id"),
                                    "target_label": item.get("target_label"),
                                    "text": item.get("text"),
                                    "base_version_no": item.get("base_version_no"),
                                    "overlap": item.get("overlap"),
                                    "warning": item.get("warning"),
                                    "origin_type": item.get("origin_type"),
                                    "simulation_based": item.get("simulation_based"),
                                    "requires_review": item.get("requires_review"),
                                    "kind": item.get("kind"),
                                }
                                for item in proposals
                            ],
                        }
                        for title, proposals in draft.get("groups") or []
                    ],
                    "proposals": [
                        {
                            "proposal_id": item.get("proposal_id"),
                            "op": item.get("op"),
                            "entry_id": item.get("entry_id"),
                            "target_label": item.get("target_label"),
                            "text": item.get("text"),
                            "base_version_no": item.get("base_version_no"),
                            "overlap": item.get("overlap"),
                            "warning": item.get("warning"),
                            "origin_type": item.get("origin_type"),
                            "simulation_based": item.get("simulation_based"),
                            "requires_review": item.get("requires_review"),
                            "kind": item.get("kind"),
                        }
                        for item in draft.get("proposals") or []
                    ],
                }
                reviewed = [
                    item
                    for item in draft.get("proposals") or []
                    if item.get("requires_review") is True
                ]
                review_categories_count = Counter(
                    category
                    for item in reviewed
                    for category in review_categories(item)
                )
                round_report["review_stats"] = {
                    "hits": len(reviewed),
                    "categories": dict(sorted(review_categories_count.items())),
                    "hit_proposals": [
                        {
                            "proposal_id": item.get("proposal_id"),
                            "op": item.get("op"),
                            "entry_id": item.get("entry_id"),
                            "target_label": item.get("target_label"),
                            "review_categories": review_categories(item),
                            "warning": item.get("warning"),
                        }
                        for item in reviewed
                    ],
                }
                print(
                    f"[round {index}] draft {draft.get('draft_id')} "
                    f"proposals={len(draft.get('proposals') or [])} "
                    f"requires_review={len(reviewed)} "
                    f"{dict(sorted(review_categories_count.items()))}",
                    flush=True,
                )
                write_json(output, report, secrets)

                calls = read_model_calls(scratch.direct_url, round_id)
                round_report["model_calls"] = calls
                round_report["raw_op_distribution"] = Counter(
                    op
                    for call in calls
                    for op, count in (call.get("raw_op_counts") or {}).items()
                    for _ in range(count)
                )
                round_report["raw_op_distribution"] = dict(
                    sorted(round_report["raw_op_distribution"].items())
                )
                final_call = final_integration_call(calls, draft)
                round_report["final_integration_call_id"] = (
                    final_call.get("id") if final_call is not None else None
                )
                round_report["final_call_op_distribution"] = (
                    final_call.get("raw_op_counts") if final_call is not None else None
                )
                round_report["final_call_proposals"] = (
                    final_call.get("raw_proposals") if final_call is not None else []
                )
                origin = classify_origins(draft, final_call, target_entry_id)
                round_report["origin_classification"] = origin[0]

                proposals = draft.get("proposals") or []
                accepted = [
                    item["proposal_id"] for item in proposals if default_selected(item)
                ]
                skipped = [
                    skipped_record(item)
                    for item in proposals
                    if not default_selected(item)
                ]
                round_report["selection"] = {
                    "accepted": accepted,
                    "skipped": skipped,
                    "skipped_count": len(skipped),
                    "accepted_count": len(accepted),
                    "unmentioned_skipped": [
                        item
                        for item in skipped
                        if item["op"] in {"replace", "remove"}
                        and "这轮输入没有提到" in (item.get("warning") or "")
                    ],
                }
                print(
                    f"[round {index}] selection accepted={len(accepted)} "
                    f"skipped={len(skipped)}",
                    flush=True,
                )
                confirmed = await post_json(
                    client,
                    f"/api/rounds/{round_id}/confirm",
                    {"draft_id": draft.get("draft_id"), "accepted": accepted},
                )
                version_no = confirmed.get("version_no")
                round_report["version_after"] = version_no
                round_report["accepted"] = accepted
                workspace = await get_json(client, f"/api/projects/{project_id}/workspace")
                document = workspace["document"]
                round_report["document_version_no"] = document.get("version_no")
                entries = collect_entries(document)
                round_report["entries"] = entries
                round_report["target_fields"] = {
                    "main_flow": [],
                    "rules": [],
                }
                feature = find_feature_block(document, FEATURE_SLUG)
                if feature is not None:
                    _, feature_block = feature
                    round_report["target_fields"]["main_flow"] = field_texts(
                        find_field_block(feature_block, "main_flow")
                    )
                    round_report["target_fields"]["rules"] = field_texts(
                        find_field_block(feature_block, "rules")
                    )
                print(
                    f"[round {index}] confirmed v{version_no} "
                    f"doc v{document.get('version_no')}",
                    flush=True,
                )

                assertions.check(
                    "确认版本号 +1",
                    version_no == previous_version + 1,
                    f"before={previous_version} after={version_no}",
                )
                assertions.check(
                    "文档版本号等于确认版本",
                    document.get("version_no") == version_no,
                    f"doc={document.get('version_no')} confirmed={version_no}",
                )
                assertions.check(
                    "没有同一内容两份",
                    not duplicates(entries),
                    json.dumps(duplicates(entries), ensure_ascii=False),
                )
                if feature is None:
                    assertions.check("s5 存在功能 order-export", False, "没找到功能块")
                    write_json(output, report, secrets)
                    break
                assertions.check("s5 存在功能 order-export", True, feature[0])

                ids = by_entry_id(entries)
                main_block = find_field_block(feature[1], "main_flow")
                rules_block = find_field_block(feature[1], "rules")
                main_texts = field_texts(main_block)
                rule_texts = field_texts(rules_block)

                if index == 1:
                    assertions.check(
                        "Round1 main_flow 恰有 1 条",
                        len(main_texts) == 1,
                        f"count={len(main_texts)} texts={main_texts}",
                    )
                    text = main_texts[0] if main_texts else ""
                    compact = squash(text)
                    for phrase in ("最近7天", "运营权限", "xlsx", "下载"):
                        assertions.check(
                            f"Round1 main_flow 含 {phrase}",
                            phrase.lower() in compact.lower(),
                            text,
                        )
                    steps = parse_steps(text)
                    assertions.check("Round1 main_flow 可解析为 4 步", len(steps) == 4, f"{steps}")
                    if main_block is not None and main_block.get("entries"):
                        target_entry_id = str(main_block["entries"][0].get("entry_id"))
                    assertions.check("Round1 记录 F", bool(target_entry_id), f"F={target_entry_id}")
                    assertions.check(
                        "目标零丢失（Round1 F 已建）",
                        target_entry_id is not None
                        and ids.get(str(target_entry_id)) is not None,
                        f"F={target_entry_id}",
                    )
                    round_report["main_flow_entry_id"] = target_entry_id
                    round_report["main_flow_text"] = text

                elif index == 2:
                    current_id = (
                        str(main_block["entries"][0].get("entry_id"))
                        if main_block is not None and main_block.get("entries")
                        else None
                    )
                    assertions.check(
                        "Round2 F entry_id 不变",
                        current_id == target_entry_id,
                        f"before={target_entry_id} after={current_id}",
                    )
                    assertions.check(
                        "Round2 main_flow 仍只有 1 条",
                        len(main_texts) == 1,
                        f"count={len(main_texts)} texts={main_texts}",
                    )
                    text = main_texts[0] if main_texts else ""
                    compact = squash(text)
                    assertions.check("Round2 含 30天", "30天" in compact, text)
                    assertions.check("Round2 不含 7天", "7天" not in compact, text)
                    steps = parse_steps(text)
                    assertions.check("Round2 仍四步", len(steps) == 4, f"{steps}")
                    target_replaces = [
                        item
                        for item in proposals
                        if item.get("proposal_id") in accepted
                        and item.get("op") == "replace"
                        and str(item.get("entry_id")) == str(target_entry_id)
                    ]
                    assertions.check(
                        "Round2 目标 F 的 replace 被默认勾选",
                        len(target_replaces) == 1
                        and target_replaces[0].get("requires_review") is False,
                        json.dumps(target_replaces, ensure_ascii=False),
                    )
                    assertions.check(
                        "目标零丢失（Round2 F replace 生效）",
                        ids.get(str(target_entry_id)) is not None
                        and "30天" in squash(
                            ids.get(str(target_entry_id), {}).get("text", "")
                        ),
                        f"F={target_entry_id}",
                    )
                    if previous_document is not None:
                        before_entries = collect_entries(previous_document)
                        old_text = by_entry_id(before_entries).get(
                            str(target_entry_id), {}
                        ).get("text", "")
                        old_steps = parse_steps(old_text)
                        if len(old_steps) == 4 and len(steps) == 4:
                            for position in (1, 2, 3):
                                assertions.check(
                                    f"Round2 第 {position + 1} 步保持不变",
                                    squash(old_steps[position]) == squash(steps[position]),
                                    f"old={old_steps[position]!r} new={steps[position]!r}",
                                )
                        else:
                            assertions.check(
                                "Round2 可比较四步",
                                False,
                                f"old={old_steps} new={steps}",
                            )
                        changes = diff_entries(
                            before_entries,
                            entries,
                            exclude_entry_ids={str(target_entry_id)},
                            exclude_locations={"s11"},
                        )
                        assertions.check(
                            "Round2 非目标条目内容不变",
                            not changes,
                            json.dumps(changes[:8], ensure_ascii=False),
                        )
                        assertions.check(
                            "非目标零漂移（Round2 逐字）",
                            not changes,
                            json.dumps(changes[:8], ensure_ascii=False),
                        )
                    round_report["main_flow_entry_id"] = current_id
                    round_report["main_flow_text"] = text
                    round_report["steps"] = steps

                elif index == 3:
                    rules_before = (
                        len(field_texts(find_field_block(
                            find_feature_block(previous_document, FEATURE_SLUG)[1],
                            "rules",
                        )))
                        if previous_document is not None
                        and find_feature_block(previous_document, FEATURE_SLUG) is not None
                        else 0
                    )
                    assertions.check(
                        "Round3 rules 条目数 +1",
                        len(rule_texts) == rules_before + 1,
                        f"before={rules_before} after={len(rule_texts)} texts={rule_texts}",
                    )
                    joined = squash("\n".join(rule_texts))
                    assertions.check("Round3 rules 含 7天", "7天" in joined, joined)
                    assertions.check("Round3 rules 含 删除", "删除" in joined, joined)
                    current = ids.get(str(target_entry_id))
                    vs = (
                        by_entry_id(collect_entries(previous_document)).get(str(target_entry_id))
                        if previous_document is not None
                        else None
                    )
                    assertions.check(
                        "Round3 F 正文逐字未变",
                        current is not None and vs is not None and current["text"] == vs["text"],
                        f"before={(vs or {}).get('text')} after={(current or {}).get('text')}",
                    )
                    assertions.check(
                        "目标零丢失（Round3 F 未丢）",
                        current is not None,
                        f"F={target_entry_id}",
                    )
                    if previous_document is not None:
                        before_entries = collect_entries(previous_document)
                        rules_location = next(
                            (
                                entry["location"]
                                for entry in entries
                                if entry["field"] == "rules"
                            ),
                            "",
                        )
                        changes = diff_entries(
                            before_entries,
                            entries,
                            exclude_locations={rules_location, "s11"},
                        )
                        assertions.check(
                            "Round3 非 rules 条目内容不变",
                            not changes,
                            json.dumps(changes[:8], ensure_ascii=False),
                        )
                        assertions.check(
                            "非目标零漂移（Round3 逐字）",
                            not changes,
                            json.dumps(changes[:8], ensure_ascii=False),
                        )
                    round_report["main_flow_entry_id"] = target_entry_id
                    round_report["main_flow_text"] = (
                        ids.get(str(target_entry_id), {}).get("text")
                    )
                    round_report["rules_texts"] = rule_texts

                elif index == 4:
                    current_id = (
                        str(main_block["entries"][0].get("entry_id"))
                        if main_block is not None and main_block.get("entries")
                        else None
                    )
                    assertions.check(
                        "Round4 main_flow 仍只有 1 条",
                        len(main_texts) == 1,
                        f"count={len(main_texts)} texts={main_texts}",
                    )
                    assertions.check(
                        "Round4 F entry_id 不变",
                        current_id == target_entry_id,
                        f"before={target_entry_id} after={current_id}",
                    )
                    target_replaces = [
                        item
                        for item in proposals
                        if item.get("proposal_id") in accepted
                        and item.get("op") == "replace"
                        and str(item.get("entry_id")) == str(target_entry_id)
                    ]
                    assertions.check(
                        "Round4 目标 F 的 replace 被默认勾选",
                        len(target_replaces) == 1
                        and target_replaces[0].get("requires_review") is False,
                        json.dumps(target_replaces, ensure_ascii=False),
                    )
                    assertions.check(
                        "目标零丢失（Round4 F replace 生效）",
                        ids.get(str(target_entry_id)) is not None
                        and "Excel（.xlsx）" in squash(
                            ids.get(str(target_entry_id), {}).get("text", "")
                        ),
                        f"F={target_entry_id}",
                    )
                    text = main_texts[0] if main_texts else ""
                    compact = squash(text)
                    assertions.check("Round4 含 Excel（.xlsx）", "Excel（.xlsx）" in compact, text)
                    assertions.check("Round4 仍含 30天", "30天" in compact, text)
                    assertions.check("Round4 旧「生成xlsx」不残留", "生成xlsx" not in compact, text)
                    steps = parse_steps(text)
                    assertions.check("Round4 仍四步", len(steps) == 4, f"{steps}")
                    markers = re.findall(r"(?<!\d)([1-9])\s*[.、)]", text)
                    assertions.check(
                        "Round4 没有拼接重复的步骤编号",
                        len(markers) == len(set(markers)),
                        f"markers={markers}",
                    )
                    before_entries = (
                        collect_entries(previous_document) if previous_document is not None else []
                    )
                    if previous_document is not None:
                        changes = diff_entries(
                            before_entries,
                            entries,
                            exclude_entry_ids={str(target_entry_id)},
                            exclude_locations={"s11"},
                        )
                        assertions.check(
                            "Round4 非目标条目内容不变",
                            not changes,
                            json.dumps(changes[:8], ensure_ascii=False),
                        )
                        assertions.check(
                            "非目标零漂移（Round4 逐字）",
                            not changes,
                            json.dumps(changes[:8], ensure_ascii=False),
                        )
                        old_steps = parse_steps(
                            by_entry_id(before_entries)
                            .get(str(target_entry_id), {})
                            .get("text", "")
                        )
                        if len(old_steps) == 4 and len(steps) == 4:
                            for position in (0, 1, 3):
                                assertions.check(
                                    f"Round4 第 {position + 1} 步保持不变",
                                    squash(old_steps[position]) == squash(steps[position]),
                                    f"old={old_steps[position]!r} new={steps[position]!r}",
                                )
                    round_report["main_flow_entry_id"] = current_id
                    round_report["main_flow_text"] = text
                    round_report["steps"] = steps

                round_report["version_after"] = version_no
                round_report["document"] = document
                round_report["assertions"] = assertions.items
                failed = assertions.failed()
                round_report["assertion_failed"] = failed
                print(
                    f"[round {index}] assertions "
                    f"{len(assertions.items) - len(failed)}/{len(assertions.items)} passed",
                    flush=True,
                )
                if target_entry_id is not None:
                    annotate_hits(report["rounds"], target_entry_id)
                previous_document = document
                previous_version = version_no
                write_json(output, report, secrets)
                if failed:
                    report["status"] = "failed"
                    report["errors"].append(
                        {
                            "round": index,
                            "stage": "assertions",
                            "failed": failed,
                        }
                    )

            global_assertions = Assertions()
            skipped_all = [
                item
                for round_report in report["rounds"]
                for item in (round_report.get("selection") or {}).get("skipped") or []
            ]
            unmentioned_skipped = [
                item
                for item in skipped_all
                if item["op"] in {"replace", "remove"}
                and "这轮输入没有提到" in (item.get("warning") or "")
            ]
            accepted_unmentioned: list[dict[str, Any]] = []
            for round_report in report["rounds"]:
                accepted_ids = set(
                    (round_report.get("selection") or {}).get("accepted") or []
                )
                for item in (round_report.get("draft") or {}).get("proposals") or []:
                    if (
                        item.get("proposal_id") in accepted_ids
                        and item.get("op") in {"replace", "remove"}
                        and is_unmentioned_review(item)
                    ):
                        accepted_unmentioned.append(item)
            review_hits_all = [
                item
                for round_report in report["rounds"]
                for item in (round_report.get("draft") or {}).get("proposals") or []
                if item.get("requires_review") is True
            ]
            review_missed = [
                item
                for item in review_hits_all
                if str(item.get("proposal_id")) not in {
                    str(skipped.get("proposal_id")) for skipped in skipped_all
                }
            ]
            global_assertions.check(
                "requires_review 提案若出现则全部被跳过",
                not review_missed,
                json.dumps(
                    {
                        "hits": len(review_hits_all),
                        "missed": review_missed[:5],
                        "note": "本轮无此类提案" if not review_hits_all else "",
                    },
                    ensure_ascii=False,
                ),
            )
            global_assertions.check(
                "跳过原因正确",
                all(item.get("reasons") for item in skipped_all),
                json.dumps(
                    [item for item in skipped_all if not item.get("reasons")],
                    ensure_ascii=False,
                ),
            )
            unmentioned_candidates = [
                item
                for round_report in report["rounds"]
                for item in (round_report.get("draft") or {}).get("proposals") or []
                if item.get("op") in {"replace", "remove"}
                and is_unmentioned_review(item)
            ]
            unmentioned_missed = [
                item
                for item in unmentioned_candidates
                if str(item.get("proposal_id")) not in {
                    str(skipped.get("proposal_id")) for skipped in skipped_all
                }
            ]
            global_assertions.check(
                "未提及目标的 replace/remove 若出现则必须被跳过",
                not unmentioned_missed,
                json.dumps(
                    {
                        "hits": len(unmentioned_candidates),
                        "missed": unmentioned_missed[:5],
                        "note": "本轮无此类提案" if not unmentioned_candidates else "",
                    },
                    ensure_ascii=False,
                ),
            )
            global_assertions.check(
                "未提及目标的改/删没有被默认勾选",
                not accepted_unmentioned,
                json.dumps(accepted_unmentioned[:5], ensure_ascii=False),
            )
            scope_adds: list[dict[str, Any]] = []
            batch_adds: list[dict[str, Any]] = []
            for round_report in report["rounds"]:
                for item in (round_report.get("draft") or {}).get("proposals") or []:
                    categories = review_categories(item)
                    if item.get("op") == "add" and "outside_round_scope" in categories:
                        scope_adds.append(item)
                    if item.get("op") == "add" and "batch_add_duplicate" in categories:
                        batch_adds.append(item)
            skipped_ids = {
                str(item.get("proposal_id")) for item in skipped_all
            }
            scope_adds_missed = [
                item for item in scope_adds
                if str(item.get("proposal_id")) not in skipped_ids
                or "outside_round_scope" not in review_categories(item)
            ]
            batch_adds_missed = [
                item for item in batch_adds
                if str(item.get("proposal_id")) not in skipped_ids
                or "batch_add_duplicate" not in review_categories(item)
            ]
            global_assertions.check(
                "越界 add 若出现则必须被标且跳过",
                not scope_adds_missed,
                json.dumps(
                    {
                        "hits": len(scope_adds),
                        "missed": scope_adds_missed[:5],
                        "note": "本轮无此类提案" if not scope_adds else "",
                    },
                    ensure_ascii=False,
                ),
            )
            global_assertions.check(
                "批内同文 add 若出现则全部跳过且原因正确",
                not batch_adds_missed,
                json.dumps(
                    {"hits": len(batch_adds), "missed": batch_adds_missed[:5]},
                    ensure_ascii=False,
                ),
            )
            fallback_total = sum(
                len((round_report.get("origin_classification") or {}).get("system_fallbacks") or [])
                for round_report in report["rounds"]
            )
            global_assertions.check(
                "系统兜底 0 次",
                fallback_total == 0,
                f"fallbacks={fallback_total}",
            )
            report["global_assertions"] = global_assertions.items
            all_assertions = [
                item
                for round_report in report["rounds"]
                for item in round_report.get("assertions") or []
            ] + global_assertions.items
            failed_assertions = [item for item in all_assertions if not item["ok"]]
            report["summary"] = {
                "rounds_completed": len(report["rounds"]),
                "assertions_total": len(all_assertions),
                "assertions_passed": len(all_assertions) - len(failed_assertions),
                "assertions_failed": len(failed_assertions),
                "model_calls_integration": sum(
                    len(round_report.get("model_calls") or [])
                    for round_report in report["rounds"]
                ),
                "raw_op_distribution": {
                    round_report["index"]: round_report.get("raw_op_distribution")
                    for round_report in report["rounds"]
                },
                "final_call_op_distribution": {
                    round_report["index"]: round_report.get(
                        "final_call_op_distribution"
                    )
                    for round_report in report["rounds"]
                },
                "model_spontaneous_replace_hits_F": {
                    round_report["index"]: sum(
                        1
                        for call in round_report.get("model_calls") or []
                        for item in call.get("raw_proposals") or []
                        if item.get("hits_F") and item.get("op") == "replace"
                    )
                    for round_report in report["rounds"]
                },
                "system_fallbacks": {
                    round_report["index"]: (
                        (round_report.get("origin_classification") or {}).get(
                            "system_fallbacks"
                        )
                        or []
                    )
                    for round_report in report["rounds"]
                },
                "system_fallbacks_total": fallback_total,
                "review_hits_total": sum(
                    (round_report.get("review_stats") or {}).get("hits", 0)
                    for round_report in report["rounds"]
                ),
                "review_category_totals": dict(
                    sorted(
                        Counter(
                            category
                            for round_report in report["rounds"]
                            for category, count in (
                                (round_report.get("review_stats") or {}).get("categories") or {}
                            ).items()
                            for _ in range(count)
                        ).items()
                    )
                ),
                "scope_add_hits": len(scope_adds),
                "batch_add_hits": len(batch_adds),
                "skipped_total": len(skipped_all),
                "unmentioned_skipped_total": len(unmentioned_skipped),
                "global_assertions": global_assertions.items,
                "target_entry_id": target_entry_id,
                "final_version": previous_version,
                "schema": scratch.schema,
            }
            if not failed_assertions and report.get("status") != "failed":
                report["status"] = "done"
            elif report.get("status") == "running":
                report["status"] = "failed"
            report["finished_at"] = now_iso()
            write_json(output, report, secrets)
            print(
                f"[done] status={report['status']} "
                f"assertions={report['summary']['assertions_passed']}/"
                f"{report['summary']['assertions_total']}",
                flush=True,
            )
            return 0 if report["status"] == "done" else 1
    except Exception as error:  # noqa: BLE001
        report["status"] = "failed"
        report["finished_at"] = now_iso()
        report["errors"].append(
            {
                "stage": "exception",
                "type": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exc(),
            }
        )
        write_json(output, report, secrets)
        print(f"[error] {type(error).__name__}: {error}", flush=True)
        return 1
    finally:
        stop_uvicorn(process)
        write_json(output, report, secrets)


def load_acceptance_env() -> dict[str, str]:
    env = load_env_files(BACKEND_ROOT)
    database = test_database(env)
    env["PMSTUDIO_DATABASE_URL"] = database
    env["PMSTUDIO_DIRECT_DATABASE_URL"] = database
    return env


async def main_async(args: argparse.Namespace) -> int:
    env = load_acceptance_env()
    username = env.get("PMSTUDIO_FIRST_ADMIN_USER", "")
    password = env.get("PMSTUDIO_FIRST_ADMIN_PASSWORD", "")
    if not username or not password:
        print("缺首管理员凭据（backend/.env.ops）", file=sys.stderr)
        return 2
    database = test_database(env)
    scratch = make_scratch(
        prefix=PREFIX,
        env=env,
        admin_user=username,
        admin_password=password,
    )
    secrets = secrets_from(env, [scratch.app_url, scratch.direct_url, database])
    output = Path(args.output)
    print(f"[scratch] schema={scratch.schema}", flush=True)
    print(f"[scratch] app={redact_dsn(scratch.app_url)}", flush=True)
    print(f"[scratch] direct={redact_dsn(scratch.direct_url)}", flush=True)
    print("[scratch] 不自动删除；清理命令见报告", flush=True)
    return await run_acceptance(
        env=env,
        scratch=scratch,
        port=args.port,
        output=output,
        secrets=secrets,
        http_timeout=args.http_timeout,
        round_timeout=args.round_timeout,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="真实模型四轮精确写入验收")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument(
        "--output", default="/tmp/opencode/exact-write-acceptance.json"
    )
    parser.add_argument("--http-timeout", type=float, default=900.0)
    parser.add_argument("--round-timeout", type=float, default=1800.0)
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
