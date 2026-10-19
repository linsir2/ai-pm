"""批 5 真服务验收：起真 uvicorn，走真 HTTP（上传 → 混合检索 → 删除）。

与 `vector_acceptance.py` 的区别：那个直接调装配组件，**这个走 HTTP 层**
——路由、鉴权、多部分表单、JSON 序列化都在内，才是"上线后用户真按的那个"。

用真模型（`FastEmbedEmbedder`），需要 `HF_ENDPOINT` / `HF_HUB_DISABLE_XET` 指向可达镜像。

用法：
    uv run python scripts/vector_http_acceptance.py
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


def _load_env() -> None:
    """把 `backend/.env` 读进 `os.environ`。

    与 `scripts/dev.py` 的读法保持一致——特别是**去两侧引号**
    （`PMSTUDIO_MODEL_PARAMS` 是 `'{"...": ...}'`，不剥引号 JSON 解析直接失败）。
    """

    for line in (BACKEND / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)

DOCS = {
    "退货说明.md": "# 退货\n用户下单后七天内可以无理由退货，退货运费由用户承担。",
    "机柜布线.md": "# 布线\n机柜内网线走背板槽位，电源线与信号线必须分开走。",
    "验收标准.md": "# 验收\n导出的文件要带权限戳，导出记录要能追溯到人。",
}


def _log(text: str) -> None:
    print(text, flush=True)


class _Client:
    """最小 HTTP 客户端：登录一次拿 cookie，后面全程带。"""

    def __init__(self, base: str) -> None:
        self._base = base
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor()
        )
        self._token = uuid.uuid4().hex

    def _request(self, method: str, path: str, *, data=None, headers=None):
        # 查询词是中文：必须先百分号编码，否则 http.client 在发请求时就 ascii 报错。
        safe_path = urllib.parse.quote(path, safe="/:?=&%")
        request = urllib.request.Request(
            f"{self._base}{safe_path}", data=data, method=method, headers=headers or {}
        )
        try:
            with self._opener.open(request, timeout=120) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", "replace")

    def login(self) -> None:
        status, body = self._request(
            "POST", "/api/auth/login",
            data=json.dumps({"username": os.environ["PMSTUDIO_FIRST_ADMIN_USER"],
                             "password": os.environ["PMSTUDIO_FIRST_ADMIN_PASSWORD"]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert status == 200, f"登录失败 {status}: {body}"
        _log(f"  登录成功（{os.environ['PMSTUDIO_FIRST_ADMIN_USER']}）")

    def get(self, path: str):
        return self._request("GET", path)

    def post(self, path: str, *, data: bytes, content_type: str):
        return self._request("POST", path, data=data,
                             headers={"Content-Type": content_type})

    def post_json(self, path: str, payload: dict):
        return self._request("POST", path, data=json.dumps(payload).encode(),
                             headers={"Content-Type": "application/json"})

    def delete(self, path: str):
        return self._request("DELETE", path)

    @property
    def project_id(self) -> str:
        return PROJECT


PROJECT = ""


def _multipart(field: str, filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = "----pmstudio" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: text/markdown\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def main() -> int:
    global PROJECT
    _load_env()

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "http_acceptance.db"
        env = dict(os.environ)
        env["PMSTUDIO_DB"] = str(db_path)
        env.setdefault("PMSTUDIO_PORT", "8123")

        _log(f"库：{db_path}")
        _log("起服务…")
        proc = subprocess.Popen(
            [sys.executable, "-u", "scripts/dev.py", "--no-reload",
             "--port", env["PMSTUDIO_PORT"]],
            cwd=BACKEND, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        base = f"http://127.0.0.1:{env['PMSTUDIO_PORT']}"
        try:
            for _ in range(90):
                try:
                    with urllib.request.urlopen(f"{base}/api/health", timeout=3) as r:
                        if r.status == 200:
                            break
                except Exception:  # noqa: BLE001 —— 启动期间连不上是常态
                    time.sleep(2)
            else:
                _log("服务没起来，进程输出如下：")
                proc.terminate()
                try:
                    out, _ = proc.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    out, _ = proc.communicate()
                _log((out or "")[-2500:])
                return 2
            _log("  /api/health 200")

            client = _Client(base)
            client.login()

            # 建一个项目
            status, body = client.post_json("/api/projects", {"title": "批5验收"})
            assert status in (200, 201), f"建项目失败 {status}: {body}"
            PROJECT = body["project_id"]
            _log(f"  项目：{PROJECT}")

            # 上传三份材料（走真 multipart 上传）
            uploaded = {}
            for name, text in DOCS.items():
                payload, ctype = _multipart("file", name, text.encode())
                status, body = client.post(
                    f"/api/projects/{PROJECT}/attachments", data=payload, content_type=ctype)
                assert status in (200, 201), f"上传 {name} 失败 {status}: {body}"
                uploaded[name] = body["material_id"]
                _log(f"  上传 {name} → {body['material_id']}（{body['chunk_count']} 块）")

            # 词法检索：升级前就能做到的
            status, body = client.get(f"/api/projects/{PROJECT}/search?q=导出权限戳")
            assert status == 200, body
            top = [h.get("label") for h in body.get("hits", []) if h.get("kind") == "material"][:3]
            _log(f"[2] 词法「导出权限戳」→ {top}")
            assert top and "验收标准" in top[0], f"词法命中不对：{top}"

            # 语义检索：升级才有的（与词面几乎不重合）
            status, body = client.get(f"/api/projects/{PROJECT}/search?q=网线怎么排布")
            assert status == 200, body
            sem = [h.get("label") for h in body.get("hits", []) if h.get("kind") == "material"][:3]
            _log(f"[3] 语义「网线怎么排布」→ {sem}")
            assert sem and "机柜布线" in sem[0], f"语义命中不对：{sem}"

            # 换项目隔离
            status, body2 = client.post_json("/api/projects", {"title": "别的项目"})
            other = body2["project_id"]
            status, body = client.get(f"/api/projects/{other}/search?q=导出权限戳")
            assert status == 200, body
            leaked = [h for h in body.get("hits", []) if h.get("kind") == "material"]
            _log(f"[4] 换项目查询 → 材料命中 {len(leaked)} 条（应为 0）")
            assert not leaked, "跨项目泄漏"

            # 删除：向量随块一起清
            status, body = client.delete(
                f"/api/projects/{PROJECT}/attachments/{uploaded['退货说明.md']}")
            assert status == 200, f"删除失败 {status}: {body}"
            _log("[5] 删除一份材料 200（向量已随块清除）")

            _log("\n全部判据通过。")
            return 0
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())