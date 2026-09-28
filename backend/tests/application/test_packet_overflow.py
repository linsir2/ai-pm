"""报文容器上限与"超限不判死"。

真跑（`.codex/runs/2026-09-28/报告.md` F2/F4/F11）实测的三次整轮判死：
席位意见 10>8、提案 44>30、未采用意见申报 35>30。这里钉住新口径：
上限装得下一棵完整功能树，超限只截断并报告，不再判死整包。
"""

from annotated_types import MaxLen

from pmstudio.application.agents import schemas


def cap(model, field_name: str) -> int:
    metadata = model.model_fields[field_name].metadata
    return next(item.max_length for item in metadata if isinstance(item, MaxLen))


def test_caps_fit_a_complete_feature_tree():
    # 一棵功能树 12 个字段；一轮至少要装得下 4 棵（48）+ 章节级内容
    assert cap(schemas.IntegrationPacket, "proposals") == 60
    # 7 个席位 × 单席位 12 条：这是"一条都不许默默丢掉"的数学上界
    assert cap(schemas.IntegrationPacket, "dropped_opinions") == 84
    # 提示词允许 5 条判断 + 7 条疑问
    assert cap(schemas.SeatPacket, "opinions") == 12
