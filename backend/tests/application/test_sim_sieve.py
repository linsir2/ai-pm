"""模拟调研机械筛单测（M10-T4）：窄词表 / 归一化 / 去重 / source_ref / 多块合成。

全是纯函数，不碰模型不碰库。调用方（RunSimInquiry/KeepSimInquiry）只负责
"拿账本/文档算集合、把丢条记进 Round.dropped"，判断逻辑全在这里，一处说了算。
"""

from pmstudio.application.sim_inquiry import (
    ORIGIN_SIMULATION,
    duplicate_of,
    normalize_statement,
    sim_demographic_hits,
    sim_source_ref,
    synthesize_sim_material,
)


def test_demographic_table_hits_identity_words() -> None:
    assert sim_demographic_hits("35 岁一线城市女性运营更爱用导出") != ()
    assert sim_demographic_hits("本科以上学历优先") != ()
    assert sim_demographic_hits("运营月底要对账") == ()


def test_demographic_table_does_not_catch_numbers_or_units() -> None:
    """岁/秒不沾边：数字断言是 _NUMERIC 的活（exploration 那边），这里只抓身份词。"""

    assert sim_demographic_hits("单次导出应在 60 秒内完成") == ()
    assert sim_demographic_hits("支持 5000 行大批量") == ()


def test_normalize_statement_squeezes_and_strips_edges() -> None:
    assert normalize_statement("  运营  按客户名  找单？ ") == "运营 按客户名 找单"
    assert normalize_statement("导出！") == "导出"


def test_duplicate_of_matches_document_first() -> None:
    assert duplicate_of("运营按客户名找单", doc_keys=frozenset({"运营按客户名找单"}), open_keys=frozenset()) == "document"
    assert duplicate_of("没见过的话", doc_keys=frozenset({"运营按客户名找单"}), open_keys=frozenset()) is None


def test_duplicate_of_matches_open_items() -> None:
    assert (
        duplicate_of(
            "留存期限是多久",
            doc_keys=frozenset(),
            open_keys=frozenset({"留存期限是多久"}),
        )
        == "open_item"
    )


def test_sim_source_ref_is_stable_per_selection() -> None:
    """同选择同 ref（幂等靠它），不同选择不同 ref（换勾选就是新材料）。"""

    assert sim_source_ref("r1", ("H1", "U1")) == sim_source_ref("r1", ("U1", "H1"))
    assert sim_source_ref("r1", ("H1",)) != sim_source_ref("r1", ("H1", "U1"))
    assert sim_source_ref("r1", ("H1",)).startswith("simulation:inquiry:r1#")


def test_synthesize_sim_material_makes_one_chunk_per_item() -> None:
    """多块合成：勾中几条就是几个 chunk（单 chunk 信封塞不下多条，这个函数是唯一入口）。"""

    parsed, content = synthesize_sim_material(
        round_id="r1",
        chosen=("H1", "U1"),
        hypotheses=(
            {
                "local_id": "H1",
                "statement": "运营宁愿等一分钟也不愿分两次导",
                "grounding": "document",
                "based_on": ["E1"],
                "falsification": "观察到运营主动分多次导出",
                "evidence_needed": "跟两场结账",
            },
        ),
        unknowns=(
            {
                "local_id": "U1",
                "statement": "节假日前的导出量会不会爆",
                "why_it_matters": "决定要不要做异步",
                "how_to_find_out": "拉去年日志",
            },
        ),
        now="2026-10-06T10:00:00",
    )

    assert parsed.origin == ORIGIN_SIMULATION == "simulation"
    assert parsed.source_ref == sim_source_ref("r1", ("H1", "U1"))
    assert len(parsed.chunks) == 2
    assert parsed.chunks[0].chunk_id == parsed.source_ref + "#c00"
    assert "待验证假设" in parsed.chunks[0].text
    assert "观察到运营主动分多次导出" in parsed.chunks[0].text
    assert "拉去年日志" in parsed.chunks[1].text
    assert content == "\n\n".join(chunk.text for chunk in parsed.chunks).encode("utf-8")


def test_sim_numeric_hit_catches_quantities() -> None:
    from pmstudio.application.sim_inquiry import sim_numeric_hit

    assert sim_numeric_hit("约 62% 用户会用导出")
    assert sim_numeric_hit("大多数运营都这么干")
    assert not sim_numeric_hit("运营宁愿等一分钟也不愿分两次导")
