"""`02-编排与数据.md §4.4` 的信封：智能体之间唯一的通信形状。

**六字段是身份与内容**：`packet_id` / `run_id` / `kind` / `produced_by` / `basis` / `payload`。
设计文档同一条明确删掉了 `confidence` 与 `emitted_refs`，所以这里也**不许**长出它们。

**另外几类是"这一趟的实况"，它们各有真实消费者，缺一个就少一处真相**：

- `packet`：校验后的报文。业务只认它——`payload` 是模型说的，`packet` 是我们认的。
- `overflow` / `discarded`：这一趟**没进卡片**的那些条，留痕要写它们
  （`02` §11.0.35"一条都不许默默丢掉"）。以前它们挂在各 agent 的 Outcome 上、
  信封是另一份载体——**现在只有这里一处**。
**这里刻意没有 `raw` / `usage` / `model`**：它们与网关旁路留样（`model_calls` 表，
`infrastructure/sqlite/model_calls.py`）逐字同源，而那张表连"空回包 / 非 JSON"那两条路
都记，是**更完整**的一份；信封这一份既没人读、又活不过一次请求，留着就是第二真相源
（复现要用原文，那件事由留样表负责，不是由信封负责）。


**身份由协调者分配**：`packet_id` 的格式只有 `make_packet_id` 一处说了算；
"同一席位第几趟第几次重试"这种信息 agent 不知道（它在用例层的重试环里），
所以由用例层通过 `slot` 传下来。信封不猜。
"""

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel

from pmstudio.harness.model.normalize import Discarded, Overflow


def make_packet_id(run_id: str, kind: str, produced_by: str, *, slot: str = "") -> str:
    """`pkt-{run}-{kind}-{produced_by}[-{slot}]`。

    `slot` 是协调者给的槽位（"第几趟第几次"）。同一槽位可复现、不同槽位必不同——
    这条性质由 `tests/harness/test_envelope.py` 钉住。

    为什么格式只在这里写一次：五个 agent 各拼一次字符串，就是五个口径；将来要加前缀
    （比如把项目 id 带上）就会漏改一处，而漏改的那处会安静地产生撞车的编号。
    """

    head = f"pkt-{run_id}-{kind}-{produced_by}"
    return f"{head}-{slot}" if slot else head


@dataclass(frozen=True, slots=True)
class Envelope:
    """§4.4 的信封：这一包是什么、谁产出的、依据是什么、以及它带来了什么。"""

    packet_id: str
    run_id: str
    kind: str
    produced_by: str
    basis: str
    #: 模型吐出来的**原始形状**（补壳之后、归一化之前）。复现与审计靠它。
    payload: Mapping[str, object]
    #: 校验后的报文。业务只认它——`payload` 是模型说的，`packet` 是我们认的。
    packet: BaseModel
    overflow: tuple[Overflow, ...] = ()
    discarded: tuple[Discarded, ...] = ()

    def __post_init__(self) -> None:
        """四个身份字段不许为空。

        这是全局不变式"先有编号再有内容"在信封层的落点：空 `packet_id` / `run_id` 的信封
        等于"一包没有出处的内容"，而整份设计里最不许发生的就是这件事。
        静默放过它，等到复盘时才发现"这包是谁产出的"查不到，就晚了。
        """

        for name in ("packet_id", "run_id", "kind", "produced_by"):
            if not getattr(self, name):
                raise ValueError(f"信封字段 {name} 不能为空：没有编号的包不许流通")


def basis_of(payload: object) -> str:
    """从回包里取 `basis`；没有、或不是字符串，就当没有。

    为什么不在这里编一个：不是每个包都有 basis（意见归 disposition 那包就没有），
    **没有就是没有**——编一个等于把"模型说了什么"变成"我们替它说了什么"。

    入参是 `object` 而不是 `Mapping`：补壳之前的形状可能是数组、字符串、数字
    （`load_shell` 不保证返回对象）。这里必须**降级而不是崩**——崩在信封这一行，
    异常还会绕过所有重试与 HTTP 处理器（`api/app.py` 只接 `DomainError` /
    `ApplicationError`），轮次会永远停在"生成中"且没有失败记录。
    """

    if not isinstance(payload, Mapping):
        return ""
    value = payload.get("basis")
    return value if isinstance(value, str) else ""
