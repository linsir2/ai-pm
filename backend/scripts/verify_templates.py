"""模板库 live 闭环验证：打一个**正在跑**的后端，断言模板链路端到端。

读检查永远安全；写检查（建模板/建项目/开轮门）默认只读，
加 `--write` 才跑——指到隔离栈去写，别拿工作库试写。

跑法：
    python3 scripts/verify_templates.py --base http://127.0.0.1:8100
    python3 scripts/verify_templates.py --base http://127.0.0.1:8102 --write
"""

import argparse
import json
import sys
import urllib.request

TRIPLE = [
    {"id": "s5", "title": "功能", "purpose": "摆设", "role": "feature"},
    {"id": "s11", "title": "未决", "purpose": "摆设", "role": "open_questions"},
    {"id": "s13", "title": "版本", "purpose": "摆设", "role": "version_history"},
]

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "PASS " if condition else "FAIL "
    extra = f" ({detail})" if detail and not condition else ""
    print(mark + name + extra)
    if not condition:
        FAILURES.append(name)


def call(base: str, method: str, path: str, body: object = None) -> tuple[int, object]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    status, templates = call(base, "GET", "/api/templates")
    check("list 200", status == 200, str(status))
    ids = [item["template_id"] for item in templates] if status == 200 else []
    check("builtins seeded+ordered", ids[:2] == ["standard", "light"], str(ids[:3]))

    status, schema = call(base, "GET", "/api/schema")
    schema_templates = (
        {t["template_id"]: t for t in schema.get("templates", [])}
        if status == 200
        else {}
    )
    check("schema carries templates", set(schema_templates) >= {"standard", "light"})
    s8 = next(
        (
            s
            for s in schema_templates.get("standard", {}).get("sections", [])
            if s["section_id"] == "s8"
        ),
        {},
    )
    got_slots = [s["name"] for s in s8.get("slots", [])][:2]
    check("schema slots present", got_slots == ["performance", "availability"])

    status, projects = call(base, "GET", "/api/projects")
    summaries = projects if status == 200 else []
    check(
        "summaries carry template ref",
        all("template_id" in p and "template_title" in p for p in summaries),
        f"{len(summaries)} projects",
    )

    if args.write:
        status, created = call(
            base, "POST", "/api/templates", {"title": "脚本验证", "sections": TRIPLE}
        )
        check("create template 201", status == 201, str(status))
        custom_id = created.get("template_id", "") if isinstance(created, dict) else ""
        status, project = call(
            base, "POST", "/api/projects", {"title": "脚本验证项目", "template_id": custom_id}
        )
        check("create project 201", status == 201, str(status))
        project_id = project.get("project_id", "") if isinstance(project, dict) else ""
        status, workspace = call(base, "GET", f"/api/projects/{project_id}/workspace")
        sections = (
            [s["section_id"] for s in workspace["document"]["sections"]]
            if status == 200
            else []
        )
        check("workspace honors template", sections == ["s5", "s11", "s13"], str(sections))
        status, body = call(
            base, "POST", f"/api/projects/{project_id}/rounds", {"input": "写点东西"}
        )
        check(
            "round gate 409",
            status == 409
            and isinstance(body, dict)
            and body.get("code") == "template_rounds_unsupported",
            f"{status} {body}",
        )
        status, missing = call(base, "GET", "/api/templates/nope")
        check("unknown template 404", status == 404)

    print(f"FAILURES: {len(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
