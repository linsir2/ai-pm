"""冻结检索语料的形状守卫：脚本与验收文档引用的是同一份数据。"""

import json
from pathlib import Path

import pytest
from scripts.retrieval_quality_bench import Corpus, _cross_queries, _url_to_index

FIXTURE = Path(__file__).parent / "fixtures" / "retrieval"


def _load(name: str):
    return json.loads((FIXTURE / name).read_text(encoding="utf-8"))


def _corpus(*, materials: int = 15, chunks: int = 40) -> Corpus:
    ids = {index: f"m-{index}" for index in range(materials)}
    chunk_ids = {
        material_id: tuple(f"{material_id}#c{ordinal:02d}" for ordinal in range(chunks))
        for material_id in ids.values()
    }
    return Corpus(
        documents=[
            {"url": f"https://example.test/{index}", "title": f"t{index}", "text": "x"}
            for index in range(materials)
        ],
        materials=ids,
        chunk_ids=chunk_ids,
        chunk_texts={
            material_id: tuple("x" for _ in range(chunks))
            for material_id in ids.values()
        },
    )


def test_frozen_retrieval_fixture_has_the_declared_shape():
    corpus = _load("corpus.json")
    frozen = _load("frozen_queries.json")
    paraphrases = _load("paraphrases.json")["paraphrases"]
    negatives = _load("negative_queries.json")
    cross = _load("cross_queries.json")

    assert len(corpus) == 15
    assert all({"title", "url", "text"} <= set(item) for item in corpus)
    assert len(frozen["items"]) == 45
    assert all(len(item) == 3 for item in frozen["items"])
    assert sum(len(group) for group in paraphrases.values()) == 14
    assert len(negatives) == 8
    for width in ("300", "400"):
        assert len(cross[width]) == 15
        assert all(item["material_index"] in range(15) for item in cross[width])
        assert all(item["chunks"] == [6, 10] for item in cross[width])


def test_frozen_cross_queries_map_to_the_synthetic_corpus():
    rows = _cross_queries(
        _corpus(), chunk_chars=300, path=FIXTURE / "cross_queries.json"
    )

    assert len(rows) == 15
    assert rows[0]["wanted"] == ["m-0#c06", "m-0#c10"]


def test_cross_queries_fail_fast_when_the_width_is_missing(tmp_path: Path):
    path = tmp_path / "cross.json"
    path.write_text('{"300": []}', encoding="utf-8")

    with pytest.raises(ValueError, match="没有 400 宽度"):
        _cross_queries(_corpus(), chunk_chars=400, path=path)


def test_cross_queries_fail_fast_when_a_material_is_missing():
    corpus = _corpus()
    corpus.materials.pop(14)

    with pytest.raises(ValueError, match="material_index=14"):
        _cross_queries(corpus, chunk_chars=300, path=FIXTURE / "cross_queries.json")


def test_cross_queries_fail_fast_when_a_target_chunk_is_missing():
    corpus = _corpus()
    corpus.chunk_ids["m-0"] = ("m-0#c00",)

    with pytest.raises(ValueError, match="目标块不存在"):
        _cross_queries(corpus, chunk_chars=300, path=FIXTURE / "cross_queries.json")


def test_cross_queries_fail_fast_when_the_file_is_missing(tmp_path: Path):
    with pytest.raises(ValueError, match="缺冻结跨块查询文件"):
        _cross_queries(_corpus(), chunk_chars=300, path=tmp_path / "missing.json")


def test_lexical_urls_must_all_map_to_materials():
    corpus = _corpus()
    items = [["词", "问句", "https://example.test/0"]]
    assert _url_to_index(corpus, items)["https://example.test/0"] == 0

    with pytest.raises(ValueError, match="URL 不在语料里"):
        _url_to_index(corpus, [[*items[0][:2], "https://example.test/missing"]])
