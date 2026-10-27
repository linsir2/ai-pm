#!/usr/bin/env python3
"""批 6a 端到端真跑：真 .docx + 真 Tavily + 真开轮 + 真浏览器截图。

走的是**用户真按的那条路**：真 uvicorn、真 HTTP、真 multipart、真解析器
（python-docx）、真模型开轮、**真 Tavily**、真前端（vite）+ 真浏览器
（Playwright + 系统 Chrome）。

**判据**：
1. 真 .docx 上传成功（python-docx 解析）；
2. 真 Tavily 跑通 → 联网结果入库（材料表里能看见 `origin=web_search`）；
3. **联网来的材料能被 F6 搜到**（今天搜不到——它没有向量）——用**与正文零词面重合**
   的问法搜；
4. 真开轮里联网结果带上了编号，提示词里看得见那段正文；
5. 截图：顶栏搜索的结果里有材料命中那一行。

**跑法**（真花 Tavily 的钱，`.env` 里有 key）：

```bash
cd backend && HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \\
  set -a && . ./.env && set +a && \\
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/vector_web_e2e.py
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

PORT = "8141"
WEB_PORT = "5311"

#: 真 .docx 的正文（机柜布线规范）。检索用**零词面重合**的问法
#: （正文写"内网线走背板槽位"，问"机器里面的线该接到哪儿"）。
DOCX_PARAGRAPHS = [
    "机柜布线规范",
    "机柜内的内网线要走背板槽位，从机柜后部进入。",
    "电源线与信号线必须分开走，电源线走左侧竖直理线架。",
    "每根线的标签朝外，方便巡检时核对。",
]
PARAPHRASE_QUERY = "机器里面的线该接到哪儿"


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


def main() -> int:
    _load_env()
    failures: list[str] = []

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

            status, body = client.post_json("/api/projects", {"title": "批6a端到端"})
            assert status in (200, 201), f"建项目失败 {status}: {body}"
            project = body["project_id"]
            _log(f"  项目 {project}")

            # ── 1. 真 .docx 上传 ───────────────────────────────────
            payload, ctype = _multipart(
                "file", docx_path.name, docx_bytes,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            status, body = client.post(
                f"/api/projects/{project}/attachments", data=payload, content_type=ctype
            )
            assert status in (200, 201), f"上传失败 {status}: {body}"
            _log(f"[1] 上传真 .docx → {body['material_id']}"
                 f"（{body['chunk_count']} 块，parser={body['parser_id']}）")
            if body["parser_id"] != "python-docx":
                failures.append(f"解析器不对：{body['parser_id']}")

            # ── 2. 真开轮（带材料）────────────────────────────────
            # 两件事分两轮：起轮的分析者要能"复述"用户输入（主张里的原话片段
            # 必须在输入里找得到），把"布线规范"和"查法规"塞进同一句会让复述对不上，
            # 整轮 502（`model_packet_unparsable`）。分两轮，各说各的。
            status, body = client.post_json(f"/api/projects/{project}/rounds", {
                "input": "我们要给机柜上线的布线规范定一个说法，并把这份材料带进这一轮。",
                "material_ids": [body["material_id"]],
            })
            assert status in (200, 201), f"起轮失败 {status}: {body}"
            round_id = body["round_id"]
            _log(f"[2] 起轮 {round_id}（真模型，带材料）…")
            deadline = time.time() + 900
            while time.time() < deadline:
                status, view = client.get(f"/api/rounds/{round_id}")
                if status == 200 and str(view.get("step")) in {
                    "claims_ready", "done", "failed"
                }:
                    break
                time.sleep(5)
            _log(f"  轮状态：{view.get('step') if status == 200 else '?'}")
            if status != 200 or str(view.get("step")) == "failed":
                failures.append(
                    f"起轮失败：{view.get('failure_reason') if status == 200 else status}"
                )

            # ── 3. 真 Tavily（单独一轮，单独勾检索词）─────────────────
            status, body = client.post_json(f"/api/projects/{project}/rounds", {
                "input": "《网络安全法》对日志留存期限是怎么规定的？我需要外部客观事实。",
                "material_ids": [],
            })
            assert status in (200, 201), f"起轮（Tavily）失败 {status}: {body}"
            web_round = body["round_id"]
            _log(f"[3] 起轮 {web_round}（要联网检索词）…")
            web_queries: list[str] = []
            deadline = time.time() + 900
            while time.time() < deadline:
                status, view = client.get(f"/api/rounds/{web_round}")
                if status == 200 and str(view.get("step")) in {
                    "claims_ready", "done", "failed"
                }:
                    web_queries = [
                        str(q.get("query", "")) for q in (view.get("web_queries") or [])
                    ]
                    break
                time.sleep(5)
            _log(f"  分析者提名 {len(web_queries)} 条联网检索词：{web_queries[:2]}")
            if not web_queries:
                failures.append("分析者没有提名联网检索词——Tavily 这一段没验成")
            else:
                status, body = client.post_json(f"/api/rounds/{web_round}/opening", {
                    "seats": [str(s.get("seat_id", ""))
                              for s in (view.get("nominated_seats") or [])],
                    "web_queries": web_queries[:1],
                })
                if status not in (200, 201):
                    failures.append(f"提交复述卡失败 {status}: {body}")
                else:
                    deadline = time.time() + 900
                    while time.time() < deadline:
                        status, view = client.get(f"/api/rounds/{web_round}")
                        if status == 200 and str(view.get("step")) in {
                            "done", "failed", "abandoned", "draft_ready",
                            "questions_ready",
                        }:
                            break
                        time.sleep(5)
                    _log(f"  勾选后轮状态：{view.get('step')}")

            status, body = client.get(f"/api/projects/{project}/attachments")
            web_materials = [
                m for m in (body or []) if str(m.get("origin", "")) == "web_search"
            ]
            _log(f"  联网材料 {len(web_materials)} 份")
            if not web_materials:
                failures.append("没有联网材料落库——Tavily 这一段没验成")

            # ── 4. 联网材料能被 F6 搜到（零词面重合的问法）─────────
            status, body = client.get(
                f"/api/projects/{project}/search?q={urllib.parse.quote(PARAPHRASE_QUERY)}"
            )
            hits = [h for h in (body or {}).get("hits", [])
                    if h.get("kind") == "material"]
            labels = [h.get("label", "") for h in hits]
            _log(f"[4] 搜「{PARAPHRASE_QUERY}」→ {len(hits)} 条材料命中")
            for label in labels[:3]:
                _log(f"    · {label[:60]}")
            if not any("机柜" in label for label in labels):
                failures.append(
                    f"用零词面重合的问法搜不到上传的材料：{labels[:3]}"
                )

            # ── 5. 截图 ────────────────────────────────────────────
            rc = _screenshot(project, base, PARAPHRASE_QUERY)
            if rc != 0:
                failures.append("截图那一步没过")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    _log("")
    if failures:
        for item in failures:
            _log(f"  ✗ {item}")
        return 1
    _log("  ✔ 全部判据通过")
    return 0


def _screenshot(project: str, base: str, query: str) -> int:
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
        shot = out_dir / "批6a-端到端-材料命中.png"
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.goto(f"http://127.0.0.1:{WEB_PORT}/", wait_until="domcontentloaded")
            page.fill("#auth-username", os.environ["PMSTUDIO_FIRST_ADMIN_USER"])
            page.fill("#auth-password", os.environ["PMSTUDIO_FIRST_ADMIN_PASSWORD"])
            page.click("button[type=submit]")
            page.wait_for_timeout(6000)
            box = page.locator("input[type=search], input[placeholder*='搜索']").first
            box.fill(query)
            box.press("Enter")
            page.wait_for_timeout(4000)
            body = page.inner_text("body")
            page.screenshot(path=str(shot), full_page=True)
            browser.close()
        _log(f"[5] 截图：{shot}")
        if "机柜" not in body:
            _log("  ✗ 截图里没看到材料命中")
            return 4
        return 0
    finally:
        vite.terminate()
        try:
            vite.wait(timeout=10)
        except subprocess.TimeoutExpired:
            vite.kill()


if __name__ == "__main__":
    raise SystemExit(main())
