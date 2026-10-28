"""冻结检索语料的形状守卫：脚本与验收文档引用的是同一份数据。"""

import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "retrieval"


def _load(name: str):
    return json.loads((FIXTURE / name).read_text(encoding="utf-8"))


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
