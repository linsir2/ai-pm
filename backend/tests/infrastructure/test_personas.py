"""五个固定模拟使用者（M11-T2）：只由**使用情境与行为**定义，机械禁止人口学字段。

为什么禁得这么死（B1 设计报告引 arXiv 2607.26348）：模型系统性高估人口特征对态度的影响、
把细分群体差距夸大 2–4 倍——按人口学条件化 persona 就是在生产被证伪的信号。
所以 TOML 里根本没有 `age / gender / …` 这些键可填，键表由 `FORBIDDEN_PERSONA_KEYS` 钉死。

为什么是单文件五个表（不是五个文件）：它们同进退（固定目录，不单独演进），顺序即
"默认勾选的三个 + 可补勾的两个"；与 `discussion/roles.toml` 同款。`persona.*` 命名空间
与席位 `seat.*`、讨论 `dlg.*` 不重叠——不进席位目录（`test_limits` 的"10 个席位"契约不破）。
"""

import dataclasses

import pytest

from pmstudio.domain.shared.errors import ShapeError
from pmstudio.infrastructure.personas.catalog import (
    FORBIDDEN_PERSONA_KEYS,
    PERSONAS_PATH,
    PersonaDefinition,
    load_personas,
    persona_by_id,
)

EXPECTED_IDS = [
    "persona.first_touch",
    "persona.bulk_operator",
    "persona.interrupted",
    "persona.edge_maker",
    "persona.boundary_walker",
]


def test_persona_fields_are_exactly_the_six():
    """字段表就是这六个（多一个就说明又有人往 persona 里加"配置"）。"""

    assert [item.name for item in dataclasses.fields(PersonaDefinition)] == [
        "persona_id",
        "title",
        "situation",
        "goal",
        "behavior",
        "probes",
    ]


def test_loader_reads_five_personas_in_order():
    personas = load_personas()

    assert [item.persona_id for item in personas] == EXPECTED_IDS
    for item in personas:
        assert item.title.strip(), item.persona_id
        assert item.situation.strip(), item.persona_id
        assert item.goal.strip(), item.persona_id
        assert item.behavior.strip(), item.persona_id
        assert item.probes.strip(), item.persona_id


def test_shipped_table_carries_no_demographic_keys():
    import tomllib

    data = tomllib.loads(PERSONAS_PATH.read_text(encoding="utf-8"))
    for table in data["personas"]:
        assert not (set(table) & FORBIDDEN_PERSONA_KEYS), table.get("persona_id")


def test_demographic_key_is_rejected(tmp_path):
    bad = tmp_path / "personas.toml"
    bad.write_text(
        "\n".join(
            [
                "[[personas]]",
                'persona_id = "persona.x"',
                'title = "某使用者"',
                'situation = "第一次用"',
                'goal = "走完任务"',
                'behavior = "按主流程走"',
                'probes = "入口、反馈"',
                'age = "35"',  # 人口学字段：整份表拒收，不静默忽略
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ShapeError):
        load_personas(bad)


def test_missing_field_is_rejected(tmp_path):
    bad = tmp_path / "personas.toml"
    bad.write_text(
        "\n".join(
            [
                "[[personas]]",
                'persona_id = "persona.x"',
                'title = "某使用者"',
                'situation = "第一次用"',
                'goal = "走完任务"',
                # 缺 behavior / probes：不许用空串混过去
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ShapeError):
        load_personas(bad)


def test_lookup_by_id_and_rejects_unknown():
    personas = load_personas()

    assert persona_by_id(personas, "persona.edge_maker").title == "异常状态制造者"
    with pytest.raises(KeyError):
        persona_by_id(personas, "persona.nobody")
