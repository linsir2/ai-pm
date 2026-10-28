"""一次完整迭代：输入 → 开场卡 → 席位 → 问卷 → 答卷（含跳过）→ 草案 → 确认 → 多一版。

全程走 HTTP（ASGI transport）+ 真 PG + 真 LangGraph；只有模型是脚本化的。
"""

import json

from tests.support.api_client import async_client

CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 全文（空文档）与本轮输入",
        "claims": [
            {
                "text": "要做一个订单导出功能",
                "raw_span": "想做个订单导出的功能",
                "label": "need",
                "zone": "body",
                "section_id": "s5",
                "feature_slug": "order-export",
            },
            {
                "text": "数据量大概几万",
                "raw_span": "数据量大概几万",
                "label": "guess",
                "zone": "body",
                "section_id": "s10",
                "assumption": {
                    "failure_consequence": "若实际是百万级，同步导出会超时",
                    "verification": "上线后看导出耗时分布",
                },
            },
            {
                "text": "登录页太丑",
                "raw_span": "登录页太丑了",
                "label": "preference",
                "zone": "preference",
            },
        ],
        # 席位提名：目录里 7 个席位，这次只挑两个（导出相关 + 数据量），
        # 其余常驻席位（业务规则/权限/前端交互/技术方案）**故意没挑**——
        # 开场卡上该显示的是这份名单，不是目录匹配的结果。
        "seats": [
            {"seat_id": "seat.export_import", "why": "这轮就是在做导出"},
            {"seat_id": "seat.data_scale", "why": "你说到了数据量"},
        ],
    },
    ensure_ascii=False,
)

SEAT_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 与 E1–E3",
        "opinions": [
            {
                "local_id": "O1",
                "form": "question",
                "about": "导出的数据量级",
                "claim": "量级决定同步还是异步，必须问用户",
                "because": "1 万以内可同步，10 万以上必须异步",
                "evidence_refs": ["E2"],
            },
            {
                "local_id": "O2",
                "form": "judgment",
                "about": "异常与边界",
                "claim": "无导出权限的用户不显示导出入口",
                "because": "订单含客户信息，导出即数据外流",
                "evidence_refs": ["E1"],
            },
            {
                "local_id": "O3",
                "form": "judgment",
                "about": "导出记录保留时长",
                "claim": "导出记录保留 30 天",
                "because": "同类工具的常见做法（纯经验判断）",
                "basis_note": "没有可引用的编号",
            },
        ],
    },
    ensure_ascii=False,
)

#: 夹具补全（`02` §11.0.37）：把席位那几条意见的去向**申报掉**。
#: 不补的话，"点名重装"会在第一次整合后插进来多消耗一次脚本回复，
#: 于是第一步就出草案、问卷那一段剧本整个错位。真模型该写成这样。
SEAT_OPINIONS_ACCOUNTED = [
    {"handle": f"{seat}:O{index}", "reason": "这一趟先不单列：已并进上面的题或提案"}
    for seat in ("seat.data_scale", "seat.permissions")
    for index in (1, 2, 3)
]

QUESTION_REPLY = json.dumps(
    {
        "questions": [
            {
                "kind": "gap",
                "title": "单次导出的最大行数是多少？",
                "why": "量级决定同步还是异步",
                "consequence_if_skipped": "按同步实现，超时风险由 PM 承担",
                "target": {"zone": "body", "section_id": "s8"},
                "type": "single",
                "options": [
                    {"option_id": "a", "text": "少于 1 万"},
                    {"option_id": "b", "text": "1 万–10 万"},
                    {"option_id": "c", "text": "超过 10 万"},
                ],
                "evidence_refs": ["E2"],
                "asked_by": ["seat.data_scale"],
            },
            {
                "kind": "gap",
                "title": "谁能执行导出？",
                "why": "决定权限口径",
                "consequence_if_skipped": "默认只有运营专员",
                "target": {"zone": "body", "section_id": "s3"},
                "type": "single",
                "options": [
                    {"option_id": "a", "text": "运营专员"},
                    {"option_id": "b", "text": "运营 + 主管"},
                ],
                "evidence_refs": ["E1"],
                "asked_by": ["seat.permissions"],
            },
        ],
        "list_items": [],
        "proposals": [],
        "dropped_opinions": SEAT_OPINIONS_ACCOUNTED,
    },
    ensure_ascii=False,
)

DRAFT_REPLY = json.dumps(
    {
        "questions": [],
        "list_items": [],
        "proposals": [
            {
                "op": "add",
                "target": {
                    "zone": "body",
                    "section_id": "s5",
                    "feature_slug": "order-export",
                    "field": "acceptance",
                },
                "text": "AC-1 5 万条以内 60 秒可下载",
                "origin_type": "derived",
                "evidence_refs": ["E1", "E2"],
                "rationale": "量级在 1 万级，同步实现可满足",
            },
            {
                "op": "add",
                "target": {
                    "zone": "body",
                    "section_id": "s5",
                    "feature_slug": "order-export",
                    "field": "exceptions",
                },
                "text": "无导出权限的用户不显示导出入口",
                "origin_type": "derived",
                "evidence_refs": ["E1"],
                "rationale": "订单含客户信息",
            },
            {
                "op": "add",
                "target": {"zone": "body", "section_id": "s8"},
                "text": "单次导出 60 秒内完成",
                "origin_type": "derived",
                "evidence_refs": ["E1"],
                "rationale": "同类导出的常见上限",
                "kind": "nfr",
            },
            {
                "op": "add",
                "target": {
                    "zone": "body",
                    "section_id": "s5",
                    "feature_slug": "order-export",
                    "field": "rules",
                },
                "text": "导出记录保留 30 天",
                "origin_type": "proposed",
                "evidence_refs": ["E1"],
                "rationale": "纯专业判断，没有依据",
            },
            {
                "op": "add",
                "target": {"zone": "preference"},
                "text": "登录页配色偏冷淡风",
                "origin_type": "user",
                "evidence_refs": ["E3"],
            },
        ],
        "dropped_opinions": SEAT_OPINIONS_ACCOUNTED,
    },
    ensure_ascii=False,
)

OPPENING = {"seats": ["seat.data_scale", "seat.permissions"], "claim_edits": []}

# 三条主张的 raw_span 必须都能在这段输入里逐字找到（§3.10 的严格规则）
INPUT = "想做个订单导出的功能，运营每天都要用，数据量大概几万。登录页太丑了，顺便改一下。"


async def create_project(client, *, title: str = "订单中台") -> str:  # type: ignore[no-untyped-def]
    response = await client.post("/api/projects", json={"title": title})
    assert response.status_code == 201
    return response.json()["project_id"]


async def start_round(client, project_id: str, *, user_input: str):  # type: ignore[no-untyped-def]
    response = await client.post(
        f"/api/projects/{project_id}/rounds", json={"input": user_input}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_loop_with_questionnaire_and_a_skipped_question(make_app):
    app, _ = make_app(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [QUESTION_REPLY, DRAFT_REPLY],
        },
        seat_replies={"seat.data_scale": SEAT_REPLY, "seat.permissions": SEAT_REPLY},
    )

    async with async_client(app) as client:
        project_id = await create_project(client)
        started = await start_round(
            client,
            project_id,
            user_input=INPUT,
        )
        assert started["waiting_on"] == "opening_card"
        assert [claim["eid"] for claim in started["claims"]] == ["E1", "E2", "E3"]
        assert started["claims"][0]["label"] == "need"
        # 开场卡的席位提名（F8）：名单是**分析者挑的**，不是目录匹配的结果。
        # 真目录里 5 个席位是 always_on，但分析者这轮只挑了这两个 →
        # 名单只该有两个，其余 5 个以"还能加的"身份出现，并且每条都带分析者的理由。
        nominated = {seat["seat_id"]: seat for seat in started["nominated_seats"]}
        assert set(nominated) == {"seat.export_import", "seat.data_scale"}
        assert all(seat["source"] == "agent" for seat in nominated.values())
        assert all(seat["why"] for seat in nominated.values())
        available = {seat["seat_id"] for seat in started["available_seats"]}
        assert "seat.business_rules" in available  # 常驻但没被挑 → 想加还能加
        assert set(available) & set(nominated) == set()

        opened = await client.post(
            f"/api/rounds/{started['round_id']}/opening", json=OPPENING
        )
        assert opened.status_code == 200, opened.text
        opened_body = opened.json()
        assert opened_body["waiting_on"] == "questionnaire"
        assert [packet["status"] for packet in opened_body["seat_packets"]] == [
            "completed",
            "completed",
        ]
        assert opened_body["questions"][0]["kind"] == "gap"
        assert opened_body["questions"][0]["options"][1] == ["b", "1 万–10 万"]
        assert opened_body["questions"][0]["consequence_if_skipped"]

        answered = await client.post(
            f"/api/rounds/{started['round_id']}/answers",
            json={
                "form_id": opened_body["form_id"],
                "answers": [{"question_id": "q1", "selected": ["b"], "text": ""}],
                "skipped": ["q2"],
            },
        )
        assert answered.status_code == 200, answered.text
        answered_body = answered.json()

        assert answered_body["waiting_on"] == "draft_card"
        assert answered_body["seat_round_count"] == 2  # 答案回来重新跑过一次席位轮
        groups = dict(answered_body["draft"]["groups"])
        assert set(groups) == {"你说过的", "它替你判断的", "它提议的"}
        assert len(groups["它提议的"]) == 1
        assert groups["它提议的"][0]["rationale"] is not None
        assert groups["它提议的"][0]["warning"] is None or isinstance(
            groups["它提议的"][0]["warning"], str
        )
        # 跳过的那道题落成 s11 开放问题
        open_questions = [
            proposal
            for proposal in answered_body["draft"]["proposals"]
            if proposal["target_label"] == "s11"
        ]
        assert len(open_questions) == 1
        assert "谁能执行导出？" in open_questions[0]["text"]
        assert "默认只有运营专员" in open_questions[0]["text"]  # 后果取题面原句
        # 每条提案都带出处编号（可追溯）
        assert all(proposal["eids"] for proposal in answered_body["draft"]["proposals"])

        accepted = [
            proposal["proposal_id"]
            for proposal in answered_body["draft"]["proposals"]
            if proposal["origin_type"] != "proposed"
        ]
        confirmed = await client.post(
            f"/api/rounds/{started['round_id']}/confirm", json={"accepted": accepted}
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["version_no"] == 2
        assert confirmed.json()["round"]["step"] == "done"

        workspace = (await client.get(f"/api/projects/{project_id}/workspace")).json()
        assert workspace["project"]["version_no"] == 2
        assert workspace["document"]["version_no"] == 2
        sections = {section["section_id"]: section for section in workspace["document"]["sections"]}
        feature = next(
            block for block in sections["s5"]["blocks"] if block["kind"] == "feature"
        )
        assert feature["feature_slug"] == "order-export"
        fields = {child["field"] for child in feature["children"]}
        assert {"acceptance", "exceptions"} <= fields
        assert "rules" not in fields  # 提议那条没被采纳
        # s8 的 nfr 写进去了：按"有非空条目"判（空槽是骨架，别让它冒充内容）
        assert any(block["entries"] for block in sections["s8"]["blocks"])
        assert workspace["document"]["preference_entries"]  # 偏好进偏好区，不进 13 章


async def test_loop_without_questionnaire_goes_straight_to_draft(make_app):
    app, _ = make_app(
        replies={"claims": CLAIMS_REPLY, "integration": DRAFT_REPLY},
        seat_replies={"seat.data_scale": SEAT_REPLY},
    )

    async with async_client(app) as client:
        project_id = await create_project(client)
        started = await start_round(client, project_id, user_input=INPUT)
        opened = await client.post(
            f"/api/rounds/{started['round_id']}/opening",
            json={"seats": ["seat.data_scale"], "claim_edits": []},
        )

    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["waiting_on"] == "draft_card"  # 没有必须问的 → 整段跳过问卷
    assert body["questions"] == []
    assert body["draft"] is not None


async def test_ai_cannot_write_the_document_until_confirm(make_app):
    app, _ = make_app(
        replies={"claims": CLAIMS_REPLY, "integration": [QUESTION_REPLY, DRAFT_REPLY]},
        seat_replies={"seat.data_scale": SEAT_REPLY},
    )

    async with async_client(app) as client:
        project_id = await create_project(client)
        started = await start_round(client, project_id, user_input=INPUT)
        await client.post(
            f"/api/rounds/{started['round_id']}/opening",
            json={"seats": ["seat.data_scale"], "claim_edits": []},
        )
        document = (await client.get(f"/api/projects/{project_id}/document")).json()

    assert document["version_no"] == 1  # 全程没确认 → 版本没动
    # 零写入 = 正文一条内容都没有。s8 的六个空槽是骨架（不是内容），所以按 entries 判。
    assert all(
        not block["entries"]
        for section in document["sections"]
        for block in section["blocks"]
    )


async def test_seat_with_no_opinions_is_allowed_when_basis_is_given(make_app):
    """空意见不等于没意见：basis 非空就允许（learnings L2 的反面）。"""

    app, _ = make_app(
        replies={"claims": CLAIMS_REPLY, "integration": QUESTION_REPLY},
        seat_replies={
            "seat.data_scale": json.dumps(
                {"basis": "读了全文，本领域没有要补的", "opinions": []}, ensure_ascii=False
            )
        },
    )

    async with async_client(app) as client:
        project_id = await create_project(client)
        started = await start_round(client, project_id, user_input=INPUT)
        opened = await client.post(
            f"/api/rounds/{started['round_id']}/opening",
            json={"seats": ["seat.data_scale"], "claim_edits": []},
        )

    assert opened.status_code == 200, opened.text
    assert opened.json()["seat_packets"][0]["judgment_count"] == 0
    assert opened.json()["seat_packets"][0]["status"] == "completed"


async def test_unparsable_seat_packet_fails_the_round_and_writes_nothing(make_app):
    app, _ = make_app(
        replies={"claims": CLAIMS_REPLY},
        seat_replies={"seat.data_scale": "我建议先问问用户"},  # 不是 JSON
    )

    async with async_client(app) as client:
        project_id = await create_project(client)
        started = await start_round(client, project_id, user_input=INPUT)
        response = await client.post(
            f"/api/rounds/{started['round_id']}/opening",
            json={"seats": ["seat.data_scale"], "claim_edits": []},
        )
        round_body = (await client.get(f"/api/rounds/{started['round_id']}")).json()
        document = (await client.get(f"/api/projects/{project_id}/document")).json()

    assert response.status_code == 502
    assert round_body["step"] == "failed"
    assert round_body["failure_reason"]
    assert document["version_no"] == 1
