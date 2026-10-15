"""模板管理 live 验收：打一个**正在跑**的后端，断言「建 / 改 / 删 / 内置保护 / 快照不变式」
在真 HTTP + 真 SQLite 上闭环（`02` §11.0.70、`06` §2.3）。

与 `verify_templates.py` 的分工：那份验模板库**读得对**（画廊、快照、自定义存取），
这份验模板库**管得住**（改、删、内置锁死、改删对已有项目零影响、枚举表齐不齐）。

读检查永远安全；写检查（建模板/改/删/建项目）默认只读，加 `--write` 才跑——
指到隔离栈去写，别拿工作库试写。

跑法：
    python3 scripts/verify_template_management.py --base http://127.0.0.1:8113
    python3 scripts/verify_template_management.py --base http://127.0.0.1:8102 --write
"""

import argparse
import json
import sys
import urllib.request

#: 一份合法的自建模板（五章）：三锚点各一 + 一个 steps 章 + 一个带判据的骨架章。
CUSTOM = [
    {"id": "s1", "title": "活动背景", "purpose": "为什么办这场", "role": "backbone",
     "min_entries": 1},
    {"id": "s5", "title": "要办的活动", "purpose": "给谁解决什么", "role": "feature"},
    {"id": "s9", "title": "活动流程", "purpose": "当天怎么走", "role": "steps"},
    {"id": "s11", "title": "待办与风险", "purpose": "还没定的", "role": "open_questions"},
    {"id": "s13", "title": "版本历史", "purpose": "导出时生成", "role": "version_history"},
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
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        return error.code, (json.loads(raw) if raw else None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--write", action="store_true", help="跑写检查（建/改/删/建项目）")
    args = parser.parse_args()
    base = args.base

    # ── 读：库的结构与标记 ──
    status, listed = call(base, "GET", "/api/templates")
    check("模板库可读", status == 200, f"{status}")
    builtins = {item["template_id"]: item for item in listed if item.get("builtin") is True}
    check("内置两份都标着 builtin", set(builtins) == {"standard", "light"}, str(set(builtins)))
    check("每份都带明确的 builtin 标记（没有缺字段的）",
          all(isinstance(item.get("builtin"), bool) for item in listed),
          str([(item["template_id"], item.get("builtin")) for item in listed]))
    check("非内置的都标 False",
          all(item["builtin"] is False for item in listed if item["template_id"] not in builtins))

    status, standard = call(base, "GET", "/api/templates/standard")
    section_ids = [item["section_id"] for item in standard["sections"]]
    check("standard 十三章", len(section_ids) == 13, str(section_ids))
    status, light = call(base, "GET", "/api/templates/light")
    light_ids = [item["section_id"] for item in light["sections"]]
    check("light 六章且跳号（standard 的子集）",
          light_ids == ["s1", "s2", "s4", "s5", "s11", "s13"], str(light_ids))

    # ── 读：枚举表（界面下拉全靠它，见 `02` §11.0.70）──
    status, schema = call(base, "GET", "/api/schema")
    roles = set(schema.get("template_roles") or [])
    kinds = set(schema.get("block_kinds") or [])
    check("/schema 给全八种角色",
          roles == {"feature", "open_questions", "version_history", "steps",
                    "decision", "data", "backbone", "plain"}, str(sorted(roles)))
    check("/schema 的 kind 表含验收标准与步骤",
          {"acceptance", "steps", "entity", "interface"} <= kinds, str(sorted(kinds)[:8]))

    # ── 读：内置锁死（不需要写权限也拦得住）──
    for method in ("PUT", "DELETE"):
        status, body = call(base, method, "/api/templates/standard",
                            {"title": "偷改", "sections": CUSTOM} if method == "PUT" else None)
        check(f"{method} 内置模板被拒",
              status == 409 and body.get("code") == "template_builtin_protected",
              f"{status} {body}")
        status, still = call(base, "GET", "/api/templates/standard")
        check(f"{method} 之后内置那份原样还在",
              still["title"] == "标准版" and len(still["sections"]) == 13)

    if not args.write:
        print("\n（读检查跑完；加 --write 才跑建/改/删/快照不变式）")
        return 1 if FAILURES else 0

    # ── 写：建 → 改 → 建项目 → 删（闭环）──
    status, created = call(base, "POST", "/api/templates",
                             {"title": "验收模板", "sections": CUSTOM})
    check("自建模板建成", status == 201 and created.get("builtin") is False, f"{status} {created}")
    template_id = created["template_id"]

    status, updated = call(base, "PUT", f"/api/templates/{template_id}",
                           {"title": "验收模板 2.0", "sections": CUSTOM + [
                               {"id": "s14", "title": "验收标准", "purpose": "怎么算做完了",
                                "role": "backbone", "min_entries": 2}]})
    check("改成功且章序与标题都变了",
          status == 200 and updated["title"] == "验收模板 2.0"
          and [item["section_id"] for item in updated["sections"]][-1] == "s14",
          f"{status}")

    status, again = call(base, "GET", f"/api/templates/{template_id}")
    check("改完再读是对的那份", again["title"] == "验收模板 2.0" and len(again["sections"]) == 6)

    # 判据随模板走（就绪度与上下文都读它）
    declared = {item["section_id"]: item.get("min_entries") for item in again["sections"]}
    check("判据声明跟着模板存下来了", declared.get("s1", {}).get("count") == 1
          and declared.get("s14", {}).get("count") == 2, str(declared))

    status, project = call(base, "POST", "/api/projects",
                           {"title": "用它建的", "template_id": template_id})
    check("用它建项目", status == 201, f"{status}")
    project_id = project["project_id"]
    _, before = call(base, "GET", f"/api/projects/{project_id}/workspace")
    before_shape = [item["section_id"] for item in before["document"]["sections"]]
    check("项目的骨架就是这份模板的六章", before_shape == ["s1", "s5", "s9", "s11", "s13", "s14"],
          str(before_shape))

    # 快照不变式：改与删都不许动已有项目（`02` §11.0.70）
    call(base, "PUT", f"/api/templates/{template_id}",
         {"title": "改名不改章", "sections": CUSTOM})
    _, after_update = call(base, "GET", f"/api/projects/{project_id}/workspace")
    check("改模板后项目骨架逐字不变",
          [item["section_id"] for item in after_update["document"]["sections"]] == before_shape)

    status, _ = call(base, "DELETE", f"/api/templates/{template_id}")
    check("删自建模板", status == 204, f"{status}")
    status, _ = call(base, "GET", f"/api/templates/{template_id}")
    check("删完再取是 404", status == 404, f"{status}")
    status, after_delete = call(base, "GET", f"/api/projects/{project_id}/workspace")
    check("删模板后项目照常打开",
          status == 200 and after_delete["project"]["title"] == "用它建的"
          and [item["section_id"] for item in after_delete["document"]["sections"]] == before_shape,
          f"{status}")
    status, again_build = call(base, "POST", "/api/projects",
                               {"title": "再建", "template_id": template_id})
    check("被删的 id 不能再建项目", status == 404, f"{status}")

    status, listed = call(base, "GET", "/api/templates")
    check("库里没留下那一份", all(item["template_id"] != template_id for item in listed))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} 项没过：" + "、".join(FAILURES))
        return 1
    print("模板管理验收全过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())