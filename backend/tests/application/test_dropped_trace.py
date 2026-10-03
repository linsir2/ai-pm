"""留痕通道的端到端：模型申报的丢弃、归因对不上、没交代去向，三条都要留得下来。

这一组测试对着的是真踩过的洞：留痕只在"出问卷"那条路上被写进轮次，
信息明确、直接出草案的一轮里全部消失（P5b §0.1 发现 2）。
"""

import json

from pmstudio.domain.round.round import DropSource
from tests.support.builders import PROJECT, SEAT
from tests.support.harness import Harness

CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了空文档",
        "claims": [
            {
                "text": "导出要快",
                "raw_span": "导出要快一点",
                "label": "constraint",
                "zone": "body",
                "section_id": "s8",
            }
        ],
        "seats": [{"seat_id": SEAT, "why": "导出要评估数据量与耗时"}],
    },
    ensure_ascii=False,
)

# 一条疑问（不进账本、没有编号）+ 一条判断（进账本，拿到 E2）
SEAT_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 与 E1",
        "opinions": [
            {
                "local_id": "O1",
                "form": "question",
                "about": "导出的数据量级",
                "claim": "量级决定同步还是异步，必须问用户",
                "because": "1 万以内可同步，10 万以上必须异步",
                "evidence_refs": ["E1"],
            },
            {
                "local_id": "O2",
                "form": "judgment",
                "about": "导出的性能上限",
                "claim": "单次导出应在 60 秒内完成",
                "because": "同类导出的常见上限",
                "evidence_refs": ["E1"],
            },
        ],
    },
    ensure_ascii=False,
)


def draft_reply(*, refs: list[str], dropped: list[dict[str, str]]) -> str:
    """"直接出草案"的整合报文：命题只引用 refs，丢弃只申报 dropped。"""

    return json.dumps(
        {
            "questions": [],
            "list_items": [],
            "dropped_opinions": dropped,
            "proposals": [
                {
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s8"},
                    "text": "单次导出应在 60 秒内完成",
                    "origin_type": "derived",
                    "evidence_refs": refs,
                    "rationale": "同类导出的常见上限",
                }
            ],
        },
        ensure_ascii=False,
    )


def harness_with(*, integration: str) -> Harness:
    return Harness(
        replies={"claims": CLAIMS_REPLY, "integration": integration},
        seat_replies={SEAT: SEAT_REPLY},
    )


def dispositions_reply(*handles: str) -> str:
    """归因补交代那条**小调用**的回包：只申报"我没采用的"（`02` §11.0.38）。

    不传 handle = "这些我都用上了"——一个都不列。**没列的就仍算没交代**，
    所以调用方要看清自己写的是哪种剧本。
    """

    return json.dumps(
        {
            "dropped_opinions": [
                {"handle": handle, "reason": "本轮先不写，留给下一轮"} for handle in handles
            ]
        },
        ensure_ascii=False,
    )


def dispositions_calls(harness) -> int:  # type: ignore[no-untyped-def]
    return [request.purpose for request in harness.gateway.requests].count(
        "opinion_dispositions"
    )


async def run_to_integrated(harness: Harness):
    """走一遍：建轮 → 抽主张 → 交席位编制 → 跑席位 → 整合。"""

    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="导出要快一点")
    await start.analyze(round_id=round_id)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)
    return round_id


async def test_draft_path_keeps_the_drop_trace():
    """不出问卷的那条路：模型申报的丢弃必须留在轮次上（旧实现这里全丢）。"""

    harness = harness_with(
        integration=draft_reply(
            refs=["E2"],  # 命题引用了那条判断 → 它在"被交代"那一边
            dropped=[{"handle": f"{SEAT}:O1", "reason": "与文档已有内容重复"}],
        )
    )

    round_id = await run_to_integrated(harness)

    round_ = harness.rounds.get(round_id)
    assert round_.draft is not None  # 确认走的确实是"出草案"那条路
    assert [(item.handle, item.reason, item.source) for item in round_.dropped] == [
        (f"{SEAT}:O1", "与文档已有内容重复", DropSource.INTEGRATOR)
    ]


async def test_unaccounted_opinion_is_reported_in_the_round():
    """整合一条也没引用 → 两条都报"没交代去向"（不靠模型自觉）。"""

    harness = harness_with(integration=draft_reply(refs=["E1"], dropped=[]))

    round_id = await run_to_integrated(harness)

    round_ = harness.rounds.get(round_id)
    assert {item.handle for item in round_.dropped} == {f"{SEAT}:O1", f"{SEAT}:O2"}
    assert {item.source for item in round_.dropped} == {DropSource.UNACCOUNTED}
    # F5：留痕要带上"这条意见说了什么"，用户才查得到丢的是哪句话
    by_handle = {item.handle: item.reason for item in round_.dropped}
    assert "导出的数据量级" in by_handle[f"{SEAT}:O1"]
    assert "量级决定同步还是异步，必须问用户" in by_handle[f"{SEAT}:O1"]
    assert "导出的性能上限" in by_handle[f"{SEAT}:O2"]


async def test_judgment_accounted_by_a_proposal_still_leaves_the_question_reported():
    """判断被引用（引用它自己的编号 E2）就算交代过；疑问没人提 → 只有它报。"""

    harness = harness_with(integration=draft_reply(refs=["E2"], dropped=[]))

    round_id = await run_to_integrated(harness)

    round_ = harness.rounds.get(round_id)
    assert [(item.handle, item.source) for item in round_.dropped] == [
        (f"{SEAT}:O1", DropSource.UNACCOUNTED)
    ]


async def test_unaccounted_reason_says_which_form_it_was():
    """报告 F19：原话术一律是"整合既没说丢掉它，也没交代它落到了哪儿"——用户看了等于没看。

    形态是领域对象自带的（疑问 / 判断），按它分两句**不猜任何东西**：
    疑问该说的是"没被变成题"，判断该说的是"没进任何提案或题目"。
    """

    harness = harness_with(integration=draft_reply(refs=["E1"], dropped=[]))

    round_id = await run_to_integrated(harness)

    by_handle = {item.handle: item.reason for item in harness.rounds.get(round_id).dropped}
    assert "没被变成题" in by_handle[f"{SEAT}:O1"]
    assert "没进任何提案或题目" in by_handle[f"{SEAT}:O2"]


def six_questions_reply(*, sixth_refs: list[str]) -> str:
    """6 道题（上限 5）：第 6 道会被挤进轮内队列，只有它引用了那条判断的编号。"""

    def one(index: int, *, section: str, refs: list[str]) -> dict:
        return {
            "kind": "gap",
            "title": f"第 {index} 件事怎么定",
            "why": "只有用户能定",
            "consequence_if_skipped": "先按默认处理",
            "target": {"zone": "body", "section_id": section},
            "type": "single",
            "options": [
                {"option_id": "a", "text": "甲"},
                {"option_id": "b", "text": "乙"},
            ],
            "evidence_refs": refs,
            "from_opinions": [],
        }

    return json.dumps(
        {
            "questions": [one(index, section="s1", refs=["E1"]) for index in range(1, 6)]
            + [one(6, section="s2", refs=sixth_refs)],
            "list_items": [],
            "dropped_opinions": [],
            "proposals": [],
        },
        ensure_ascii=False,
    )


async def test_a_judgment_cited_only_by_a_deferred_question_is_accounted():
    """报告 F19 的漏算：本批没排上的题**已经产出**了，它引用的判断当然算"交代过"。

    以前只把"保留下来的那 5 道题"当产出（`produced_questions=assembled.questions`），
    被挤进轮内队列的第 6 道题里的引用整条被漏掉 → 那条判断被误报"没人交代"。
    """

    harness = harness_with(integration=six_questions_reply(sixth_refs=["E2"]))

    round_id = await run_to_integrated(harness)

    round_ = harness.rounds.get(round_id)
    assert len(round_.deferred) == 1  # 确实有一道被挤到本批之外
    assert {item.handle for item in round_.dropped} == {f"{SEAT}:O1"}  # 疑问照旧报，判断被交代了


async def test_a_silently_unused_opinion_is_still_reported():
    """反例必须留着：既没被引用、也没写地址、也没申报 → 照旧报，不许因为改了话术就放松。"""

    harness = harness_with(integration=draft_reply(refs=["E1"], dropped=[]))

    round_id = await run_to_integrated(harness)

    handles = {item.handle for item in harness.rounds.get(round_id).dropped}
    assert handles == {f"{SEAT}:O1", f"{SEAT}:O2"}


# —— F19 的机械兜底：没交代的意见，点名要模型补一次（`02` §11.0.37） ——


def integration_calls(harness) -> int:  # type: ignore[no-untyped-def]
    return [request.purpose for request in harness.gateway.requests].count("integration")


async def test_an_unaccounted_opinion_triggers_exactly_one_targeted_repair():
    """模型一次没交代全 → 系统用**小调用**问一次 → 它补上 → 最终不留 unaccounted。

    P24 之前这里是"再发一次整包"，真跑证明那样会补一批又漏一批（P23）；
    现在补交代走小调用，**整包只发一次**。
    """

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": draft_reply(refs=["E1"], dropped=[]),
            "opinion_dispositions": dispositions_reply(f"{SEAT}:O1", f"{SEAT}:O2"),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )

    round_id = await run_to_integrated(harness)

    round_ = harness.rounds.get(round_id)
    assert integration_calls(harness) == 1  # 整包只发一次
    assert dispositions_calls(harness) == 1  # 补交代走小调用
    assert [item.source for item in round_.dropped] == [DropSource.INTEGRATOR] * 2
    named = harness.prompts.last("opinion_dispositions")["opinions"]
    assert f"{SEAT}:O1" in named and f"{SEAT}:O2" in named


async def test_the_disposition_loop_stops_at_two():
    """补交代额度是 2：报了一条（有进展）→ 再问一次；第二次不报 → 收手。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": draft_reply(refs=["E1"], dropped=[]),
            "opinion_dispositions": [
                dispositions_reply(f"{SEAT}:O1"),
                dispositions_reply(),
            ],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )

    round_id = await run_to_integrated(harness)

    assert integration_calls(harness) == 1
    assert dispositions_calls(harness) == 2
    handles = {item.handle for item in harness.rounds.get(round_id).dropped}
    assert handles == {f"{SEAT}:O1", f"{SEAT}:O2"}  # O1 被申报，O2 仍算没交代


async def test_a_disposition_that_makes_no_progress_stops_early():
    """第一次补交代**一条都没补**（点数没降）→ 不再花第二次（`02` §11.0.38 的护栏）。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": draft_reply(refs=["E1"], dropped=[]),
            "opinion_dispositions": dispositions_reply(),  # 一个都不列 = 没进展
        },
        seat_replies={SEAT: SEAT_REPLY},
    )

    round_id = await run_to_integrated(harness)

    assert dispositions_calls(harness) == 1
    assert {item.handle for item in harness.rounds.get(round_id).dropped} == {
        f"{SEAT}:O1",
        f"{SEAT}:O2",
    }


async def test_no_targeted_repair_when_everything_is_accounted():
    """一次就交代全 → **不许白花一次调用**。"""

    harness = harness_with(
        integration=draft_reply(
            refs=["E2"], dropped=[{"handle": f"{SEAT}:O1", "reason": "与文档重复"}]
        )
    )

    await run_to_integrated(harness)

    assert integration_calls(harness) == 1
    assert dispositions_calls(harness) == 0


async def test_a_disposition_about_a_handle_we_did_not_name_is_ignored():
    """小调用只能影响它被点名的那几条：多写的 handle 不并入（不给留痕添噪音）。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": draft_reply(refs=["E1"], dropped=[]),
            "opinion_dispositions": dispositions_reply(f"{SEAT}:O1", f"{SEAT}:O9"),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )

    round_id = await run_to_integrated(harness)

    handles = {item.handle for item in harness.rounds.get(round_id).dropped}
    assert handles == {f"{SEAT}:O1", f"{SEAT}:O2"}  # O9 不是这一轮的条目，被忽略


async def test_a_failed_disposition_call_does_not_fail_the_round():
    """补交代是**锦上添花**：这一步挂了不该把一整轮判死。

    这条用例故意**不给** `opinion_dispositions` 写脚本——假网关没有脚本时直接抛错
    （不许返回空串），正好模拟"这一步失败了"。
    """

    harness = harness_with(integration=draft_reply(refs=["E1"], dropped=[]))

    round_id = await run_to_integrated(harness)

    round_ = harness.rounds.get(round_id)
    assert round_.draft is not None  # 轮次照旧走到草案卡
    assert dispositions_calls(harness) == 1  # 试过一次
    assert {item.handle for item in round_.dropped} == {f"{SEAT}:O1", f"{SEAT}:O2"}


async def test_the_accounting_repair_does_not_eat_the_validation_budget():
    """解析错与"没交代"是两本账：解析错重装一次（整包）之后，补交代照样有小调用的额度。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            # 第一发编了个不存在的编号 → 校验重装；第二发过了但两条都没交代
            "integration": [
                draft_reply(refs=["E999"], dropped=[]),
                draft_reply(refs=["E1"], dropped=[]),
            ],
            "opinion_dispositions": dispositions_reply(f"{SEAT}:O1", f"{SEAT}:O2"),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )

    round_id = await run_to_integrated(harness)

    assert integration_calls(harness) == 2  # 一发失败 + 一发成功
    assert dispositions_calls(harness) == 1
    assert {item.source for item in harness.rounds.get(round_id).dropped} == {
        DropSource.INTEGRATOR
    }


async def test_a_judgment_cited_only_by_a_deferred_question_is_not_a_repair_trigger():
    """判定与写库取的是**同一份账**：被第 6 道（没排上的）题引用的判断，不算"没交代"。

    这一轮里真正没交代的是那条疑问（O1：没人写它的地址、也没申报丢弃），所以点名重装
    该只为 O1 发生——O2（被挤到队列里的那道题引用了它的编号）不许出现在点名名单里。
    """

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": six_questions_reply(sixth_refs=["E2"]),
            "opinion_dispositions": dispositions_reply(),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )

    round_id = await run_to_integrated(harness)

    assert integration_calls(harness) == 1
    assert dispositions_calls(harness) == 1
    named = harness.prompts.last("opinion_dispositions")["opinions"]
    assert f"{SEAT}:O1" in named
    assert f"{SEAT}:O2" not in named
    assert {item.handle for item in harness.rounds.get(round_id).dropped} == {f"{SEAT}:O1"}


def overflow_reply(count: int) -> str:
    """一次交太多：真跑里 44 条提案把整轮打死（报告 F2）。"""

    return json.dumps(
        {
            "questions": [],
            "list_items": [],
            "dropped_opinions": [],
            "proposals": [
                {
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s1"},
                    "text": f"第 {index} 条",
                    "origin_type": "derived",
                    "evidence_refs": ["E1"],
                }
                for index in range(count)
            ],
        },
        ensure_ascii=False,
    )


async def test_overflow_items_land_in_dropped_and_gaps():
    """超限截断的那部分：留痕里说得清，草案卡缺口行上也看得见（不静默丢）。"""

    harness = harness_with(integration=overflow_reply(70))

    round_id = await run_to_integrated(harness)

    round_ = harness.rounds.get(round_id)
    assert round_.draft is not None
    assert len(round_.draft.proposals) == 60
    assert any("超出单轮上限" in item.reason for item in round_.dropped)
    assert any("因单轮上限未进卡片" in gap for gap in round_.draft.gaps)


def harness_with_replies(*, integration: str | list[str]) -> Harness:
    """与 `harness_with` 一样，只是整合那一步可以给一串回复（测重装）。"""

    return Harness(
        replies={"claims": CLAIMS_REPLY, "integration": integration},
        seat_replies={SEAT: SEAT_REPLY},
    )


async def test_integration_repacks_when_the_packet_is_an_array():
    """真跑实测：第一次回数组就立刻 502，**根本没有第二次调用**（留样 15 与 16 差 55 秒）。

    修好之后：第一次是归不了位的数组（元素 `{}`）→ 判死前先重装 → 第二次写对 → 整轮照走。
    """

    # 第二次写成**完全交代过**的一份（引用 E2 交代判断、申报疑问）——这条用例只考
    # "形状错重装一次"，不让归因重装混进来（那是另一条用例的事）。
    harness = harness_with_replies(
        integration=[
            "[{}]",
            draft_reply(
                refs=["E2"], dropped=[{"handle": f"{SEAT}:O1", "reason": "与文档重复"}]
            ),
        ]
    )

    round_id = await run_to_integrated(harness)

    round_ = harness.rounds.get(round_id)
    assert round_.draft is not None  # 没判死
    integrations = [
        request for request in harness.gateway.requests if request.purpose == "integration"
    ]
    assert len(integrations) == 2
    # 重装带上了形状指引（取**整合者**那次渲染：一轮里记忆整理者也会渲染一次）
    assert "数组" in harness.prompts.last("integrator")["validation_error"]


async def test_integration_still_takes_the_graceful_exit_after_two_bad_shapes():
    """两次都是数组 → 与内容错**同一个出口**（有席位疑问就降级成 s11 草案，不判死）。"""

    harness = harness_with_replies(integration=["[{}]", "[{}]"])

    round_id = await run_to_integrated(harness)

    round_ = harness.rounds.get(round_id)
    assert round_.draft is not None
    assert any("未归并" in gap for gap in round_.draft.gaps)
    assert len(
        [request for request in harness.gateway.requests if request.purpose == "integration"]
    ) == 2


async def test_degraded_questions_count_as_accounted_but_judgments_do_not():
    """整合失败时，疑问原样落 s11（算交代过），没落地的判断仍然报"没交代"。"""

    # 报文故意引用不存在的编号（E9）：两次都过不了校验 → 走降级出口
    harness = harness_with(integration=draft_reply(refs=["E9"], dropped=[]))

    round_id = await run_to_integrated(harness)

    round_ = harness.rounds.get(round_id)
    assert [(item.handle, item.source) for item in round_.dropped] == [
        (f"{SEAT}:O2", DropSource.UNACCOUNTED)
    ]


async def test_dropped_trace_accumulates_across_passes():
    """第二趟（答完问卷再跑）的留痕不会把第一趟的覆盖掉。"""

    question_reply = json.dumps(
        {
            "questions": [
                {
                    "kind": "gap",
                    "title": "单次导出的最大行数是多少？",
                    "why": "量级决定同步还是异步",
                    "consequence_if_skipped": "按同步实现",
                    "target": {"zone": "body", "section_id": "s8"},
                    "type": "text",
                    "evidence_refs": ["E1"],
                    "from_opinions": [f"{SEAT}:O1"],
                }
            ],
            "list_items": [],
            "proposals": [],
            # 这条用例考的是"留痕跨趟累加"，所以第一趟就把判断 O2 申报掉——
            # 不让归因重装（§11.0.37）在这一趟插进来改变剧本。
            "dropped_opinions": [{"handle": f"{SEAT}:O2", "reason": "无依据，按提议处理"}],
        },
        ensure_ascii=False,
    )
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [question_reply, draft_reply(refs=["E2"], dropped=[])],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )

    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="导出要快一点")
    await start.analyze(round_id=round_id)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)

    first = harness.rounds.get(round_id)
    assert first.questionnaire is not None
    first_pass = [(item.handle, item.pass_index) for item in first.dropped]
    # 第一趟：疑问 O1 被题点过（算交代），判断 O2 没人引用 → 报"没交代"
    assert first_pass == [(f"{SEAT}:O2", 1)]

    await harness.submit_answers()(
        round_id=round_id,
        form_id=str(first.questionnaire.form_id),
        answers=(),
        skipped=(str(first.questionnaire.questions[0].question_id),),
    )
    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)

    passes = [(item.handle, item.pass_index) for item in harness.rounds.get(round_id).dropped]

    assert passes[: len(first_pass)] == first_pass  # 第一趟的留痕还在
    assert len(passes) > len(first_pass)  # 第二趟又留了新的


# —— P23 B：整合报文的非对象元素（先给它补上留痕出口，再换 lenient 解析） ——


def packet_with_a_placeholder_proposal() -> str:
    """一份"能出草案"的报文，但第二条提案是个占位符字符串。"""

    payload = json.loads(
        draft_reply(refs=["E2"], dropped=[{"handle": f"{SEAT}:O1", "reason": "重复"}])
    )
    payload["proposals"].append("p2_placeholder")
    return json.dumps(payload, ensure_ascii=False)


async def test_a_placeholder_proposal_is_repacked_then_dropped_with_a_trace():
    """两次都带占位符 → 丢掉那一条 + 留痕 + 草案卡缺口行，**整轮继续**（不判死、不 409）。"""

    harness = harness_with(integration=packet_with_a_placeholder_proposal())

    round_id = await run_to_integrated(harness)

    round_ = harness.rounds.get(round_id)
    assert round_.draft is not None  # 没判死
    assert [item.text for item in round_.draft.proposals] == ["单次导出应在 60 秒内完成"]
    assert [
        (item.handle, item.source)
        for item in round_.dropped
        if item.source is DropSource.ASSEMBLY
    ] == [("提案 #2（不是对象）", DropSource.ASSEMBLY)]
    assert any("形状不对" in gap for gap in round_.draft.gaps)
    assert integration_calls(harness) == 2  # 一次正常 + 一次形状重装


async def test_a_placeholder_proposal_is_repacked_and_can_be_saved():
    """第一次带占位符、第二次干净 → 只留一次调用，没有任何丢弃留痕。"""

    harness = harness_with_replies(
        integration=[
            packet_with_a_placeholder_proposal(),
            draft_reply(
                refs=["E2"], dropped=[{"handle": f"{SEAT}:O1", "reason": "重复"}]
            ),
        ]
    )

    round_id = await run_to_integrated(harness)

    round_ = harness.rounds.get(round_id)
    assert integration_calls(harness) == 2
    assert [item.source for item in round_.dropped] == [DropSource.INTEGRATOR]
    assert not any("形状不对" in gap for gap in round_.draft.gaps)
