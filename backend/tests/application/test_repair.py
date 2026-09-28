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
