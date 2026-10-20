"""联网材料**就地升级**：老档位（query 片段）的材料，在再次搜到同一 URL 时换成整页版。

## 为什么必须修（2026-10-20 批 6c 挖出来的真问题）

联网材料的入库口径换了：`tavily-rest-v1` 存的是 Tavily 按 query 挑的片段，
`tavily-rest-v2-raw` 存的是整页。而 `_keep` 的
「同一 URL 已存在 → 返回既有份」这条规则，会让**老材料永远停在旧档位**——
用户再次搜到同一个网址，拿到的还是片段版：页面主体依旧搜不到。

## 修法为什么不是「后台按原查询重搜一遍」

① 真库里 **0 份材料**（只有一次性验证库里有旧的）→ 那是**死代码**；
② 后台任务没有 owner scope（`require_owner(None)` 抛 `missing_owner_scope`）；
③ 启动时自动花 Tavily 额度，而 `--reload` 每次热重载都会再刷一遍。

**改成：用这次已经搜到的整页内容把它换掉**——内容就在手里，用户本来就在搜，
**不额外花一次额度**，也不需要后台任务。

## 三条纪律（照 `FetchMaterial` 的分支模式）

① 已被编号引用的**不就地改**（那些编号指着它里面的块）→ 另出一份变体，旧的冻结留着；
② 同一档位的**不重写**（重写一遍只是白花 ONNX 推理）；
③ 升级后**向量要重算**（正文换了，旧向量作废）。
"""

import json

from pmstudio.domain.shared.ids import RoundId
from tests.support.builders import PROJECT, SEAT
from tests.support.doubles import RecordingWebSearch
from tests.support.harness import Harness

QUERY = "RBAC 权限模型的主流做法"
OLD_VERSION = "tavily-rest-v1"
NEW_VERSION = "tavily-rest-v2-raw"


def _claims_reply() -> str:
    return json.dumps(
        {
            "basis": "读了空文档与本轮输入",
            "claims": [
                {
                    "text": "权限想按行业通行做法做",
                    "raw_span": "权限想按行业通行做法做",
                    "label": "need",
                    "zone": "body",
                    "section_id": "s5",
                }
            ],
            "seats": [{"seat_id": SEAT, "why": "你说到了权限"}],
            "web_queries": [],
        },
        ensure_ascii=False,
    )


def _harness(*packets) -> Harness:  # type: ignore[no-untyped-def]
    return Harness(
        replies={"claims": _claims_reply()},
        seat_replies={SEAT: json.dumps({"basis": "读了检索结果", "opinions": []})},
        web_search=RecordingWebSearch(returns=packets),
    )


async def _recap_round(harness: Harness) -> RoundId:
    """建轮 → 分析：停在复述卡上。"""

    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="权限想按行业通行做法做")
    await start.analyze(round_id=round_id)
    return round_id


def _packet(*, url: str, text: str, version: str):  # type: ignore[no-untyped-def]
    """一条指定档位的联网结果（形状照 `make_web_packet`，但**正文不截断**）。

    不截断是为了能造出「预算装不下」的那一条（`make_web_packet` 会截到 300 字，
    那样每条都装得下，造不出"入库了但没发编号"的状态）。
    """
    from pmstudio.application.dto import MaterialChunk, ParsedMaterial

    return ParsedMaterial(
        origin="web_search",
        title="权限模型综述",
        source_ref=url,
        media_type="text/html",
        bytes_len=len(text.encode("utf-8")),
        chunks=(
            MaterialChunk(
                chunk_id="tavily#c00",
                ordinal=0,
                text=text,
                heading_path=(),
                anchor="网页摘要",
            ),
        ),
        parser_id="tavily",
        parser_version=version,
        truncated=False,
        warnings=("来自检索：这一轮的检索词",),
    )


async def test_a_material_stored_by_the_old_parser_is_upgraded_in_place():
    """R：老档位**且没被编号引用过**的联网材料，在再次搜到同一 URL 时就地升级。

    「没被引用过」是真实场景：联网结果入库后，**预算装不下的那几条**留在材料表里
    （提示词里明说"材料表里留着，搜得到"），它们没发过编号 → 可以就地换内容。

    升级的判据是「同一 parser、版本不同」——新材料来自**当前**网关，
    它的版本就是当前版本。
    """
    harness = _harness(
        _packet(url="https://small", text="短内容，装得进预算。", version=NEW_VERSION),
        # 这条很长 → 预算装不下 → **只入库、不发编号**
        _packet(url="https://old", text="很长的正文。" * 500, version=OLD_VERSION),
    )
    round_id = await _recap_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,), web_queries=(QUERY,))

    # 前置条件：old 那份入库了，但没发编号
    round_ = harness.rounds.get(round_id)
    assert [ref.chunk_id for ref in round_.materials] == ["m1#c00"], "前一条应该被选中并发号"
    assert harness.materials.get("m2").parsed.parser_version == OLD_VERSION
    assert harness.material_index.material_references(PROJECT).get("m2", ()) == (), (
        "前置条件没造出来：这份材料被引用了"
    )

    # 第二轮：同一个 URL，但网关给的是新档位的整页内容
    harness.web_search.returns = (
        _packet(
            url="https://old",
            text="整页正文：这里有页面主体才有的细节。",
            version=NEW_VERSION,
        ),
    )
    round_id2 = await _recap_round(harness)
    await harness.submit_opening()(round_id=round_id2, seats=(SEAT,), web_queries=(QUERY,))

    upgraded = harness.materials.get("m2")
    assert upgraded is not None, "材料不见了"
    assert upgraded.parsed.parser_version == NEW_VERSION, "老档位没有被升级"
    assert "页面主体才有的细节" in upgraded.parsed.chunks[0].text, "正文没换成整页版"


async def test_a_referenced_old_material_is_not_rewritten_in_place():
    """R：已被编号引用的老材料**不就地改写**——那些编号指着它里面的块。

    改内容等于改指编号 → 另出一份变体，旧的冻结留着。
    """
    harness = _harness(_packet(url="https://a", text="旧的片段内容。", version=OLD_VERSION))
    round_id = await _recap_round(harness)

    await harness.submit_opening()(round_id=round_id, seats=(SEAT,), web_queries=(QUERY,))

    # 第一轮发了编号 → 这份材料已被引用
    round_ = harness.rounds.get(round_id)
    assert [ref.chunk_id for ref in round_.materials] == ["m1#c00"]

    harness.web_search.returns = (
        _packet(
            url="https://a",
            text="整页正文：这里有页面主体才有的细节。",
            version=NEW_VERSION,
        ),
    )
    round_id2 = await _recap_round(harness)
    await harness.submit_opening()(round_id=round_id2, seats=(SEAT,), web_queries=(QUERY,))

    frozen = harness.materials.get("m1")
    assert frozen.parsed.parser_version == OLD_VERSION, "被引用的材料被改写了——编号会改指"
    assert frozen.parsed.chunks[0].text == "旧的片段内容。", "被引用的材料正文被换了"
    # 整页版**另出一份**（变体键：同一 URL + 同一内容 → 同一个键，反复刷不堆变体）
    variant = harness.materials.get("m2")
    assert variant is not None, "没有另出一份整页版"
    assert variant.parsed.parser_version == NEW_VERSION
    assert "页面主体才有的细节" in variant.parsed.chunks[0].text


async def test_a_material_of_the_same_version_is_left_alone():
    """R：同一档位的材料**不重写**——重写一遍只是白花 ONNX 推理。

    真缺向量的情况由启动时的幂等回填兜着。
    """
    harness = _harness(_packet(url="https://a", text="第一遍的内容。", version=NEW_VERSION))
    round_id = await _recap_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,), web_queries=(QUERY,))

    harness.web_search.returns = (
        _packet(url="https://a", text="第二遍的内容。", version=NEW_VERSION),
    )
    round_id2 = await _recap_round(harness)
    await harness.submit_opening()(round_id=round_id2, seats=(SEAT,), web_queries=(QUERY,))

    kept = harness.materials.get("m1")
    assert kept.parsed.chunks[0].text == "第一遍的内容。", "同档位的材料被重写了"
