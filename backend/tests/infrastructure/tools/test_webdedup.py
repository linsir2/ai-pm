"""联网结果的两层去重（批 6a Task 5）：字节级 + 近重复（精确 5-gram Jaccard）。

**为什么是这两层、为什么不用别的**（2026-10-19 真数据实测，50 条 Tavily 结果、
990–1225 对）：

| 方法 | precision | recall | 真规模耗时 | 结论 |
|---|---|---|---|---|
| sha256（字节级） | 1.000 | 0.061 | 54ms | 留：便宜且精确 |
| 精确 5-gram Jaccard | 1.000 | **0.159** | 2255ms | 留：唯一有增量的近重复层 |
| MinHash(128) | 1.000 | 0.093 | **92418ms** | **不引**（datasketch 要拖 scipy ~50MB，慢 57 倍） |
| 语义余弦 ≥0.90 | 1.000 | 0.185 | — | **不做**：保精度时对 n-gram 零增量 |
| 语义余弦 ≥0.80 | 0.772 | 0.422 | — | **不做**：放松阈值就误杀不同内容 |

**阈值 0.2 是数据给的**（precision 1.000 / recall 0.159）。原定的 0.55 在真数据上
recall 只有 0.084——是按构造集拍的，真实转载的相似度谱系比想象宽。
"""

from pmstudio.infrastructure.tools.webdedup import dedupe_by_content


def _item(text: str, *, url: str = "https://a"):
    """一条联网结果（真形状：`ParsedMaterial`，正文在块里）。"""
    from pmstudio.application.dto import MaterialChunk, ParsedMaterial

    return ParsedMaterial(
        origin="web_search",
        title="标题",
        source_ref=url,
        media_type="text/html",
        bytes_len=len(text.encode("utf-8")),
        chunks=(
            MaterialChunk(chunk_id="tavily#c00", ordinal=0, text=text,
                          heading_path=(), anchor="网页正文 · 第 1 段"),
        ),
        parser_id="tavily",
        parser_version="1",
    )


def test_byte_identical_results_are_deduplicated():
    """R：正文**逐字相同**的两条 → 只留一条（字节级，最便宜最精确的一层）。"""
    items = [_item("同一段正文"), _item("同一段正文", url="https://b")]

    assert len(dedupe_by_content(items)) == 1


def test_a_result_with_an_extra_advertisement_is_a_near_duplicate():
    """R：正文只多了句广告 → 算近重复（这是转载最常见的形态）。"""
    body = "个人信息保护法要求处理个人信息应当取得个人同意，并明确处理目的与范围。"
    items = [_item(body), _item(body + "本文转载自网络，版权归原作者所有。", url="https://b")]

    assert len(dedupe_by_content(items)) == 1


def test_two_different_regulations_are_not_duplicates():
    """R：**硬负例**——同主题但内容不同的两份**不许**被合并。

    这是最危险的误杀：不同法规、不同解读，都是有价值的独立来源。
    """
    items = [
        _item("网络安全法规定日志留存时间不少于六个月，用于追溯网络操作。"),
        _item("个人信息保护法要求个人信息处理者告知处理目的并取得同意。"),
    ]

    assert len(dedupe_by_content(items)) == 2


def test_an_empty_text_does_not_match_everything():
    """R：空正文**不能**和谁都像（否则一批空结果会被并成一条，且并错了）。"""
    items = [_item(""), _item(""), _item("")]

    assert len(dedupe_by_content(items)) == 1


def test_the_first_occurrence_is_the_one_kept():
    """R：留的是**第一条**（Tavily 的顺序就是相关序，不能打乱）。"""
    items = [_item("甲乙丙丁戊己庚辛壬癸", url="https://first"),
             _item("甲乙丙丁戊己庚辛壬癸", url="https://second")]

    kept = dedupe_by_content(items)

    assert len(kept) == 1
    assert kept[0].source_ref == "https://first"


def test_the_threshold_is_the_one_the_data_gave():
    """R：阈值钉死 0.2（数据给的点：precision 1.000 / recall 0.159）。

    钉它是为了不让后来的人"随手调到 0.5 看起来更严"——真数据上 0.55 的 recall
    只有 0.084，等于白做。
    """
    from pmstudio.infrastructure.tools.webdedup import NEAR_DUPLICATE_THRESHOLD

    assert NEAR_DUPLICATE_THRESHOLD == 0.2
