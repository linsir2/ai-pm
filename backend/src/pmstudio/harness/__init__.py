"""harness：运行时控制平面（最内层，不依赖任何业务层）。

**依赖方向是单向的**：`harness` 只许 import `harness` 自己 + 标准库 + 白名单里的第三方
（目前只有 pydantic）；`domain / application / infrastructure / api / bootstrap` 都可以 import
`harness`；反过来一行都不许。这条由 `tests/architecture/test_harness_imports.py` 与
`tests/architecture/guards.py` 的分层表强制，不靠约定。

**它现在装什么**（七件，每件都有真实消费者）：

| 模块 | 消费者 |
|------|--------|
| `errors` | 全项目错误表（api 的 HTTP 映射、四个 agent、网关、工具） |
| `model/contracts` | 四个 agent + 网关 + container |
| `model/failure` | 网关退避判定（`is_transient`；`classify` 的接线在计划 M4） |
| `model/jsonio` | 网关回包出口 |
| `model/normalize` | 四个 agent 的报文解析 + 留痕报告 |
| `model/envelope` | `packet.emitted` 事件（界面按 `packet_id` 回查） |
| `model/structured` | 四个 agent 的唯一结构化调用路径 |

**还没搬进来的**（计划里有编号，不要在这里建空壳）：事件总线与工具声明（M3/M5）、
网关实现与调用留样（M4）、预算与取消（M6）。

**它不装什么**：产品经理的产品文档。黑板是 `domain/round/Round`（`05-DDD架构.md` §12.1
明确否决第二块黑板），记忆是 `domain/memory/`，13 章骨架是 `domain/authoring/`，
工具的**实现**（上传 / 解析 / 抓网页）在 `infrastructure/tools/`——
它们不是控制平面，只是恰好没有业务词汇而已。

**判据**：不是"有没有业务词"，而是"运行时控制平面需不需要它"。
"""
