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
    assert len(seats) == 7
    assert {seat.seat_id for seat in seats} >= {
        "seat.data_scale",
        "seat.permissions",
        "seat.business_rules",
        "seat.frontend_interaction",
        "seat.tech_approach",
    }
    assert all(seat.title and seat.focus for seat in seats)


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
