"""纠错指引（重装时喂给模型的第二段话）：只给要求，不回喂模型写错的那串内容。"""

from pmstudio.application.errors import PacketParseError
from pmstudio.application.repair import repair_note

#: 真模型在 `edge-long` 上写进去过的那串说明（连括号一起抄进 raw_span）
SPAN = "（隐含于上下文，仅提及订单导出）"


def test_ungrounded_span_points_at_the_claim_and_never_echoes_it():
    note = repair_note(
        PacketParseError(
            "主张里的原话片段在用户输入里找不到（复述必须能跳回原文）",
            purpose="claims",
            claim_id="c4",
            position=4,
            raw_span=SPAN,
        )
    )

    assert "第 4 条主张（c4）" in note
    assert "找不到" in note
    assert "逐字" in note
    assert SPAN not in note  # 复制回去等于给它一个可以照抄的模板


def test_seat_id_outside_the_catalog_gets_the_prefix_rule():
    # 坏值特意选一个**不会与指引里的正确示例撞子串**的（`tech_approach` 是
    # `seat.tech_approach` 的后缀，拿它当坏值就分不清"回喂了写错的串"和"给了正确示例"）
    note = repair_note(
        PacketParseError(
            "席位提名里出现了目录里没有的席位",
            purpose="claims",
            seat_id="seat.frontend_interactions",
            position=1,
        )
    )

    assert "原样照抄" in note
    assert "`seat.`" in note
    assert "frontend_interactions" not in note


def test_schema_failure_lists_the_field_paths():
    note = repair_note(
        PacketParseError(
            "模型返回的报文过不了 schema",
            purpose="claims",
            errors=[
                {
                    "loc": ("claims", 2, "label"),
                    "msg": "Input should be 'fact', 'need', …",
                    "type": "literal_error",
                }
            ],
        )
    )

    assert "claims.2.label" in note


def test_broken_json_asks_for_a_plain_json_object():
    note = repair_note(
        PacketParseError(
            "模型返回的不是合法 JSON：Expecting value", purpose="claims", offset=0
        )
    )

    assert "JSON" in note
    assert "围栏" in note


def test_overflow_tells_the_model_how_many_to_cut():
    """超限是**可修复的错**：指引要说清"你给了几条、上限几条、怎么精简"。"""

    note = repair_note(
        PacketParseError(
            "模型返回的报文过不了 schema",
            purpose="integration",
            overflow=("proposals", 44, 60),
        )
    )

    assert "44" in note and "60" in note
    assert "proposals" in note
    assert "精简" in note


def test_packet_shape_tells_the_model_the_object_it_owes():
    """真跑实测：模型把整包回成一个数组（留样 15 / 66）→ 指引要说清它欠一个对象。"""

    note = repair_note(
        PacketParseError(
            "模型把整包返回成了数组，无法唯一归位到任何一类内容",
            purpose="integration",
            packet_shape={
                "got": "array",
                "expected_keys": (
                    "questions",
                    "list_items",
                    "proposals",
                    "dropped_opinions",
                ),
            },
        )
    )

    assert "数组" in note and "对象" in note
    assert "questions" in note and "dropped_opinions" in note


def schema_error(kind: str, loc: tuple, **ctx: object) -> dict:
    return {"type": kind, "loc": loc, "msg": "…", "ctx": ctx}


def test_list_type_error_says_it_wants_a_list():
    note = repair_note(
        PacketParseError(
            "模型返回的报文过不了 schema",
            purpose="seat_review",
            errors=[schema_error("list_type", ("opinions", 2, "evidence_refs"))],
        )
    )

    assert "opinions.2.evidence_refs" in note
    assert "数组" in note
    assert "[]" in note  # 没有依据就写空数组


def test_missing_field_says_which_one():
    note = repair_note(
        PacketParseError(
            "模型返回的报文过不了 schema",
            purpose="claims",
            errors=[schema_error("missing", ("seats",), field_type="list")],
        )
    )

    assert "seats" in note
    assert "必填" in note


def test_literal_error_lists_the_allowed_values():
    note = repair_note(
        PacketParseError(
            "模型返回的报文过不了 schema",
            purpose="seat_review",
            errors=[
                schema_error(
                    "literal_error",
                    ("opinions", 0, "form"),
                    expected="'question' or 'judgment'",
                )
            ],
        )
    )

    assert "opinions.0.form" in note
    assert "question" in note and "judgment" in note


def test_a_missing_kind_gets_the_three_values_and_the_criterion():
    """`kind` 写不出来时，重装那一次要把三种取值与判据一起递过去（P11 C3）。

    只报"是必填字段"它只会随便挑一个（真跑里 8/15 道题干脆没写）。
    """

    note = repair_note(
        PacketParseError(
            "模型返回的报文过不了 schema",
            purpose="integration",
            errors=[schema_error("missing", ("questions", 0, "kind"))],
        )
    )

    assert "questions.0.kind" in note
    assert "gap" in note and "conflict" in note and "decision" in note
    assert "互斥" in note


def test_a_non_object_item_says_every_item_must_be_an_object():
    """真跑报告 F17：`opinions[1]` 是一个占位符字符串。

    重装那一次要说清两件事：**哪一条**、**该是什么形状**。
    不许把模型写的那串占位符回喂回去（learnings L20：回喂等于给它一个可以照抄的模板）。
    """

    note = repair_note(
        PacketParseError(
            "模型返回的报文过不了 schema",
            purpose="seat_review",
            discarded=("opinions", 2),
        )
    )

    assert "opinions" in note
    assert "第 2 条" in note
    assert "不是对象" in note
    assert "占位符" in note
    assert "placeholder" not in note
