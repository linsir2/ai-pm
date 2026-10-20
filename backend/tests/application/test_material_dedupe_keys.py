"""材料**去重键**的三条口径（2026-10-20 阶段性核验查出来的真问题）。

## A4：无链接的检索结果共用一把键 → 后一条被**静默丢弃**

联网结果里没有链接时，`source_ref` 是占位符 `NO_SOURCE_REF`。若去重键只按
`source_ref` 算，**所有**无链接结果共用同一把键 → `_keep` 把后一条当成
"已存在"直接返回 → **那条结果凭空消失**（实测：两条无链接结果只入库一份）。

修法：无链接时改用**内容指纹**做键。

## A3：抓网页的去重键用原地址、来源却写最终地址 → 同一页两份材料

`http://a` 跳到 `https://a`：原来键是 `fetch:http://a`、出处是 `https://a` →
**两份材料、两个 id、同一个出处**。修法：键与出处**同源**，都用最终地址。
"""

from pmstudio.application.dto import NO_SOURCE_REF
from pmstudio.domain.shared.ids import RoundId
from tests.support.builders import PROJECT, SEAT, make_web_packet
from tests.support.doubles import RecordingWebSearch
from tests.support.harness import Harness

QUERY = "RBAC 权限模型的主流做法"


def _claims_reply() -> str:
    import json

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
    import json

    return Harness(
        replies={"claims": _claims_reply()},
        seat_replies={SEAT: json.dumps({"basis": "读了检索结果", "opinions": []})},
        web_search=RecordingWebSearch(returns=packets),
    )


async def _recap_round(harness: Harness) -> RoundId:
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="权限想按行业通行做法做")
    await start.analyze(round_id=round_id)
    return round_id


async def test_two_results_without_a_link_are_both_kept():
    """R：两条**没有链接**的检索结果**都要入库**（不许静默丢一条）。

    静默丢数据是最坏的一种 bug：用户看得见结果，库里却没有。
    """
    harness = _harness(
        make_web_packet(url="", text="第一条：行业通行做法是 RBAC。"),
        make_web_packet(url="", text="第二条：另一段完全不同的内容，讲的是别的做法。"),
    )
    round_id = await _recap_round(harness)

    await harness.submit_opening()(round_id=round_id, seats=(SEAT,), web_queries=(QUERY,))

    stored = [
        packet
        for packet in harness.materials._packets.values()  # noqa: SLF001 —— 替身内部状态
    ]
    assert len(stored) == 2, f"无链接的结果被丢了一条：{[p.parsed.chunks[0].text for p in stored]}"
    assert all(packet.parsed.source_ref == NO_SOURCE_REF for packet in stored)


async def test_fetching_the_same_page_through_a_redirect_keeps_one_material():
    """R：同一个页面（http 跳 https）抓两次 → **一份材料**（键与出处同源）。"""
    from pmstudio.harness.errors import ToolCallFailed
    from pmstudio.infrastructure.tools.fetch import FetchedPage

    harness = Harness(replies={})
    harness.seed_project()
    page = FetchedPage(
        content="<html><body><p>机柜布线规范</p></body></html>".encode(),
        media_type="text/html",
        final_url="https://example.com/spec",
        title="机柜布线规范",
    )
    # 两个**不同的**入口地址，落到**同一个**最终地址（http 跳 https）
    harness.fetcher.returns = (page, page)

    first = await harness.fetch_material()(project_id=PROJECT, url="http://example.com/spec")
    second = await harness.fetch_material()(project_id=PROJECT, url="https://example.com/spec")

    assert first.material_id == second.material_id, (
        f"同一页存了两份材料：{first.material_id} / {second.material_id}"
    )
    assert first.source_ref == second.source_ref
    if isinstance(first, ToolCallFailed):  # pragma: no cover —— 只是把类型用全
        raise AssertionError("不该是失败")
