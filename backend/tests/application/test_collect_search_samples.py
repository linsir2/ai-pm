"""采集脚本的解析：旧分解行与新 caller/stage 行都要能读（P0 Task 0.4）。"""

from scripts.collect_search_samples import parse_line


def test_parses_the_hybrid_decomposition_line():
    item = parse_line(
        "INFO material_search project=p1 query='导出权限' "
        "fts_candidates=20 vec_candidates=12 fts_hits=3 hybrid_hits=5"
    )

    assert item == {
        "project": "p1",
        "query": "导出权限",
        "fts_candidates": 20,
        "vec_candidates": 12,
        "fts_hits": 3,
        "hybrid_hits": 5,
    }


def test_parses_the_lookup_line_with_caller_and_stage():
    item = parse_line(
        "INFO material_search caller=search stage=lookup project=p1 "
        "query='导出权限' limit=50 hits=4"
    )

    assert item is not None
    assert (item["caller"], item["stage"]) == ("search", "lookup")
    assert (item["project"], item["query"], item["limit"], item["hits"]) == (
        "p1",
        "导出权限",
        50,
        4,
    )


def test_ignores_a_line_without_any_number():
    assert parse_line("INFO material_search project=p1 query='导出'") is None
