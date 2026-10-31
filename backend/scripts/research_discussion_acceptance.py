"""S5 真机验收：项目级研究 + panel 讨论（真模型 / 真 HTTP / 真 PostgreSQL）。

它不跑单元测试，也不连接业务库：在 `127.0.0.1:55433/pmstudio_test` 上建一个
随机 schema（前缀 `rdq`），跑迁移 + finalize，然后起一个**真实 uvicorn 子进程**，
用真实首管理员、真实模型走完：

1. 新建项目 → `POST /projects/{id}/research`（真实失败例主题）；
2. 读 `model_calls` / `tool_calls` / `explorations.payload` 做数据库侧复核；
3. `POST /projects/{id}/discussions` + `POST /discussions/{id}/run-round` 真跑一轮；
4. 用 `agent-browser` 登录、打开项目，截「研究」与「讨论」页签。

设计要点（与 `exact_write_acceptance.py` 同纪律）：

- **两个 DSN 显式覆盖**到本地 testdb，拒绝业务库 `pmstudio`；
- 模型配置仍读 `backend/.env`（不打印 key）；
- `PMSTUDIO_TRACE_MODEL=1` 只放进子进程环境，用于从 `model_calls` 读原始回复；
- schema **不自动删除**：输出名字与打码后的 DSN，留给浏览器复核与人工清理；
- 失败照实写进 JSON / Markdown，不重试到绿、不美化。

用法（在 backend 下）：

    .venv/bin/python scripts/research_discussion_acceptance.py --port 8021 --web-port 5176

退出码：0 全部断言通过；1 有断言/调用失败；2 配置或启动失败。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import traceback
from collections import Counter
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
FRONTEND_ROOT = REPO_ROOT / "frontend"
sys.path.insert(0, str(BACKEND_ROOT / "src"))
sys.path.insert(0, str(BACKEND_ROOT))

from scripts.pg_scratch import (  # noqa: E402
    APP_URL_ENV,
    DIRECT_URL_ENV,
    load_env_files,
    make_scratch,
)

from pmstudio.domain.shared.writing import normalize  # noqa: E402

#: 验收库：只认本机 testdb。业务库 `pmstudio` 一律拒绝（见 `_test_database`）。
DEFAULT_TEST_DB = "postgresql://postgres:postgres@127.0.0.1:55433/pmstudio_test"
PREFIX = "rdq"

TOPIC = (
    "自动完成任务产生的中间文件与日志：分页算法（偏移量 / 游标 / 基于 ID）该如何选择，"
    "才兼顾实现复杂度与查询性能？前端该用无限滚动、传统页码还是虚拟列表？"
)
DISCUSSION_TOPIC = (
    "分页算法与前端列表交互：偏移量 / 游标 / 基于 ID 如何选，"
    "无限滚动 / 页码 / 虚拟列表如何取舍？"
)
DISCUSSION_ROLES = ("dlg.challenger", "dlg.user_voice", "dlg.analogist")

JSON_OUT = Path("/tmp/opencode/research-discussion-acceptance.json")
REPORT_OUT = Path("/tmp/opencode/pmstudio-s5-report.md")
RUN_DIR = Path("/tmp/opencode")
RESEARCH_SHOT = RUN_DIR / "rdq-research.png"
DISCUSSION_SHOT = RUN_DIR / "rdq-discussion.png"

HTTP_TIMEOUT = 900.0
NOISE_PATTERNS = ("indeed", "cursor")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Assertions:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.items.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    def failed(self) -> list[dict[str, Any]]:
        return [item for item in self.items if not item["ok"]]


def redact_dsn(url: str) -> str:
    parts = urlsplit(url)
    if parts.password is None:
        return url
    user = parts.username or ""
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{user}:***@{host}{port}" if user else f"***@{host}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _test_database(env: dict[str, str]) -> str:
    value = str(env.get("PMSTUDIO_ACCEPTANCE_DB_URL") or DEFAULT_TEST_DB).strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise SystemExit(f"验收库必须是 postgresql://，现在是 {parsed.scheme!r}")
    if (parsed.path or "").rstrip("/") in {"", "/pmstudio"}:
        raise SystemExit("拒绝连接业务库 pmstudio；请用 pmstudio_test")
    if (parsed.hostname or "") not in {"127.0.0.1", "localhost"}:
        raise SystemExit(f"验收库必须是本机 testdb，现在 host={parsed.hostname!r}")
    return value


def secrets_from(env: dict[str, str]) -> list[str]:
    names = (
        "PMSTUDIO_API_KEY",
        "PMSTUDIO_TAVILY_API_KEY",
        "PMSTUDIO_FIRST_ADMIN_PASSWORD",
        "PMSTUDIO_OWNER_PASSWORD",
        "PMSTUDIO_APP_PASSWORD",
        "POSTGRES_PASSWORD",
    )
    values = [str(env.get(name) or "") for name in names]
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


def build_uvicorn_env(env: dict[str, str], scratch: Any) -> dict[str, str]:
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
        start_new_session=True,
    )
    process._pmstudio_log = log  # type: ignore[attr-defined]
    process._pmstudio_group = True  # type: ignore[attr-defined]
    return process


def start_vite(*, port: int, api_port: int, log_path: Path) -> subprocess.Popen[bytes]:
    env = {**os.environ}
    env.update(
        {
            "PMSTUDIO_WEB_PORT": str(port),
            "PMSTUDIO_API_TARGET": f"http://127.0.0.1:{api_port}",
        }
    )
    log = log_path.open("wb")
    process = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=str(FRONTEND_ROOT),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    process._pmstudio_log = log  # type: ignore[attr-defined]
    process._pmstudio_group = True  # type: ignore[attr-defined]
    return process


def stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        grouped = bool(getattr(process, "_pmstudio_group", False))
        try:
            if grouped:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                if grouped:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
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


def wait_healthy(port: int, *, timeout: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=5.0) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(f"http://127.0.0.1:{port}/api/health")
                if response.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
    return False


def wait_web(port: int, *, timeout: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=5.0) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(f"http://127.0.0.1:{port}/")
                if response.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
    return False


# —— 文本相似度 ——


def longest_common_substring(a: str, b: str) -> int:
    if not a or not b:
        return 0
    previous = [0] * (len(b) + 1)
    best = 0
    for left in a:
        current = [0] * (len(b) + 1)
        for index, right in enumerate(b, start=1):
            if left == right:
                current[index] = previous[index - 1] + 1
                if current[index] > best:
                    best = current[index]
        previous = current
    return best


def similarity(answer: str, topic: str) -> dict[str, Any]:
    norm_answer = normalize(answer)
    norm_topic = normalize(topic)
    return {
        "normalized_equal": norm_answer == norm_topic,
        "sequence_matcher_ratio": round(
            SequenceMatcher(None, norm_answer, norm_topic).ratio(), 4
        ),
        "longest_common_substring": longest_common_substring(norm_answer, norm_topic),
        "topic_length": len(norm_topic),
        "answer_length": len(norm_answer),
        "normalized_containment": norm_topic in norm_answer,
    }


# —— DB 侧复核 ——


def read_model_calls(dsn: str, trace_id: str) -> list[dict[str, Any]]:
    with psycopg.connect(dsn, autocommit=True) as connection:
        rows = connection.execute(
            "SELECT id, purpose, attempt, ok, error, duration_ms, reply_text"
            " FROM model_calls WHERE round_id = %s ORDER BY id",
            (trace_id,),
        ).fetchall()
    calls: list[dict[str, Any]] = []
    for row in rows:
        calls.append(
            {
                "id": row[0],
                "purpose": row[1],
                "attempt": row[2],
                "ok": row[3],
                "error": row[4],
                "duration_ms": row[5],
                "reply_text": row[6] or "",
                "reply_excerpt": (row[6] or "")[:1200],
            }
        )
    return calls


def read_tool_calls(dsn: str, trace_id: str) -> list[dict[str, Any]]:
    with psycopg.connect(dsn, autocommit=True) as connection:
        rows = connection.execute(
            "SELECT id, tool_id, ok, error, duration_ms, result_note"
            " FROM tool_calls WHERE round_id = %s ORDER BY id",
            (trace_id,),
        ).fetchall()
    return [
        {
            "id": row[0],
            "tool_id": row[1],
            "ok": row[2],
            "error": row[3],
            "duration_ms": row[4],
            "result_note": (row[5] or "")[:200],
        }
        for row in rows
    ]


def _walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        found: list[str] = []
        for item in value.values():
            found.extend(_walk_strings(item))
        return found
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(_walk_strings(item))
        return found
    return []


def raw_reply_contains(calls: list[dict[str, Any]], answer: str) -> bool:
    wanted = normalize(answer)
    if not wanted:
        return False
    for call in calls:
        text = call.get("reply_text") or call.get("reply_excerpt") or ""
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        candidates = _walk_strings(parsed) if parsed is not None else [text]
        for candidate in candidates:
            if wanted in normalize(candidate):
                return True
        if wanted in normalize(text):
            return True
    return False


def read_research_payload(dsn: str, exploration_id: str) -> dict[str, Any] | None:
    with psycopg.connect(dsn, autocommit=True) as connection:
        row = connection.execute(
            "SELECT payload FROM explorations WHERE exploration_id = %s",
            (exploration_id,),
        ).fetchone()
    if row is None:
        return None
    payload = row[0]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload


def material_content_lengths(
    dsn: str, project_id: str, material_ids: list[str]
) -> dict[str, int]:
    if not material_ids:
        return {}
    with psycopg.connect(dsn, autocommit=True) as connection:
        rows = connection.execute(
            "SELECT material_id, bytes_len FROM materials"
            " WHERE project_id = %s AND material_id = ANY(%s)",
            (project_id, material_ids),
        ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def parse_tool_attempts(notes: list[str]) -> int | None:
    for note in notes:
        match = re.search(r"工具调用\s*(\d+)\s*次", note)
        if match:
            return int(match.group(1))
    return None


# —— 浏览器 ——


class Browser:
    def __init__(self, *, session: str) -> None:
        self.session = session

    def _run(self, args: list[str], *, stdin: str | None = None, timeout: float = 120.0) -> str:
        completed = subprocess.run(
            ["agent-browser", "--session", self.session, *args],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"agent-browser {' '.join(args)} 失败："
                f"{(completed.stderr or completed.stdout).strip()[:400]}"
            )
        return completed.stdout.strip()

    def login(self, url: str, username: str, password: str) -> None:
        self._run(["open", url])
        self._run(
            [
                "auth",
                "save",
                "rdq-s5",
                "--url",
                url,
                "--username",
                username,
                "--password-stdin",
                "--username-selector",
                "#auth-username",
                "--password-selector",
                "#auth-password",
                "--submit-selector",
                "button[type=submit]",
            ],
            stdin=password + "\n",
        )
        self._run(["auth", "login", "rdq-s5"])
        self._ensure_loop()

    def _ensure_loop(self) -> None:
        """空项目的闭环栏默认折叠；先点开 rail，再等页签出现。"""

        script = (
            "(() => {"
            "if (document.querySelector('.loop__tabs')) return 'tabs';"
            "const expand = document.querySelector('.loop > button');"
            "if (expand) { expand.click(); return 'clicked'; }"
            "return 'wait';"
            "})()"
        )
        for _ in range(80):
            result = self._run(["eval", script])
            if "tabs" in result:
                return
            time.sleep(0.5)
        raise RuntimeError("闭环栏页签没有出现（展开失败）")

    def open_project(self, url: str) -> None:
        self._run(["open", url])
        self._ensure_loop()

    def click_tab(self, label: str) -> None:
        script = (
            "(() => {"
            "const target = [...document.querySelectorAll('[role=tab]')]"
            f".find((el) => el.textContent.trim() === {json.dumps(label)});"
            "if (!target) return 'missing';"
            "target.click(); return 'clicked';"
            "})()"
        )
        result = self._run(["eval", script])
        if "clicked" not in result:
            raise RuntimeError(f"找不到页签：{label}（eval 返回 {result[:200]!r}）")
        time.sleep(1.0)

    def page_text(self) -> str:
        return self._run(["get", "text", ".loop__scroll"])

    def scroll_to_text(self, probe: str) -> bool:
        """把包含 probe 的第一个 <p> 滚到闭环栏顶部（截图取景用）。"""

        if not probe:
            return False
        script = (
            "(() => {"
            "const sc = document.querySelector('.loop__scroll');"
            "if (!sc) return 'no-scroll';"
            "const probe = " + json.dumps(probe) + ";"
            "const target = [...sc.querySelectorAll('p')]"
            ".find((el) => (el.textContent || '').includes(probe));"
            "if (!target) return 'no-target';"
            "const top = target.getBoundingClientRect().top"
            " - sc.getBoundingClientRect().top + sc.scrollTop;"
            "sc.scrollTop = Math.max(0, top - 8);"
            "return 'scrolled';"
            "})()"
        )
        result = self._run(["eval", script])
        time.sleep(0.5)
        return "scrolled" in result

    def screenshot(self, path: Path) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._run(["screenshot", str(path)])
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"截图没有落盘：{path}")
        return str(path)

    def cleanup(self) -> None:
        try:
            self._run(["auth", "delete", "rdq-s5"])
        except Exception:  # noqa: BLE001
            pass
        try:
            self._run(["close", "--all"])
        except Exception:  # noqa: BLE001
            pass


# —— 报告 ——


def build_report(report: dict[str, Any]) -> str:
    assertions = report.get("assertions") or []
    lines = [
        "# PM Studio S5 真机验收报告：项目级研究 + panel 讨论",
        "",
        f"- 时间：{report.get('started_at', '')} → {report.get('finished_at', '')}",
        "- 分支：`feat/multi-round-exact-writes`",
        f"- 模型：`{report.get('model', '')}`（API base：`{report.get('api_base', '')}`）",
        f"- 验收 schema：`{report.get('db_schema', '')}`（**未删除**，留作复核）",
        f"- DSN（打码）：`{report.get('app_dsn_redacted', '')}`",
        f"- 后端 / 前端：`127.0.0.1:{report.get('api_port')}` / "
        f"`127.0.0.1:{report.get('web_port')}`",
        "- DSN 覆盖：显式改为 `127.0.0.1:55433/pmstudio_test`（拒绝业务库 `pmstudio`）",
        "- 子进程：`PMSTUDIO_TRACE_MODEL=1`；单次 HTTP 超时 900s",
        f"- 退出码：{report.get('exit_code', '（运行中）')}",
        f"- 父进程代理环境：{report.get('proxy_env_present')}"
        "（True 时产品 fetch 的 IP 钉定与 HTTPS 代理不兼容，见环境备注）",
        "",
        "## 断言总表",
        "",
        "| # | 断言 | 结果 | 证据 |",
        "|---|---|---|---|",
    ]
    for index, item in enumerate(assertions, start=1):
        mark = "PASS" if item["ok"] else "FAIL"
        detail = str(item.get("detail") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {index} | {item['name']} | **{mark}** | {detail} |")

    research = report.get("research") or {}
    lines += [
        "",
        "## 研究验收",
        "",
        f"- 项目：`{report.get('project_id', '')}`",
        f"- 研究 id：`{research.get('exploration_id', '')}`",
        f"- 状态：`{research.get('status')}`；"
        f"失败原因：{research.get('failure_reason') or '（无）'}",
        f"- 耗时：{research.get('elapsed_seconds')} 秒",
        f"- answer 长度：{research.get('answer_length')}；"
        f"归一化后与 topic 相同：{research.get('normalized_equal')}；"
        f"SequenceMatcher：{research.get('sequence_matcher_ratio')}；"
        f"最长公共子串：{research.get('longest_common_substring')}"
        f"（topic 归一化长度 {research.get('topic_length')}）",
        f"- answer_refs：{research.get('answer_refs')}；"
        f"全部可解析：{research.get('refs_resolvable')}",
        f"- 来源条数：{research.get('source_count')}；DB 中正文非空（已读/fetch 成功）"
        f"来源数：{research.get('read_source_count')}",
        f"- 模型调用（DB）：{research.get('model_call_counts')}；"
        f"工具调用（研究 notes）：{research.get('tool_attempts')}；"
        f"tool_calls 表行数：{research.get('tool_calls_rows')}",
        f"- 是否有「系统补读」备注：{research.get('has_system_fallback')}"
        f"（notes：{research.get('notes')}）",
        f"- DB 原始报文里能找回 answer：{research.get('answer_in_raw_reply')}",
        "- answer 是否复述主题："
        + ("**是**（机制应判 failed）" if research.get("normalized_equal") else "否"),
        "",
        "### 来源标题（如实列出）",
        "",
    ]
    for item in research.get("sources") or []:
        lines.append(
            f"- `{item.get('eid')}` {item.get('title')} — {item.get('url')}"
            f"（标题含 indeed：{item.get('title_has_indeed')}；"
            f"含 cursor：{item.get('title_has_cursor')}）"
        )
    if not research.get("sources"):
        lines.append("- （没有来源）")

    titles = research.get("sources") or []
    has_indeed = any(item.get("title_has_indeed") for item in titles)
    has_cursor = any(item.get("title_has_cursor") for item in titles)
    if has_indeed:
        lines.append(
            "- **词面噪声结论**：仍有 Indeed 类词面污染，见上面标题原文。"
        )
    else:
        lines.append("- **词面噪声结论**：本轮标题没有 Indeed。")
    if has_cursor:
        lines.append(
            "  含 cursor 的标题是分页主题词（不是 Indeed/CURSOR 产品噪声）；"
            "是否算噪声按原题口径由复核人判断。"
        )

    counts = research.get("model_call_counts") or {}
    if counts.get("research") and not counts.get("research_synthesis"):
        lines.append(
            "- **口径差异（不掩盖）**：需求写的是 purpose=research_loop/research_synthesis；"
            "本仓库综合调用实际落库 purpose 是 `research`"
            "（`application/agents/definitions.py` 的 `RESEARCHER.purpose`）。"
            "本次按 DB 实际值核对，没有伪造 `research_synthesis` 行。"
        )
    if research.get("tool_calls_rows") == 0:
        lines.append(
            "- **工具调用口径**：研究工具箱不写 `tool_calls` 表（该表用于轮次联网检索），"
            "研究侧次数取自 notes 的「工具调用 N 次」；"
            f"本趟 notes={research.get('tool_attempts')}、tool_calls 行=0，不矛盾。"
        )

    lines += [
        "",
        "### answer 原文",
        "",
        "```text",
        research.get("answer") or "（空）",
        "```",
    ]

    discussion = report.get("discussion") or {}
    lines += [
        "",
        "## 讨论验收",
        "",
        f"- thread_id：`{discussion.get('thread_id', '')}`",
        f"- 参与者：{discussion.get('participants')}",
        f"- run-round 状态码：{discussion.get('run_status_code')}；"
        f"错误：{discussion.get('run_error') or '（无）'}",
        f"- 本轮 turns：{discussion.get('turn_count')}"
        f"（参与者数 {discussion.get('participant_count')}）",
        f"- 耗时：{discussion.get('elapsed_seconds')} 秒；"
        f"模型调用（DB）：{discussion.get('model_call_counts')}；"
        f"单次最长：{discussion.get('max_call_duration_ms')} ms",
        "",
        "### 第一轮发言（如实摘录，每条截断 500 字）",
        "",
    ]
    for turn in discussion.get("turns") or []:
        text = str(turn.get("text") or "")
        lines.append(f"- **{turn.get('role')}**（round {turn.get('round')}）：{text[:500]}")
    if not discussion.get("turns"):
        lines.append("- （没有 turn）")

    lines += [
        "",
        "## 浏览器截图",
        "",
        f"- 研究页签：`{report.get('research_screenshot')}`"
        f"（页面文本含 answer：{report.get('research_page_has_answer')}；"
        f"已滚到 answer：{report.get('research_scrolled_to_answer')}）",
        f"- 讨论页签：`{report.get('discussion_screenshot')}`"
        f"（页面文本含第一轮发言：{report.get('discussion_page_has_turn')}；"
        f"已滚到线程标题：{report.get('discussion_scrolled_to_turn')}）",
        "",
        "## 环境备注（影响本次结论的边界）",
        "",
        "- 首轮在继承 WSL `HTTPS_PROXY` 的父进程里跑：搜索到 5 条，"
        "5/5 fetch 全部 ConnectError，研究按机制判 failed。",
        "  单测 `HttpxWebFetcher` 对 example.com/baidu.com 复现："
        "带代理 2/2 失败，去掉代理 2/2 成功。",
        "  原因是 fetcher 把已校验 IP 钉进 URL（DNS rebinding 护栏），"
        "与 HTTPS 代理的 CONNECT 语义冲突；",
        "  这不是 S5 代码路径的问题，但**在本机代理环境里就是会失败**。",
        "- 首轮 JSON / 报告另存：`/tmp/opencode/rdq-run1-proxy-env.json` / "
        "`rdq-run1-proxy-env-report.md`。",
        f"- 本次正式运行父进程是否带代理：{report.get('proxy_env_present')}。",
        "",
        "## 机制保证 vs 模型残余（不美化）",
        "",
        "**机制保证（代码路径强制，不依赖模型自觉）**",
        "",
        "- answer 非空、不能与 topic 归一后相同、`answer_refs` 必须能在本趟来源里解析：",
        "  `project_research._synthesize()` 两轮校验；不过就整趟 failed，不会落一份坏报告。",
        "- 至少读到 1 篇正文：`execute()` 在 `toolbox.read_count == 0` "
        "且兜底补读仍为 0 时直接 failed。",
        "- 报告里的来源就是 `toolbox.read_sources`（抓到正文的那些），不是搜索结果全量。",
        "- 讨论一轮的 turns 数 == 本轮在场参与者数：`RunPanelRound` 对 remaining 逐个 gather，",
        "  任一发言失败即抛错，不会返回 200 的半轮。",
        "- 线程 id、状态码、轮次落库都是用例层与 DB 事务保证。",
        "",
        "**模型残余（断言通过也仍然由模型决定）**",
        "",
        "- answer 是否真的回答了分页算法/前端列表的取舍，而不是换个说法复述问题：",
        "  机制只拦住「归一化后完全相同」，语义级复述拦不住。",
        "- 来源标题与检索词面噪声（Indeed / CURSOR 之类）是检索与模型选词的结果，机制不净化标题。",
        "- 模型是否主动 fetch 正文、是否触发系统补读、调用次数多少，都是模型的工具使用行为。",
        "- 讨论发言的论证质量、角色区分度、综合质量，均为模型残余。",
        "",
    ]

    failure = report.get("error")
    if failure:
        lines += ["## 运行期错误（原文）", "", "```text", str(failure)[:8000], "```", ""]

    lines += [
        "## 复核与清理",
        "",
        f"- schema：`{report.get('db_schema', '')}`（保留）",
        f"- 手工删除：`DROP SCHEMA IF EXISTS \"{report.get('db_schema', '')}\" CASCADE;`",
        f"- JSON 证据：`{JSON_OUT}`",
        f"- 后端日志：`{report.get('uvicorn_log')}`",
        f"- 前端日志：`{report.get('vite_log')}`",
        "",
    ]
    return "\n".join(lines)


# —— 主流程 ——


def http_client(api_port: int) -> httpx.Client:
    return httpx.Client(
        base_url=f"http://127.0.0.1:{api_port}/api",
        timeout=httpx.Timeout(HTTP_TIMEOUT, connect=10.0),
        follow_redirects=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8021)
    parser.add_argument("--web-port", type=int, default=5176)
    args = parser.parse_args()

    report: dict[str, Any] = {
        "started_at": now_iso(),
        "api_port": args.port,
        "web_port": args.web_port,
        "topic": TOPIC,
        "discussion_topic": DISCUSSION_TOPIC,
        "discussion_roles": list(DISCUSSION_ROLES),
        "assertions": [],
        "uvicorn_log": str(RUN_DIR / "rdq-uvicorn.log"),
        "vite_log": str(RUN_DIR / "rdq-vite.log"),
        "proxy_env_present": any(
            os.environ.get(name)
            for name in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
            )
        ),
    }
    assertions = Assertions()
    report["assertions"] = assertions.items

    env = load_env_files()
    secrets = secrets_from(env)
    report["model"] = env.get("PMSTUDIO_MODEL", "")
    report["api_base"] = env.get("PMSTUDIO_API_BASE", "(官方默认)")
    report["tavily_configured"] = bool(env.get("PMSTUDIO_TAVILY_API_KEY"))

    uvicorn: subprocess.Popen[bytes] | None = None
    vite: subprocess.Popen[bytes] | None = None
    browser: Browser | None = None
    exit_code = 1
    try:
        test_db = _test_database(env)
        scratch_env = dict(env)
        scratch_env[APP_URL_ENV] = test_db
        scratch_env[DIRECT_URL_ENV] = test_db
        report["test_db_redacted"] = redact_dsn(test_db)

        print(f"[db] 建临时 schema（prefix={PREFIX}）…", flush=True)
        scratch = make_scratch(prefix=PREFIX, env=scratch_env)
        report["db_schema"] = scratch.schema
        report["app_dsn_redacted"] = redact_dsn(scratch.app_url)
        report["direct_dsn_redacted"] = redact_dsn(scratch.direct_url)
        print(f"[db] schema={scratch.schema}", flush=True)
        print(f"[db] app DSN={redact_dsn(scratch.app_url)}", flush=True)
        assertions.check(
            "schema 建在 pmstudio_test（非业务库）",
            True,
            f"{scratch.schema} @ {report['test_db_redacted']}",
        )

        uvicorn_env = build_uvicorn_env(env, scratch)
        uvicorn = start_uvicorn(
            port=args.port, env=uvicorn_env, log_path=Path(report["uvicorn_log"])
        )
        print(f"[api] uvicorn 启动中 :{args.port} …", flush=True)
        healthy = wait_healthy(args.port)
        assertions.check("uvicorn /api/health 就绪", healthy, f"port={args.port}")
        if not healthy:
            raise RuntimeError("uvicorn 未在 90 秒内就绪，见日志")

        username = env.get("PMSTUDIO_FIRST_ADMIN_USER", "")
        password = env.get("PMSTUDIO_FIRST_ADMIN_PASSWORD", "")
        if not username or not password:
            raise RuntimeError("backend/.env.ops 缺首管理员账号或密码")
        report["admin_username"] = username

        with http_client(args.port) as client:
            login = client.post(
                "/auth/login", json={"username": username, "password": password}
            )
            assertions.check(
                "首管理员登录 200",
                login.status_code == 200,
                f"status={login.status_code}",
            )
            if login.status_code != 200:
                raise RuntimeError(f"登录失败：{login.status_code} {login.text[:300]}")

            created = client.post(
                "/projects",
                json={"title": "S5 分页研究与讨论验收", "template_id": "standard"},
            )
            project_id = ""
            if created.status_code == 201:
                project_id = str((created.json() or {}).get("project_id") or "")
            report["project_id"] = project_id
            assertions.check(
                "新建项目 201 且有 project_id",
                created.status_code == 201 and bool(project_id),
                f"status={created.status_code} project_id={project_id}",
            )
            if not project_id:
                raise RuntimeError(f"建项目失败：{created.status_code} {created.text[:300]}")

            # —— 研究 ——
            print("[research] 发起真实研究（可能数分钟）…", flush=True)
            started = time.monotonic()
            research_response = client.post(
                f"/projects/{project_id}/research", json={"topic": TOPIC}
            )
            elapsed = round(time.monotonic() - started, 2)
            research_view: dict[str, Any] = {}
            if research_response.status_code == 200:
                research_view = research_response.json() or {}
            research = research_view.get("research") or {}
            answer = str(research.get("answer") or "")
            source_items = list(research.get("sources") or [])
            source_eids = {str(item.get("eid")) for item in source_items}
            answer_refs = [str(ref) for ref in (research.get("answer_refs") or [])]
            notes = [str(note) for note in (research.get("notes") or [])]
            data = {
                "exploration_id": research_view.get("exploration_id"),
                "status": research_view.get("status"),
                "failure_reason": research_view.get("failure_reason"),
                "http_status": research_response.status_code,
                "elapsed_seconds": elapsed,
                "answer": answer,
                "answer_length": len(normalize(answer)),
                "answer_refs": answer_refs,
                "refs_resolvable": bool(answer_refs)
                and all(ref in source_eids for ref in answer_refs),
                "sources": [
                    {
                        "eid": item.get("eid"),
                        "title": item.get("title"),
                        "url": item.get("url"),
                        "excerpt": (item.get("excerpt") or "")[:200],
                        "title_has_indeed": "indeed" in str(item.get("title") or "").casefold(),
                        "title_has_cursor": "cursor" in str(item.get("title") or "").casefold(),
                    }
                    for item in source_items
                ],
                "source_count": len(source_items),
                "notes": notes,
                "has_system_fallback": any("系统补读" in note for note in notes),
            }
            if answer and TOPIC:
                data.update(similarity(answer, TOPIC))
            else:
                data.update(
                    {
                        "normalized_equal": False,
                        "sequence_matcher_ratio": None,
                        "longest_common_substring": None,
                        "topic_length": len(normalize(TOPIC)),
                        "answer_length": 0,
                        "normalized_containment": False,
                    }
                )

            exploration_id = str(data.get("exploration_id") or "")
            if exploration_id:
                payload = read_research_payload(scratch.direct_url, exploration_id)
                payload_sources = (
                    ((payload or {}).get("research") or {}).get("sources") or []
                )
                material_ids = [
                    str(item.get("material_id"))
                    for item in payload_sources
                    if item.get("material_id")
                ]
                lengths = material_content_lengths(
                    scratch.direct_url, project_id, material_ids
                )
                read_ids = [mid for mid in material_ids if lengths.get(mid, 0) > 0]
                data["read_source_count"] = len(read_ids)
                data["source_material_ids"] = material_ids
                data["source_content_lengths"] = lengths

                calls = read_model_calls(scratch.direct_url, exploration_id)
                tool_rows = read_tool_calls(scratch.direct_url, exploration_id)
                data["model_calls"] = calls
                data["model_call_counts"] = dict(
                    sorted(Counter(str(call["purpose"]) for call in calls).items())
                )
                data["tool_calls_rows"] = len(tool_rows)
                data["tool_calls_detail"] = tool_rows
                data["answer_in_raw_reply"] = raw_reply_contains(calls, answer)
            else:
                data["read_source_count"] = 0
                data["model_call_counts"] = {}
                data["tool_calls_rows"] = 0
                data["answer_in_raw_reply"] = False
            data["tool_attempts"] = parse_tool_attempts(notes)
            report["research"] = data

            assertions.check(
                "研究 HTTP 200",
                research_response.status_code == 200,
                f"status={research_response.status_code}",
            )
            assertions.check(
                "研究状态 done",
                data.get("status") == "done",
                f"status={data.get('status')} failure={data.get('failure_reason')}",
            )
            assertions.check(
                "answer 非空",
                bool(answer.strip()),
                f"len={len(answer)}",
            )
            assertions.check(
                "answer 与 topic 归一后不相同",
                bool(answer.strip()) and not data.get("normalized_equal"),
                f"ratio={data.get('sequence_matcher_ratio')} "
                f"lcs={data.get('longest_common_substring')}",
            )
            assertions.check(
                "answer_refs 全部可解析到本趟来源",
                bool(answer_refs) and data.get("refs_resolvable") is True,
                f"refs={answer_refs} sources={sorted(source_eids)}",
            )
            assertions.check(
                "已读（fetch 成功）来源 ≥ 1",
                int(data.get("read_source_count") or 0) >= 1,
                f"read={data.get('read_source_count')} sources={data.get('source_count')}",
            )
            assertions.check(
                "DB model_calls 有 research_loop 与综合调用",
                "research_loop" in (data.get("model_call_counts") or {})
                and any(
                    purpose in (data.get("model_call_counts") or {})
                    for purpose in ("research", "research_synthesis")
                ),
                f"counts={data.get('model_call_counts')}",
            )
            assertions.check(
                "DB 原始报文里能找回 answer",
                bool(data.get("answer_in_raw_reply")),
                f"answer_in_raw_reply={data.get('answer_in_raw_reply')}",
            )

            # —— 讨论 ——
            print("[discussion] 开线程并真跑一轮（可能数分钟）…", flush=True)
            discussion_start = client.post(
                f"/projects/{project_id}/discussions",
                json={
                    "topic": DISCUSSION_TOPIC,
                    "role_ids": list(DISCUSSION_ROLES),
                },
            )
            thread: dict[str, Any] = {}
            if discussion_start.status_code == 200:
                thread = discussion_start.json() or {}
            thread_id = str(thread.get("thread_id") or "")
            participants = list(thread.get("participants") or [])
            discussion: dict[str, Any] = {
                "create_status_code": discussion_start.status_code,
                "create_error": None
                if discussion_start.status_code == 200
                else discussion_start.text[:500],
                "thread_id": thread_id,
                "participants": [
                    item.get("role_title") for item in participants
                ],
                "participant_count": len(participants),
            }
            report["discussion"] = discussion
            assertions.check(
                "POST /projects/{id}/discussions 返回 200 + thread_id",
                discussion_start.status_code == 200 and bool(thread_id),
                f"status={discussion_start.status_code} thread_id={thread_id}",
            )

            if thread_id:
                started = time.monotonic()
                run_response = client.post(f"/discussions/{thread_id}/run-round")
                run_elapsed = round(time.monotonic() - started, 2)
                run_thread: dict[str, Any] = {}
                if run_response.status_code == 200:
                    run_thread = run_response.json() or {}
                turns = list(run_thread.get("turns") or [])
                discussion.update(
                    {
                        "run_status_code": run_response.status_code,
                        "run_error": None
                        if run_response.status_code == 200
                        else run_response.text[:800],
                        "elapsed_seconds": run_elapsed,
                        "turn_count": len(turns),
                        "turns": [
                            {
                                "index": turn.get("index"),
                                "role": turn.get("role"),
                                "round": turn.get("round"),
                                "text": turn.get("text"),
                                "based_on": turn.get("based_on"),
                            }
                            for turn in turns
                        ],
                    }
                )
                calls = read_model_calls(scratch.direct_url, thread_id)
                discussion["model_calls"] = calls
                discussion["model_call_counts"] = dict(
                    sorted(Counter(str(call["purpose"]) for call in calls).items())
                )
                discussion["max_call_duration_ms"] = max(
                    (int(call.get("duration_ms") or 0) for call in calls), default=0
                )
                assertions.check(
                    "run-round 返回 200",
                    run_response.status_code == 200,
                    f"status={run_response.status_code} error={discussion.get('run_error')}",
                )
                assertions.check(
                    "turns 数量 == 参与者数量",
                    len(turns) == len(participants) and len(participants) == 3,
                    f"turns={len(turns)} participants={len(participants)}",
                )
                assertions.check(
                    "讨论耗时可见",
                    isinstance(discussion.get("elapsed_seconds"), (int, float))
                    and discussion["elapsed_seconds"] > 0,
                    f"elapsed={discussion.get('elapsed_seconds')}s "
                    f"max_call={discussion.get('max_call_duration_ms')}ms",
                )
            else:
                discussion.update(
                    {
                        "run_status_code": None,
                        "run_error": "没有 thread_id，未发起 run-round",
                        "elapsed_seconds": None,
                        "turn_count": 0,
                        "turns": [],
                    }
                )
                assertions.check("run-round 返回 200", False, "没有 thread_id")
                assertions.check("turns 数量 == 参与者数量", False, "没有 thread_id")
                assertions.check("讨论耗时可见", False, "没有 thread_id")

            # —— 浏览器 ——
            print("[browser] 起前端并截图…", flush=True)
            vite = start_vite(
                port=args.web_port,
                api_port=args.port,
                log_path=Path(report["vite_log"]),
            )
            web_ready = wait_web(args.web_port)
            assertions.check(
                "前端 dev 代理就绪",
                web_ready,
                f"http://127.0.0.1:{args.web_port}/",
            )
            research_shot = None
            discussion_shot = None
            research_page_has_answer = False
            discussion_page_has_turn = False
            research_scrolled = False
            discussion_scrolled = False
            if web_ready:
                browser = Browser(session=f"rdq-s5-{os.getpid()}")
                try:
                    browser.login(
                        f"http://127.0.0.1:{args.web_port}/", username, password
                    )
                    browser.open_project(
                        f"http://127.0.0.1:{args.web_port}/?project={project_id}"
                    )
                    browser.click_tab("研究")
                    page_text = browser.page_text()
                    probe = normalize(answer)[:30]
                    research_page_has_answer = bool(probe) and probe in normalize(page_text)
                    research_scrolled = browser.scroll_to_text(answer.strip()[:15])
                    research_shot = browser.screenshot(RESEARCH_SHOT)
                    browser.click_tab("讨论")
                    page_text = browser.page_text()
                    first_turn = (
                        str((discussion.get("turns") or [{}])[0].get("text") or "")
                    )
                    probe = normalize(first_turn)[:30]
                    discussion_page_has_turn = bool(probe) and probe in normalize(page_text)
                    discussion_scrolled = browser.scroll_to_text(DISCUSSION_TOPIC.strip()[:15])
                    discussion_shot = browser.screenshot(DISCUSSION_SHOT)
                finally:
                    browser.cleanup()

            report["research_screenshot"] = research_shot
            report["discussion_screenshot"] = discussion_shot
            report["research_page_has_answer"] = research_page_has_answer
            report["discussion_page_has_turn"] = discussion_page_has_turn
            report["research_scrolled_to_answer"] = research_scrolled
            report["discussion_scrolled_to_turn"] = discussion_scrolled
            assertions.check(
                "研究页签截图落盘",
                bool(research_shot) and Path(research_shot).is_file(),
                str(research_shot),
            )
            assertions.check(
                "讨论页签截图落盘",
                bool(discussion_shot) and Path(discussion_shot).is_file(),
                str(discussion_shot),
            )
            assertions.check(
                "研究页签页面文本含 answer",
                research_page_has_answer,
                f"probe={normalize(answer)[:30]!r}",
            )
            assertions.check(
                "研究截图已把 answer 滚到闭环栏顶部",
                research_scrolled,
                f"probe={answer.strip()[:15]!r}",
            )
            assertions.check(
                "讨论截图已把线程标题滚到闭环栏顶部",
                discussion_scrolled,
                f"probe={DISCUSSION_TOPIC.strip()[:15]!r}",
            )
            assertions.check(
                "讨论页签页面文本含第一轮 turn",
                discussion_page_has_turn,
                "probe="
                + repr(
                    normalize(
                        str((discussion.get("turns") or [{}])[0].get("text") or "")
                    )[:30]
                ),
            )

        exit_code = 0 if not assertions.failed() else 1
    except SystemExit as error:
        report["error"] = str(error)
        exit_code = 2
    except Exception as error:  # noqa: BLE001
        report["error"] = f"{type(error).__name__}: {error}\n{traceback.format_exc()}"
        exit_code = 2
    finally:
        stop_process(vite)
        stop_process(uvicorn)
        report["finished_at"] = now_iso()
        report["exit_code"] = exit_code
        report["failures"] = assertions.failed()
        write_json(JSON_OUT, report, secrets)
        REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
        REPORT_OUT.write_text(
            scrub(build_report(report), secrets), encoding="utf-8"
        )
        print(f"[out] {JSON_OUT}", flush=True)
        print(f"[out] {REPORT_OUT}", flush=True)
        print(f"[out] schema={report.get('db_schema')}", flush=True)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
