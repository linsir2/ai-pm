"""模拟使用者表：五个固定 persona 写死在一个 TOML 里（M11-T2）。

席位分文件（10 个独立演进），这里五个**同进退**（固定目录、不单独演进）→ 单文件五表，
顺序即"默认勾选的三个 + 可补勾的两个"（与 `discussion/roles.toml` 同款）。

**机械禁止人口学字段**：`age / gender / ethnicity / region / income / education /
personality / job_title` 出现在任何一张表里 → 整份报错（不是静默忽略）。理由见 B1 设计报告：
按人口学条件化 persona 就是在生产被证伪的信号（arXiv 2607.26348）。
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from pmstudio.domain.shared.errors import ShapeError

#: persona 表。**定义在它所指向的资源旁边**（与 `discussion/roles.py` 的 `ROLES_PATH` 同款）。
PERSONAS_PATH = Path(__file__).with_name("personas.toml")

#: 人口学字段黑名单：出现任何一个键就整份拒收。键是英文（TOML 是机器配置），
#: 与 B1 报告 §2.2 的清单一致——不增不减（增了要同步理由）。
FORBIDDEN_PERSONA_KEYS = frozenset(
    {
        "age",
        "gender",
        "ethnicity",
        "region",
        "income",
        "education",
        "personality",
        "job_title",
    }
)

_REQUIRED_KEYS = ("persona_id", "title", "situation", "goal", "behavior", "probes")


@dataclass(frozen=True, slots=True)
class PersonaDefinition:
    """一个模拟使用者：situation/goal/behavior/probes 全是行为口径，没有人格化称呼。

    字段刻意就六个：真正的走查协议（怎么走、卡住怎么办、产出什么形状）住在提示词里
    （`harness/prompts/sim_review.md`），表里再加"配置"就没人消费了。
    """

    persona_id: str
    title: str
    situation: str
    goal: str
    behavior: str
    probes: str


def load_personas(path: Path = PERSONAS_PATH) -> tuple[PersonaDefinition, ...]:
    """读 `personas.toml` 的 `[[personas]]` 五表；顺序即选择器里的呈现顺序。"""

    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    personas: list[PersonaDefinition] = []
    for table in data.get("personas", ()):
        forbidden = sorted(set(table) & FORBIDDEN_PERSONA_KEYS)
        if forbidden:
            raise ShapeError(
                f"模拟使用者表带人口学字段 {forbidden}——persona 只由使用情境与行为定义",
                persona_id=table.get("persona_id"),
            )
        missing = [
            key
            for key in _REQUIRED_KEYS
            if not str(table.get(key, "")).strip()
        ]
        if missing:
            raise ShapeError(
                f"模拟使用者表缺字段 {missing}（空串不算填）",
                persona_id=table.get("persona_id"),
            )
        personas.append(
            PersonaDefinition(
                persona_id=table["persona_id"],
                title=table["title"],
                situation=table["situation"],
                goal=table["goal"],
                behavior=table["behavior"],
                probes=table["probes"],
            )
        )
    if not personas:
        raise ShapeError("模拟使用者表为空——一个 persona 都没有就跑不了评测")
    return tuple(personas)


def persona_by_id(
    personas: tuple[PersonaDefinition, ...], persona_id: str
) -> PersonaDefinition:
    """按 id 取一个；没有 → `KeyError`（调用方转译成人话错误）。"""

    for persona in personas:
        if persona.persona_id == persona_id:
            return persona
    raise KeyError(persona_id)
