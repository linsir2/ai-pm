"""一轮的调用上限：**文档那张表就是契约**（2026-10-04 · M6a）。

## 这条文件存在的理由

`02-编排与数据.md §11.0` 有一张「一轮的调用上限」表（12 行），以及一句手写的算术：
"最坏 ≈ **46 次**"。那张表与那句算术**从来没有被任何东西验证过**，于是它漂了：

- **46 漏算了记忆整理者**（`IntegrateRound` 里每轮调 `_curate`，最多 2 次）；
- 更根本的是，**它用的席位数 7 没有任何出处**——席位目录里实际有 10 个，
  而代码里**没有席位数上限**（席位来自目录，用户在开场卡上勾）。

所以本文件做两件事：

1. **表里的每一个上限都钉在代码常量上**——改代码不改文档（或反之）这里会红；
2. **"最坏多少次"由代码算出来，再与文档写的那个数对照**——这是唯一能自动抓
   文档算术漂移的办法（把数值抄进测试只能防代码漂，防不了文档漂）。

## 为什么不是 M6a 原计划说的"把 22 个上限搬进 harness"

因为大部分上限是**产品决定**（问几轮席位、分几批问卷），不是控制平面该知道的；
而且现有守卫的 `PRODUCT_VOCABULARY` 是**精确匹配**，`MAX_SEAT_ROUNDS` ≠ `seat_id`
——搬进去它抓不到。本文件里 `test_product_limits_never_enter_harness` 就是堵这个盲区。
"""

import re
from pathlib import Path

from pmstudio.application import use_cases
from pmstudio.domain.round import questionnaire as questionnaire_module
from pmstudio.domain.round import step as step_module
from pmstudio.harness.model.providers import litellm as litellm_module

REPO = Path(__file__).resolve().parents[3]
LIMITS_DOC = REPO / ".codex" / "docs" / "设计" / "02-编排与数据.md"
SEATS_DIR = REPO / "backend" / "src" / "pmstudio" / "infrastructure" / "seats" / "definitions"

#: `02 §11.0`「一轮的调用上限」表，一行一个 → 代码里真的是那个数。
#: 顺序与文档一致，方便对着看。
DOCUMENTED_CALL_LIMITS = [
    # (代码里的常量, 值, 文档那一行怎么说)
    (step_module.MAX_SEAT_ROUNDS, 3, "席位轮 3 轮"),
    (questionnaire_module.MAX_QUESTIONNAIRES, 2, "问卷 2 批"),
    (questionnaire_module.MAX_OPEN_ITEMS_PER_BATCH, 3, "归因补交代 1 次（另算一本账）"),
    (use_cases.MAX_CLAIMS_ATTEMPTS, 2, "主张 claims 重装 1 次（共 2 次）"),
    (use_cases.MAX_SEAT_ATTEMPTS, 2, "席位轮 · 单席位报文不合格：重装 1 次（共 2 次）"),
    (use_cases.MAX_INTEGRATION_ATTEMPTS, 2, "整合 重装 1 次（共 2 次）"),
    (use_cases.MAX_CURATOR_ATTEMPTS, 2, "记忆整理 重装 1 次（共 2 次）"),
    (litellm_module.MAX_ATTEMPTS, 3, "单次网关调用 3 次尝试"),
]


def test_documented_call_limits_match_the_code():
    """文档那张表里的每个上限，代码里必须真的是那个数。"""

    wrong = [(value, why) for value, expected, why in DOCUMENTED_CALL_LIMITS if value != expected]

    assert wrong == [], f"这些上限与文档不一致（左边是代码的值）：{wrong}"


def test_the_seat_catalog_really_has_ten_seats():
    """最坏次数的算式里那个席位数，**必须是目录的真实条目数**。

    为什么单独钉：文档原来用的是 7，而目录里有 10 个、代码里没有席位数上限。
    那个 7 成了"理论极值席位"的传闻依据——而它既没有出处，也拦不住。
    席位目录加减条目时，这里会红，提醒同时更新最坏次数。
    """

    seats = sorted(path.stem for path in SEATS_DIR.glob("*.toml"))

    assert len(seats) == 10, f"席位目录变成了 {len(seats)} 个：{seats}（最坏次数要重算）"


def _worst_case_calls(seats: int) -> int:
    """一轮的最坏模型调用次数——**算出来的**，不是抄的。

    口径（与 `02 §11.0` 那张表逐行对应）：
      主张 claims 2 + 席位轮 3 × 每席位 2 次 × 席位数 + 整合 2 + 记忆整理 2

    **归因补交代不在里面**：文档说它"另算一本账"（`MAX_ACCOUNTING_REPAIRS`），
    那是有意的口径，不是漏算。
    """

    return (
        use_cases.MAX_CLAIMS_ATTEMPTS
        + step_module.MAX_SEAT_ROUNDS * seats * use_cases.MAX_SEAT_ATTEMPTS
        + use_cases.MAX_INTEGRATION_ATTEMPTS
        + use_cases.MAX_CURATOR_ATTEMPTS
    )


def test_the_documented_worst_case_equals_the_computed_one():
    """**文档写的"最坏 N 次"必须等于从代码算出来的那个数。**

    这是本文件最值钱的一条：文档那句算术是手写的，从来没人验证过，
    于是它漏算了记忆整理者，还用了一个没有出处的席位数（7，而目录里有 10 个）。
    """

    computed = _worst_case_calls(10)
    text = LIMITS_DOC.read_text(encoding="utf-8")

    stated = re.search(r"最坏\s*[≈约]*\s*\*\*(\d+)\s*次\*\*", text)
    assert stated is not None, "文档里找不到「最坏 ≈ N 次」那句话——它被删了或改了写法？"

    assert int(stated.group(1)) == computed, (
        f"文档说最坏 {stated.group(1)} 次，按代码算出来是 {computed} 次"
        f"（claims {use_cases.MAX_CLAIMS_ATTEMPTS} + 席位 "
        f"{step_module.MAX_SEAT_ROUNDS}×10×{use_cases.MAX_SEAT_ATTEMPTS} + 整合 "
        f"{use_cases.MAX_INTEGRATION_ATTEMPTS} + 整理 {use_cases.MAX_CURATOR_ATTEMPTS}）"
    )


def test_accounting_repairs_are_counted_in_their_own_book():
    """归因补交代是"另算一本账"——这是有意的口径，所以要钉住它没被并进主账。"""

    assert use_cases.MAX_ACCOUNTING_REPAIRS == 2

    text = LIMITS_DOC.read_text(encoding="utf-8")
    assert "另算一本账" in text, "文档里那句口径不见了；并进主账的话 66 要改成 68"


def test_the_backoff_schedule_is_exactly_one_and_three_seconds():
    """退避时刻是**要被断言的行为**（`test_llm.py` 断言 `slept == [1.0, 3.0]`），
    但那个数字本身**从来没有被任何测试钉过**——改掉它只会让那些断言跟着一起变绿。"""

    assert litellm_module.BACKOFF_SECONDS == (1.0, 3.0)
    assert litellm_module.DEFAULT_TIMEOUT == 90.0


# ── 归属：控制平面的上限在 harness，产品上限不许进去 ────────────────────────────


def test_control_plane_limits_live_in_harness():
    """**每次调用的重试与超时属于运行时控制平面**，所以归 harness。"""

    assert litellm_module.MAX_ATTEMPTS == 3
    assert "harness" in Path(litellm_module.__file__).parts


def test_product_limits_never_enter_harness():
    """**产品上限不许进 harness**——而现有守卫**抓不到**这件事。

    `test_harness_imports.py` 的 `PRODUCT_VOCABULARY` 是**精确匹配**（用集合交），
    `MAX_SEAT_ROUNDS` ≠ `seat_id`、`MAX_CLAIMS_ATTEMPTS` ≠ `ClaimsPacket`，
    所以"把产品上限伪装成复合常量名搬进 harness"它一条都拦不住。
    这条专门堵那个盲区：M6a 原计划就是要把这些常量搬进 harness。
    """

    from tests.architecture.guards import code_identifiers

    harness = REPO / "backend" / "src" / "pmstudio" / "harness"

    offenders: dict[str, list[str]] = {}
    for path in sorted(harness.rglob("*.py")):
        # **只扫标识符，不扫源码文本**——注释里提到某个上限是在解释它，
        # 那不是依赖。（这条守卫第一版就是裸子串扫，于是被我自己在
        # `providers/litellm.py` 的注释里写的 `MAX_SEAT_ROUNDS` 判了一次违规。）
        found = code_identifiers(path.read_text(encoding="utf-8"))
        hits = sorted(
            name
            for name in (
                "MAX_SEAT_ROUNDS",
                "MAX_QUESTIONNAIRES",
                "MAX_OPEN_ITEMS_PER_BATCH",
                "MAX_QUESTIONS_PER_BATCH",
                "MAX_WEB_QUERIES",
                "MAX_CLAIMS_ATTEMPTS",
                "MAX_SEAT_ATTEMPTS",
                "MAX_INTEGRATION_ATTEMPTS",
                "MAX_CURATOR_ATTEMPTS",
                "MAX_ACCOUNTING_REPAIRS",
                "MATERIAL_BUDGET",
                "WEB_BUDGET",
                "MEMORY_LINE_LIMIT",
                "SUMMARY_LIMIT",
            )
            if name in found
        )
        if hits:
            offenders[path.relative_to(harness).as_posix()] = hits

    assert offenders == {}, f"这些产品上限出现在 harness 里：{offenders}"


def test_domain_and_application_limits_stay_where_they_are():
    """反过来也钉一下：产品上限待在**决定它的那层**。

    `MAX_SEAT_ROUNDS` 在 `domain/round/step.py`——因为"轮"是领域概念，
    运行时控制平面不该知道我们问几轮席位。
    """

    assert Path(step_module.__file__).parent.name == "round"
    assert Path(questionnaire_module.__file__).parent.name == "round"
    assert "application" in Path(use_cases.__file__).parts


# ── 其余上限：不是模型调用，但同样是"文档写了、代码散着"的东西 ────────────────


def test_the_remaining_documented_limits():
    """其余**不在那张调用表里**的上限，但同样是"文档写了、代码散着"的。

    解析阶段那两个（`tools/limits.py`）的注释已经写明"必须是同一个数"——
    文本 / PDF / HTML 与 Word / Excel 两边共用，所以它们是**唯一一份**，钉住它。
    """

    from pmstudio.application import deliverable as deliverable_module
    from pmstudio.domain.memory import memory as memory_module
    from pmstudio.infrastructure.tools import fetch as fetch_module
    from pmstudio.infrastructure.tools import limits as tools_limits

    assert tools_limits.CHUNK_CHARS == 500
    assert tools_limits.MAX_MATERIAL_CHARS == 200_000
    assert fetch_module.MAX_FETCH_BYTES == 5 * 1024 * 1024
    assert fetch_module.MAX_REDIRECTS == 3
    assert deliverable_module.SUMMARY_LIMIT == 60
    assert memory_module.MAX_ENTRY_LINES == 2


def test_asks_at_most_five_questions_per_batch():
    """`MAX_QUESTIONS_PER_BATCH` 是问卷批的**每批题数**，上面那张表只写了"2 批"。

    两个数一起才有意义：批数 × 每批题数 = 一轮最多问几道题。
    """

    assert questionnaire_module.MAX_QUESTIONS_PER_BATCH == 5
    assert questionnaire_module.MAX_QUESTIONNAIRES == 2


def test_web_queries_per_round_are_capped_in_the_domain():
    """联网检索词的每轮上限在 `domain`（它是轮次的不变量，不是提示词预算）。"""

    from pmstudio.domain.round import web_query as web_query_module

    assert web_query_module.MAX_WEB_QUERIES == 3


def test_prompt_character_budgets():
    """提示词里的字符预算（`02` 另有一处口径）。它们决定上下文被怎么裁。"""

    assert use_cases.MAX_MATERIAL_BYTES == 20 * 1024 * 1024
    assert use_cases.MAX_MATERIALS_PER_PROJECT == 50
    assert litellm_module.DEFAULT_TIMEOUT == 90.0
    from pmstudio.application import context as context_module

    assert context_module.MATERIAL_BUDGET == 4000
    assert context_module.WEB_BUDGET == 2000
    assert context_module.MEMORY_LINE_LIMIT == 20


def test_every_limit_in_this_file_is_a_positive_integer():
    """防"看起来在配、其实配了 0"——0 会让"超限"判断永远不成立。"""

    values = [value for value, _, _ in DOCUMENTED_CALL_LIMITS]

    assert all(isinstance(value, int) and value > 0 for value in values), values