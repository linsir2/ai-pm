from pathlib import Path

import pytest

from pmstudio.infrastructure.seats.catalog import TomlSeatCatalog

DEFINITIONS = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "pmstudio"
    / "infrastructure"
    / "seats"
    / "definitions"
)


def test_catalog_reads_every_definition():
    catalog = TomlSeatCatalog(DEFINITIONS)
    seats = catalog.all()
    assert len(seats) == 10
    assert {seat.seat_id for seat in seats} >= {
        "seat.data_scale",
        "seat.permissions",
        "seat.business_rules",
        "seat.frontend_interaction",
        "seat.tech_approach",
    }
    # 触发组（`04-未决.md` C1 的推荐版）：导出导入 / 报表 / 通知 / 状态机 / 第三方集成 / 合规
    assert {seat.seat_id for seat in seats} >= {
        "seat.export_import",
        "seat.notification",
        "seat.state_machine",
        "seat.integration",
        "seat.compliance",
    }
    assert all(
        not seat.always_on
        for seat in seats
        if seat.seat_id
        in {
            "seat.export_import",
            "seat.notification",
            "seat.state_machine",
            "seat.integration",
            "seat.compliance",
        }
    )
    assert all(seat.title and seat.focus for seat in seats)


@pytest.mark.parametrize(
    ("user_input", "expected"),
    [
        ("订单要有审核状态，驳回后能回到草稿", "seat.state_machine"),
        ("要跟 CRM 对接，用开放平台的接口同步", "seat.integration"),
        ("导出的文件里有客户信息，要脱敏并留审计", "seat.compliance"),
    ],
)
def test_catalog_nominates_the_trigger_seats_by_keyword(user_input: str, expected: str):
    catalog = TomlSeatCatalog(DEFINITIONS)
    ids = {seat.seat_id for seat in catalog.nominate(user_input=user_input)}
    assert expected in ids


def test_catalog_nominates_always_on_plus_keyword_hits():
    catalog = TomlSeatCatalog(DEFINITIONS)

    ids = {
        seat.seat_id
        for seat in catalog.nominate(
            user_input="想做个订单导出，导出成 Excel，运营每天都要用"
        )
    }

    assert "seat.data_scale" in ids  # 通用组（always_on）
    assert "seat.export_import" in ids  # 触发词命中"导出"
    assert "seat.notification" not in ids  # 没提到通知


def test_catalog_nominate_keeps_declaration_order_and_no_duplicates():
    catalog = TomlSeatCatalog(DEFINITIONS)
    ids = [seat.seat_id for seat in catalog.nominate(user_input="导出导出，报表也要导出")]
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids, key=catalog.order.index)


def test_catalog_by_id_returns_definition_and_raises_on_unknown():
    catalog = TomlSeatCatalog(DEFINITIONS)
    assert catalog.by_id("seat.data_scale").title == "数据规模与性能"
    with pytest.raises(KeyError):
        catalog.by_id("seat.nope")


def test_catalog_reads_sections_and_fields(tmp_path):
    """席位 TOML 要能声明"负责哪几章 / 哪些功能字段"（`01` F16，缩编要用）。"""

    (tmp_path / "a.toml").write_text(
        'seat_id = "seat.a"\ntitle = "A"\nfocus = "x"\n'
        'sections = ["s5", "s8"]\nfields = ["acceptance"]\n',
        encoding="utf-8",
    )

    seat = TomlSeatCatalog(tmp_path).all()[0]

    assert (seat.sections, seat.fields) == (("s5", "s8"), ("acceptance",))


def test_real_seat_files_all_declare_their_sections():
    """10 个真实席位都要有两项，章节 id 必须合法（防手滑写 s15）。"""

    from pmstudio.domain.shared.sections import SECTION_IDS

    catalog = TomlSeatCatalog(DEFINITIONS)
    valid_sections = {str(item) for item in SECTION_IDS}
    from pmstudio.domain.authoring.schema import FEATURE_FIELD_BY_NAME

    for seat in catalog.all():
        assert seat.sections, seat.seat_id
        assert set(seat.sections) <= valid_sections, seat.seat_id
        assert seat.fields, seat.seat_id
        assert set(seat.fields) <= set(FEATURE_FIELD_BY_NAME), seat.seat_id


# ── 内容感知席位过滤 ────────────────────────────────────────────────


def test_filter_by_input_keeps_all_always_on():
    """always_on 席位无论输入是什么都保留。"""
    catalog = TomlSeatCatalog(DEFINITIONS)
    filtered = catalog.filter_by_input("完全无关的输入文本")
    ids = {s.seat_id for s in filtered}
    assert "seat.business_rules" in ids
    assert "seat.data_scale" in ids
    assert "seat.frontend_interaction" in ids
    assert "seat.permissions" in ids
    assert "seat.tech_approach" in ids


def test_filter_by_input_keyword_match():
    """关键词匹配的席位保留。"""
    catalog = TomlSeatCatalog(DEFINITIONS)
    filtered = catalog.filter_by_input("用户需要导出Excel报表")
    ids = {s.seat_id for s in filtered}
    assert "seat.export_import" in ids


def test_filter_by_input_filters_irrelevant():
    """无关席位被过滤。"""
    catalog = TomlSeatCatalog(DEFINITIONS)
    filtered = catalog.filter_by_input("添加一个按钮")
    ids = {s.seat_id for s in filtered}
    # 没有触发词的席位应该被过滤掉
    assert "seat.compliance" not in ids
    assert "seat.export_import" not in ids
    assert "seat.integration" not in ids
    assert "seat.notification" not in ids
    assert "seat.state_machine" not in ids


def test_filter_by_input_case_insensitive():
    """大小写不敏感匹配。"""
    catalog = TomlSeatCatalog(DEFINITIONS)
    # EXCEL 大写也应命中
    filtered = catalog.filter_by_input("需要EXCEL导出")
    ids = {s.seat_id for s in filtered}
    assert "seat.export_import" in ids


def test_filter_by_input_chinese_keyword():
    """中文关键词匹配。"""
    catalog = TomlSeatCatalog(DEFINITIONS)
    filtered = catalog.filter_by_input("订单状态流转需要审批")
    ids = {s.seat_id for s in filtered}
    assert "seat.state_machine" in ids


def test_filter_by_input_empty_only_always_on():
    """空输入只保留 always_on。"""
    catalog = TomlSeatCatalog(DEFINITIONS)
    filtered = catalog.filter_by_input("")
    assert all(s.always_on for s in filtered)
    assert len(filtered) == 5


def test_filter_by_input_no_false_negatives_on_partial_match():
    """部分匹配也能命中。"""
    catalog = TomlSeatCatalog(DEFINITIONS)
    # "批量导出" 应该命中 export_import（导出 命中）
    filtered = catalog.filter_by_input("支持批量导出操作")
    ids = {s.seat_id for s in filtered}
    assert "seat.export_import" in ids
