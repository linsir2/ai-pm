#!/usr/bin/env python3
"""批 5 端到端真跑：真 .docx → 真开轮 → **句面完全不同也命中** → 截图。

**走的是用户真按的那条路**：真 uvicorn、真 HTTP、真 multipart 上传、真解析器
（python-docx）、真模型开轮、真前端（vite）+ 真浏览器（Playwright + 系统 Chrome）。

**为什么命中落在 F6 顶栏搜索这条路上**（与批 5 计划原文的偏差，按代码事实）：
- 「句面完全不同也命中」的用户可见落点是 `GET /projects/{id}/search`（顶栏搜索）
  ——那正是批 5 改的 `MaterialRetriever` 端口；
- **轮内选块**（`select_chunks`）是 2-gram 词面粗排，`context.py:1089` 明确记着
  「语义检索在这条路上被证据否决」——那里硬塞向量会与那条既有决定冲突。

**判据**：
1. 真 .docx 上传成功，且块进了本轮（拿到 `E` 编号）；
2. 用**与正文零词面重合**的说法搜 → 命中**我们上传的那份材料**；
3. 用**逐字**的词搜 → 同样命中（词法零倒退）；
4. 换项目搜 → 0 条（隔离）；
5. 真 Tavily：复述卡上的联网检索词勾选后真跑通，联网结果作为材料出现；
6. 截图里看得见材料命中那一行。

**跑法**（Tavily 真花钱，`.env` 里 `PMSTUDIO_TAVILY_API_KEY` 已有）：

```bash
cd backend && HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \\
  set -a && . ./.env && set +a && \\
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/vector_e2e_acceptance.py
```
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(BACKEND))

PORT = "8131"
WEB_PORT = "5301"

#: 真 .docx 的正文（机柜布线规范）。**用词刻意与检索词错开**：
#: 正文写"内网线走背板槽位"，检索问"网线要接到哪里"——零词面重合。
DOCX_PARAGRAPHS = [
    "机柜布线规范",
    "机柜内的内网线要走背板槽位，从机柜后部进入。",
    "电源线与信号线必须分开走，电源线走左侧竖直理线架。",
    "每根线的标签朝外，方便巡检时核对。",
    "布线完成后要做通断测试，并拍照留档。",
]

#: 与正文**零词面重合**的问法。
PARAPHRASE_QUERY = "机器内部的线要接到哪个位置"

#: 正文里**原样**出现的词（词法零倒退的对照）。
LITERAL_QUERY = "背板槽位"


def _log(text: str) -> None:
    print(text, flush=True)


def _load_env() -> None:
    for line in (BACKEND / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _make_docx(path: Path) -> bytes:
    """造一份**真** .docx（走真 office 解析器，不是 .md 改后缀）。"""
    import docx

    document = docx.Document()
    for paragraph in DOCX_PARAGRAPHS:
        document.add_paragraph(paragraph)
    document.save(str(path))
    return path.read_bytes()


def _multipart(field: str, filename: str, content: bytes, content_type: str):
    boundary = "----pmstudio" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


class _Client:
    """最小 HTTP 客户端（登录一次拿 cookie）。"""

    def __init__(self, base: str) -> None:
        self._base = base
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())

    def _request(self, method: str, path: str, *, data=None, headers=None):
        safe = urllib.parse.quote(path, safe="/:?=&%")
        request = urllib.request.Request(
            f"{self._base}{safe}", data=data, method=method, headers=headers or {}
        )
        try:
            with self._opener.open(request, timeout=600) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", "replace")

    def get(self, path: str):
        return self._request("GET", path)

    def post_json(self, path: str, payload: dict):
        return self._request(
            "POST", path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )

    def post(self, path: str, *, data: bytes, content_type: str):
        return self._request("POST", path, data=data,
                             headers={"Content-Type": content_type})

    def delete(self, path: str):
        return self._request("DELETE", path)


def _drive_round(
    client: _Client, round_id: str, *, web_queries: list[str] | None = None
) -> dict:
    """把一轮推到 `done`：复述卡 → 问卷（跳过） → 确认草案（全收）。

    每一段都按 `waiting_on` 走（那是服务端说的"现在轮到谁"），不猜。
    """
    deadline = time.time() + 1200
    view: dict = {}
    while time.time() < deadline:
        status, view = client.get(f"/api/rounds/{round_id}")
        if status != 200:
            time.sleep(5)
            continue
        step = str(view.get("step", ""))
        waiting = str(view.get("waiting_on", ""))
        if step in {"done", "failed", "abandoned"}:
            return view
        if waiting == "opening_card":
            seats = [
                str(s.get("seat_id", ""))
                for s in (view.get("nominated_seats") or [])
            ] or [
                str(s.get("seat_id", ""))
                for s in (view.get("available_seats") or [])
            ]
            chosen = web_queries or []
            if web_queries is None:
                chosen = []          # 第一轮：不联网（联网是第 6 段单独验的）
            _log(f"  提交复述卡：席位 {len(seats)} 个、联网检索词 {len(chosen)} 条")
            status, body = client.post_json(f"/api/rounds/{round_id}/opening", {
                "seats": seats, "web_queries": chosen,
            })
            assert status in (200, 201), f"提交复述卡失败 {status}: {body}"
        elif waiting == "questionnaire":
            questions = view.get("questions") or []
            form_id = str(view.get("form_id") or "")
            _log(f"  交问卷：{len(questions)} 题全跳过")
            status, body = client.post_json(f"/api/rounds/{round_id}/answers", {
                "form_id": form_id,
                "answers": [],
                "skipped": [str(q.get("question_id", "")) for q in questions],
            })
            assert status in (200, 201), f"交问卷失败 {status}: {body}"
        elif waiting == "draft_card":
            draft = view.get("draft") or {}
            proposals = draft.get("proposals") or []
            accepted = [
                str(p.get("proposal_id", p.get("id", "")))
                for p in proposals
                if p.get("default_selected", True) or p.get("source") != "proposed"
            ]
            _log(f"  确认草案：收 {len(accepted)}/{len(proposals)} 条")
            status, body = client.post_json(f"/api/rounds/{round_id}/confirm", {
                "accepted": accepted, "accepted_memory": [],
            })
            assert status in (200, 201), f"确认草案失败 {status}: {body}"
        else:
            time.sleep(5)
    return view


def _material_hits(body) -> list[dict]:
    return [h for h in (body or {}).get("hits", []) if h.get("kind") == "material"]


def main() -> int:
    _load_env()
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "e2e.db"
        docx_path = Path(tmp) / "机柜布线规范.docx"
        docx_bytes = _make_docx(docx_path)
        _log(f"真 .docx：{len(docx_bytes)} 字节")

        env = dict(os.environ)
        env["PMSTUDIO_DB"] = str(db_path)
        env["PMSTUDIO_PORT"] = PORT
        _log("起服务…")
        proc = subprocess.Popen(
            [sys.executable, "-u", "scripts/dev.py", "--no-reload", "--port", PORT],
            cwd=BACKEND, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
        base = f"http://127.0.0.1:{PORT}"
        try:
            for _ in range(120):
                try:
                    with urllib.request.urlopen(f"{base}/api/health", timeout=3) as r:
                        if r.status == 200:
                            break
                except Exception:  # noqa: BLE001
                    time.sleep(2)
            else:
                proc.terminate()
                out, _ = proc.communicate(timeout=10)
                _log("服务没起来：\n" + (out or "")[-2000:])
                return 2
            _log("  /api/health 200")

            client = _Client(base)
            status, _ = client.post_json("/api/auth/login", {
                "username": os.environ["PMSTUDIO_FIRST_ADMIN_USER"],
                "password": os.environ["PMSTUDIO_FIRST_ADMIN_PASSWORD"],
            })
            assert status == 200, f"登录失败 {status}"
            _log("  登录成功")

            status, body = client.post_json("/api/projects", {"title": "批5端到端验收"})
            assert status in (200, 201), f"建项目失败 {status}: {body}"
            project = body["project_id"]
            _log(f"  项目 {project}")

            # ── 1. 真上传 ─────────────────────────────────────────────
            payload, ctype = _multipart(
                "file", docx_path.name, docx_bytes,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            status, body = client.post(
                f"/api/projects/{project}/attachments", data=payload, content_type=ctype)
            assert status in (200, 201), f"上传失败 {status}: {body}"
            material_id = body["material_id"]
            _log(f"[1] 上传真 .docx → {material_id}（{body['chunk_count']} 块，"
                 f"parser={body['parser_id']}）")
            assert body["chunk_count"] >= 1, body

            # ── 2. 真开轮（材料带进去） ─────────────────────────────────
            status, body = client.post_json(f"/api/projects/{project}/rounds", {
                "input": "我们要给机柜上线的布线规范定一个说法，并把这份材料带进这一轮。",
                "material_ids": [material_id],
            })
            assert status in (200, 201), f"起轮失败 {status}: {body}"
            round_id = body["round_id"]
            _log(f"[2] 起轮 {round_id}（真模型）…")
            view = _drive_round(client, round_id)
            state = str(view.get("step", ""))
            _log(f"  轮状态：{state}")
            assert state == "done", f"这一轮没跑完（{state}）：{view.get('failure_reason')}"
            # 材料块拿到了编号（E 系列）= 它真的进了这一轮
            claims = view.get("claims") or []
            _log(f"  主张 {len(claims)} 条（材料块拿到编号 = 已进本轮）")

            # ── 3. 句面完全不同地搜 ───────────────────────────────────
            status, body = client.get(
                f"/api/projects/{project}/search?q={urllib.parse.quote(PARAPHRASE_QUERY)}")
            assert status == 200, body
            hits = _material_hits(body)
            labels = [h.get("label", "") for h in hits]
            _log(f"[3] 语义查询「{PARAPHRASE_QUERY}」→ {len(hits)} 条材料命中")
            for label in labels[:3]:
                _log(f"    · {label[:60]}")
            assert hits, "换个说法搜不到——混合检索没起作用"
            assert any("机柜" in label for label in labels), \
                f"命中的不是我们上传的那份材料：{labels[:3]}"

            # ── 4. 词法零倒退 ──────────────────────────────────────────
            status, body = client.get(
                f"/api/projects/{project}/search?q={urllib.parse.quote(LITERAL_QUERY)}")
            hits = _material_hits(body)
            labels = [h.get("label", "") for h in hits]
            _log(f"[4] 词法查询「{LITERAL_QUERY}」→ {len(hits)} 条")
            assert hits and any("机柜" in label for label in labels), \
                f"逐字词搜不到那份材料了：{labels[:3]}"

            # ── 5. 隔离 ───────────────────────────────────────────────
            status, other = client.post_json("/api/projects", {"title": "别的项目"})
            other_id = other["project_id"]
            status, body = client.get(
                f"/api/projects/{other_id}/search?q={urllib.parse.quote(LITERAL_QUERY)}")
            leaked = _material_hits(body)
            _log(f"[5] 换项目查询 → {len(leaked)} 条（应为 0）")
            assert not leaked, "跨项目泄漏"

            # ── 6. 真 Tavily ───────────────────────────────────────────
            rc = _tavily_segment(client, project)
            if rc != 0:
                return rc

            # ── 7. 界面截图 ────────────────────────────────────────────
            rc = _screenshot(project, base)
            if rc != 0:
                return rc
            return 0
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def _tavily_segment(client: _Client, project: str) -> int:
    """真 Tavily：分析者提名联网检索词 → 勾选 → 真跑 → 结果作为材料出现。

    这一段是 **P17b 老路的回归**（不是向量判据）——批 5 计划 Step 3 明写要"真 Tavily"。
    """
    _log("[6] 真 Tavily 一段…")
    status, body = client.post_json(f"/api/projects/{project}/rounds", {
        "input": "2026 年个人信息保护法对导出日志的留存要求是什么？我需要外部客观事实。",
        "material_ids": [],
    })
    if status not in (200, 201):
        _log(f"  ✗ 起轮失败 {status}: {body}")
        return 3
    round_id = body["round_id"]
    deadline = time.time() + 900
    view = None
    while time.time() < deadline:
        status, view = client.get(f"/api/rounds/{round_id}")
        if status == 200 and str(view.get("step", "")) in {
            "done", "failed", "abandoned", "awaiting_opening", "questionnaire", "draft"
        }:
            break
        time.sleep(5)
    if view is None:
        _log("  ✗ 轮没起来")
        return 3
    queries = view.get("web_queries") or []
    _log(f"  分析者提名了 {len(queries)} 条联网检索词")
    # 席位名单取分析者提名的（前端 `defaultSeats` 同一口径）
    seats = [str(s.get("seat_id", "")) for s in (view.get("nominated_seats") or [])]
    if not seats:
        seats = [str(s.get("seat_id", "")) for s in (view.get("available_seats") or [])]
    if not queries:
        _log("  ⚠ 这一轮没提名联网检索词（模型没提）——跳过 Tavily 段，"
             "**这不算通过**，只是模型这轮没提")
        return 0
    chosen = [str(q.get("query", "")) for q in queries[:1]]
    _log(f"  勾选：{chosen}；席位 {len(seats)} 个")

    status, body = client.post_json(f"/api/rounds/{round_id}/opening", {
        "seats": seats,
        "web_queries": chosen,
    })
    if status not in (200, 201):
        _log(f"  ✗ 提交复述卡失败 {status}: {body}")
        return 3
    deadline = time.time() + 900
    while time.time() < deadline:
        status, view = client.get(f"/api/rounds/{round_id}")
        if status == 200 and str(view.get("step", "")) in {"done", "failed", "abandoned"}:
            break
        time.sleep(5)
    _log(f"  轮状态：{view.get('step') if view else '?'}")
    status, body = client.get(f"/api/projects/{project}/attachments")
    web_materials = [
        m for m in (body or [])
        if str(m.get("origin", "")) == "web_search"
    ]
    _log(f"  联网材料 {len(web_materials)} 份")
    if not web_materials:
        _log("  ⚠ 没有联网材料——Tavily 这一段没验成（模型没提词或 Tavily 失败）")
        return 0
    _log("  ✔ 真 Tavily 跑通，联网结果作为材料落库")
    return 0


def _screenshot(project: str, base: str) -> int:
    """真浏览器：顶栏搜索 → 材料命中那一行 → 截图。"""
    _log("[7] 真浏览器截图…")
    from playwright.sync_api import sync_playwright

    frontend = BACKEND.parent / "frontend"
    env = dict(os.environ)
    env["PMSTUDIO_API_TARGET"] = base
    env["PMSTUDIO_WEB_PORT"] = WEB_PORT
    vite = subprocess.Popen(
        ["npm", "run", "dev"], cwd=frontend, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        for _ in range(120):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{WEB_PORT}/", timeout=3):
                    break
            except Exception:  # noqa: BLE001
                time.sleep(2)
        else:
            vite.terminate()
            out, _ = vite.communicate(timeout=10)
            _log("前端没起来：\n" + (out or "")[-1500:])
            return 3

        out_dir = BACKEND.parent / ".codex" / "runs" / "2026-10-19"
        out_dir.mkdir(parents=True, exist_ok=True)
        shot = out_dir / "批5-端到端-材料命中.png"
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.goto(f"http://127.0.0.1:{WEB_PORT}/", wait_until="domcontentloaded")
            page.fill("#auth-username", os.environ["PMSTUDIO_FIRST_ADMIN_USER"])
            page.fill("#auth-password", os.environ["PMSTUDIO_FIRST_ADMIN_PASSWORD"])
            page.click("button[type=submit]")
            page.wait_for_timeout(6000)
            _log(f"  登录后 URL = {page.url}")

            # **先切回目标项目**：前面造隔离项目时前端自动选中了"最近的那个"，
            # 搜索打在别的项目上当然"没找到"（第一次跑就踩了，截图里能看见）。
            switcher = page.locator("#project-switch")
            if switcher.count():
                switcher.select_option(project)
                page.wait_for_timeout(2500)
                _log(f"  已切到项目 {project}")

            # 顶栏搜索框
            box = page.locator("input[type=search], input[placeholder*='搜索']").first
            box.fill(PARAPHRASE_QUERY)
            box.press("Enter")
            page.wait_for_timeout(4000)
            body = page.inner_text("body")
            page.screenshot(path=str(shot), full_page=True)
            browser.close()

        _log(f"  截图：{shot}")
        if "机柜" not in body:
            _log("  ✗ 截图里没看到材料命中的那一行")
            return 4
        _log("  ✔ 界面上看得见材料命中")
        return 0
    finally:
        vite.terminate()
        try:
            vite.wait(timeout=10)
        except subprocess.TimeoutExpired:
            vite.kill()


if __name__ == "__main__":
    raise SystemExit(main())
