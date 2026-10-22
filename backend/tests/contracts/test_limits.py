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


def test_退避基线仍是_1_与_3_但等待值带抖动():
    """退避**基线**仍是被钉的行为；等待值不再钉死——它现在带抖动（P2）。

    高并发 P2 之后 `slept` 是 `jittered_backoff(基线)`，落在基线 ±50% 内。
    钉死具体秒数等于把"重试风暴"这个 bug 钉回来：固定退避会让所有同时失败的
    调用方**在同一刻**重试，恰好在上游最需要喘息的时刻再打它一遍。
    `test_gateway_resilience.py::test_退避带抖动` 钉的是新性质（有分散、不固定）。

    退避基线本身仍然是契约：改它会让上界（1+3 秒 + 三次尝试）整体漂移。
    """

    assert litellm_module.BACKOFF_SECONDS == (1.0, 3.0)


def test_默认模型超时按真跑数据定为_150秒():
    """**需求变更（2026-10-16，高并发 P2）**：90 → 150，理由是实测分布。

    167 次成功调用的耗时：p50 18.5s / p95 89.9s / p99 131.3s / **最大 143.0s**。
    90 秒会把最慢那一两条**本来能成功**的调用判死；150 秒覆盖全部已观测到的成功。
    同时 `.env.example` 从 300 收到 150——失败调用的耗时中位恰好是 300.2s、
    最大 900.5s（= 300 × 3 次尝试），也就是说 300 秒只是在多等 107 秒
    去看一个必然超时的结果，并且**占住隔离舱的槽位**。
    病态挂起由熔断负责兜底，不是靠无限拉长单次超时。
    """

    assert litellm_module.DEFAULT_TIMEOUT == 150.0


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

    # 块宽 500 → 400（2026-10-21 批 7）：喂给嵌入模型的是「标题 + 章节链 + 正文」，
    # 而 bge-small-zh-v1.5 的上限是 **512 token**（模型契约，超了**静默**截断）。
    # 实测块宽 500 时块尾部对向量**完全不可见**（cos=1.000000）。联网那条路早就是 400，
    # 现在两条路共用这一个数字。
    assert tools_limits.CHUNK_CHARS == 300
    # **只有一个块宽常量**：同值的两个常量就是第二个真相源（改一处忘另一处）。
    assert not hasattr(tools_limits, "WEB_CHUNK_CHARS"), "块宽常量又长出第二个来"
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
    # 同上：默认超时 P2 起按真跑数据定为 150s（理由见 test_默认模型超时…）
    assert litellm_module.DEFAULT_TIMEOUT == 150.0
    from pmstudio.application import context as context_module

    assert context_module.MATERIAL_BUDGET == 4000
    assert context_module.WEB_BUDGET == 2000
    assert context_module.MEMORY_LINE_LIMIT == 20


def test_inquiry_budget_is_its_own_account_and_pinned():
    """M8：一趟调研的调用账**另算一本**（不进轮次的 66），上限钉在代码与文档两处。"""

    from pmstudio.application import use_cases as uc
    from pmstudio.domain.exploration import item as inquiry_items

    assert uc.MAX_INQUIRY_ATTEMPTS == 2
    assert uc.MAX_EXPLORATION_LIST == 20
    assert inquiry_items.MAX_INQUIRY_HYPOTHESES == 6
    assert inquiry_items.MAX_INQUIRY_BLIND_SPOTS == 3

    text = LIMITS_DOC.read_text(encoding="utf-8")
    assert "探索（调研）另算第三本账" in text, "文档里那节口径不见了？"
    assert "假设 6 条 / 盲区 3 条" in text


def test_every_limit_in_this_file_is_a_positive_integer():
    """防"看起来在配、其实配了 0"——0 会让"超限"判断永远不成立。"""

    values = [value for value, _, _ in DOCUMENTED_CALL_LIMITS]

    assert all(isinstance(value, int) and value > 0 for value in values), values

def test_sim_inquiry_budget_is_its_own_account_and_pinned():
    """M10：模拟调研的调用账**另算第五本**（不进轮次的 66，不占调研/讨论那几本）。"""

    from pmstudio.application import use_cases as uc
    from pmstudio.domain.exploration import item as sim_items

    assert uc.MAX_SIM_INQUIRY_ATTEMPTS == 2
    assert uc.MAX_SIM_INQUIRIES_PER_ROUND == 2
    assert sim_items.MAX_SIM_INQUIRY_SLICES == 3
    assert sim_items.MAX_SIM_INQUIRY_HYPOTHESES == 6
    assert sim_items.MAX_SIM_INQUIRY_UNKNOWNS == 3

    text = LIMITS_DOC.read_text(encoding="utf-8")
    assert "另算第五本账" in text, "文档里那节口径不见了？"
    assert "切片 3 /" in text and "假设 6 / 盲区 3" in text


# ── 限流上限（高并发 P3）─────────────────────────────────────────────────────


def test_login_rate_limits_are_pinned_to_the_configured_defaults():
    """登录限流的默认值**钉在这里**，理由写在 `settings.py` 的常量处。

    这三条不是一个数：`per-IP` 与 `per-username` 挡的是**两种不同形状的攻击**
    （单 IP 喷多账号 / 多 IP 撞单账号），窗口是另一个维度。合成一个数就等于
    放弃了其中一种攻击。
    """

    from pmstudio.bootstrap import settings as settings_module

    assert settings_module.LOGIN_IP_MAX_ATTEMPTS == 20
    assert settings_module.LOGIN_USERNAME_MAX_ATTEMPTS == 10
    assert settings_module.LOGIN_ATTEMPT_WINDOW_SECONDS == 60
    assert settings_module.LOGIN_MAX_FAILS == 5
    assert settings_module.LOGIN_LOCK_MINUTES == 15


def test_每分钟二十次_意味着攻击者每分钟最多试二十次():
    """把"20/分"翻译成人话钉住——数字变了要有人知道变了。"""

    from pmstudio.bootstrap import settings as settings_module

    per_hour = settings_module.LOGIN_IP_MAX_ATTEMPTS * 60

    assert per_hour == 1200, f"单 IP 每小时上限变成了 {per_hour} 次"


def test_尝试闸_的上限_不许小于失败锁_否则两层互相盖住():
    """**两层闸的次序是有讲究的**：失败锁先判（回 401），再判尝试闸（回 429）。

    如果 per-IP 尝试上限比 `max_fails` 还低，失败锁就永远轮不到触发——
    撞库者拿到的是 429 而不是不可枚举的 401，等于把"我被限了"直接说出去，
    而不可枚举性是 `test_login_failure_is_uniform_for_ghost_and_wrong_password`
    在守的东西。
    """

    from pmstudio.bootstrap import settings as settings_module

    assert settings_module.LOGIN_IP_MAX_ATTEMPTS > settings_module.LOGIN_MAX_FAILS
    assert (
        settings_module.LOGIN_USERNAME_MAX_ATTEMPTS > settings_module.LOGIN_MAX_FAILS
    ), "per-username 尝试闸比失败锁还紧，会盖掉不可枚举的 401"


def test_redis_连接池上界_不许小于并发目标():
    """**默认 100 会炸**：redis-py 的 asyncio 池默认就是 100，实测 200 并发直接
    `MaxConnectionsError`。并发目标是 200，所以这个数必须显式大于它。"""

    from pmstudio.bootstrap import settings as settings_module

    assert settings_module.REDIS_MAX_CONNECTIONS >= 200


def test_redis_超时必须小到_能当降级路径用():
    """不设 `socket_timeout` 时实测会卡 5 秒——降级分支根本走不到。
    登录一次 argon2 约 90-200ms，Redis 这段不该比它还长一个量级。"""

    from pmstudio.bootstrap import settings as settings_module

    assert 0 < settings_module.REDIS_TIMEOUT <= 1.0


def test_限流上限不许进_harness():
    """限流是**控制平面**，归 bootstrap/settings；`harness` 只该知道"怎么安全地
    调外部依赖"，不该知道产品的防爆阈值。"""

    from tests.architecture.guards import code_identifiers

    harness = REPO / "backend" / "src" / "pmstudio" / "harness"
    names = (
        "LOGIN_IP_MAX_ATTEMPTS",
        "LOGIN_USERNAME_MAX_ATTEMPTS",
        "LOGIN_ATTEMPT_WINDOW_SECONDS",
        "LOGIN_MAX_FAILS",
        "LOGIN_LOCK_MINUTES",
        "REDIS_MAX_CONNECTIONS",
        "REDIS_TIMEOUT",
    )
    offenders: dict[str, list[str]] = {}
    for path in sorted(harness.rglob("*.py")):
        found = code_identifiers(path.read_text(encoding="utf-8"))
        hits = sorted(name for name in names if name in found)
        if hits:
            offenders[path.relative_to(harness).as_posix()] = hits
    assert offenders == {}, f"这些限流上限出现在 harness 里：{offenders}"


def test_the_upper_limit_registry_matches_the_code():
    """R：`02` 的「**上限登记表**」与代码**一致**（防表漂移）。

    为什么（2026-10-22，批 14，M6 第一步）：那张表是 M6 后续的**工作清单**
    （"与谁对过账"那一列）——表里的数一旦和代码对不上，它就从"账"变成"传说"。

    I：读 `02-编排与数据.md` 的登记表；
    P：逐行把「位置」里的模块与常量取出来，比对**值**；
    R：全对得上（表里多一个代码里没有的常量 → 也红）。
    """
    import importlib
    import re
    from pathlib import Path

    doc = Path(__file__).resolve().parents[3] / ".codex/docs/设计/02-编排与数据.md"
    text = doc.read_text(encoding="utf-8")
    start = text.index("### 11.0.90 上限登记表")
    block = text[start : text.index("**防漂移**", start)]
    rows = [
        line
        for line in block.splitlines()
        if line.startswith("| `") and "`" in line and "位置" not in line
    ]
    assert rows, "登记表没解析出行 —— 表的格式变了？"

    pattern = re.compile(
        r"^\| `(?P<name>[A-Z0-9_/]+)` \| (?P<value>[^|]+) \| `(?P<where>[^`]+)` \|"
    )
    checked = 0
    for row in rows:
        match = pattern.match(row)
        assert match, f"行没解析出来：{row}"
        where = match.group("where")
        module_name, name = where.rsplit(".", 1)
        module = importlib.import_module(f"pmstudio.{module_name}")
        if not hasattr(module, name):
            # 表里有、代码里没有 → **表漂了**（那个上限被删了或改名了）
            raise AssertionError(f"表里的 {where} 在代码里不存在 —— 表漂了")
        expected = match.group("value").strip()
        actual = getattr(module, name)
        assert str(actual) == expected, (
            f"{where} 在代码里是 {actual}，表里写的是 {expected} —— 表漂了"
        )
        checked += 1
    assert checked >= 20, f"只核到 {checked} 个 —— 表的格式或范围变了？"
