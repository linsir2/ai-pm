"""模型回包的两个前置判定：剥围栏 + 是不是 JSON。

这两个是"模型会胡说八道"的最短路径，所以住在 harness 而不是网关文件里——
网关、`parse_packet*`、将来的 `call_structured` 都要用同一个判定，不能各写一遍。
"""

from pmstudio.harness.model.jsonio import is_json, strip_fence


def test_strips_json_code_fence():
    assert strip_fence('```json\n{"a":1}\n```') == '{"a":1}'


def test_strips_bare_fence_without_language():
    assert strip_fence('```\n{"a":1}\n```') == '{"a":1}'


def test_keeps_plain_text_untouched():
    assert strip_fence('  {"a":1}  ') == '{"a":1}'
    assert strip_fence("不是 JSON") == "不是 JSON"


def test_opening_fence_without_newline_yields_empty():
    """真踩过：只回一个 ``` 开头没有换行——剥完必须是空串，不能把原文漏下去。"""

    assert strip_fence("```") == ""
    assert strip_fence("```json") == ""


def test_is_json_accepts_objects_and_arrays():
    assert is_json('{"a":1}') is True
    assert is_json("[1, 2]") is True


def test_is_json_rejects_prose_and_empty():
    assert is_json("这是模型的解释，不是 JSON") is False
    assert is_json("") is False
