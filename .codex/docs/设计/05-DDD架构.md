# 05 · DDD 架构（怎么实现）

> 技术选型已定：**SQLite + LiteLLM + FastAPI + React/Vite**。
> 这份回答：分几个限界上下文、聚合与不变量落在哪、端口怎么切、LangGraph 放哪。

---

## 1. 三个限界上下文

系统的语言可以清楚地分成三块，各说各的话：

| 上下文 | 它管什么 | 核心语言 |
|--------|---------|---------|
| **Authoring 文档** | 13 章骨架、block 树、版本与快照 | section、block、feature、version、snapshot |
| **Provenance 追溯** | 出处账本与可追溯链 | evidence、kind、derived_from、supersedes |
| **Round 回合** | 一轮怎么推进：主张、席位、意见、问卷、草案 | claim、seat、opinion、questionnaire、draft |

跨上下文**只传 id，不传对象**：Round 里的内容候选引用 `eid` 与 `block id`，但不持有它们。

席位与工具的**定义**（注册表）在 v1 只是静态配置，做成一个只读的 `SeatCatalog` 端口，
不建模型（等真的需要用户自定义席位时再升级成上下文）。

## 2. 统一语言（写代码时就照这个命名）

| 词 | 含义 | 不叫 |
|----|------|------|
| Claim | 从用户输入切出来的原子主张，带标签与 target | 需求、条目 |
| Opinion | 席位交回的专业意见，只有疑问或判断两种形态 | 发现、结论 |
| Evidence | 出处账本里的一条，带六类 kind | 引用、来源记录 |
| Questionnaire | 一组题目，不是"卡片" | 表单、问卷卡 |
| Draft | 提案数组，按来源分三组 | 填充卡 |
| Round | 从用户一次输入到写入的一整轮 | 会话、任务 |
| Step / WaitingOn | 轮走到哪、在等谁 | 相位、状态 |

## 3. 聚合与不变量

四个聚合，每个不变量只在一个地方实现。

### 3.1 Authoring 上下文

**Project**（聚合根，最外层容器）
字段：`project_id`、`name`、`document_id`、`preference_area`（偏好区）。
不变量：项目名非空；偏好区只存偏好，不混正文。

**Document**（聚合根，含版本）
字段：`document_id`、`meta`、`blocks[]`、`versions[]`。

| 不变量 | 内容 |
|--------|------|
| D1 | 十三个 section 固定，不可增删重排 |
| D2 | block id 永久稳定；删除留墓碑 |
| D3 | 只有 s5 内允许新增 feature 子树（用户点名的新功能除外） |
| D4 | 同一父节点下字段名唯一 |
| D5 | 被手改过的 block 不能被静默覆盖（v1 无手改，规则先立） |
| D6 | 一次写入 = 一个版本；版本只增不改；回退产生新版本 |

方法：`apply(proposals) -> Version`、`view()`（只读投影）。
**版本与文档同属一个聚合**，因为"写入并产生版本"必须是同一个事务。

### 3.2 Provenance 上下文

**EvidenceLedger**（聚合根）
字段：`evidence[]`。

| 不变量 | 内容 |
|--------|------|
| E1 | 编号全局唯一、只增、永不复用 |
| E2 | 修订只能新增条目并 `supersedes` 旧条目，不能改写历史 |
| E3 | `agent_derived` 必须带 `derived_from`，且指向已存在的编号 |
| E4 | 账本内部不校验"链是否落到用户输入"——那是跨上下文的事，交给领域服务 |

方法：`record(kind, text, source) -> eid`、`chain(eid) -> Evidence[]`。

### 3.3 Round 上下文

**Round**（聚合根）
字段：`round_id`、`project_id`、`user_input`、`claims[]`、`seats[]`、`opinions[]`、
`questionnaire?`、`draft?`、`step`、`waiting_on`、`seat_round_count`、`answer_sets[]`。

| 不变量 | 内容 |
|--------|------|
| R1 | 一轮绑定一次用户输入 |
| R2 | step 只能按序推进，不回退；`waiting_on` 非空时不能推进 |
| R3 | 席位轮最多 3 次 |
| R4 | 一批问卷最多 5 题，超出降级成清单项 |
| R5 | 草案只能由整合结果产生，且每条提案必须带 refs |
| R6 | 答案必须带 question_id，且只能回答本轮的问题 |

方法：`start(input)`、`attest(packet)`（席位交回）、`integrate(result)`、
`answer(formId, answers)`、`produceDraft(draft)`、`advance(step)`。

## 4. 领域服务（跨聚合的逻辑）

| 服务 | 干什么 | 为什么不能塞进聚合 |
|------|--------|------------------|
| `TraceabilityChecker` | 给定 refs，回溯账本，判断链是否落到 `user_input` / `card_answer` | 要同时读 Round 与 Ledger |
| `ProposalCommitter` | 把确认的提案应用到 Document 并生成版本，**唯一写入路径** | 要同时读 Round 的草案与 Document |
| `ScopeGuard` | 校验提案的 target 落在本轮写范围内 | 写范围来自 Round 的主张，落点在 Document |
| `EvidenceAssigner` | 给席位交回的意见分配全局编号 | 席位并行，编号必须由汇合点统一分配 |

## 5. 端口（Ports）

领域只声明接口，实现在 infrastructure。

| 端口 | 关键方法 | 实现 |
|------|---------|------|
| `DocumentRepository` | `get(projectId)` / `save(document)` | SQLite |
| `RoundRepository` | `get(roundId)` / `save(round)` | SQLite |
| `EvidenceRepository` | `get(projectId)` / `append(...)` / `chain(eid)` | SQLite |
| `UnitOfWork` | `commit()` / `rollback()`，一个用例一个事务 | SQLite |
| `ModelGateway` | `complete(messages, response_format)` | **LiteLLM** |
| `SeatCatalog` | `all()` / `match(input)` | 静态定义（JSON/YAML） |
| `EventPublisher` | `publish(event)` | 进程内 → SSE |
| `Clock` / `IdGenerator` | `now()` / `newId(prefix)` | 系统实现 |

## 6. 应用服务（一个用例一个入口）

| 用例 | 做什么 | 事务 |
|------|--------|------|
| `StartRound` | 建轮 → Analyzer 切分定性 → 组装开场卡 | 一小事务 |
| `SubmitOpening` | 收用户对复述与席位的修改 → 落账本 → 触发席位轮 | 一小事务 |
| `RunSeatRound` | 并行调各席位 → 收信息包 → 交给整合者 | 每席位一个事务，整合一个 |
| `IntegrateRound` | 整合意见 → 出问卷 或 直接出草案 | 一个事务 |
| `SubmitAnswers` | 答案落账本 → 重新组装上下文 → 触发下一轮席位 | 一个事务 |
| `ConfirmDraft` | **唯一写入路径**：应用提案 → 新版本 → 发事件 | **一个事务** |
| `GetDocument` / `ListVersions` / `GetRound` | 查询（只读投影，给界面） | 只读 |

## 7. LangGraph 放哪（这条最容易搞错）

**LangGraph 是编排的"实现技术"，属于 infrastructure，不属于领域。**

- 领域里的 Round 聚合才是**真相源**：走哪一步、等什么、最多几次席位轮，都是 R1–R6 不变量。
- 图的 state **只放引用**（`round_id`、当前 step），**不放业务状态**——否则图与聚合就成了
  两个真相源（既有设计里用黑板做共享状态，正是为了解决这个，我们直接避免它）。
- 图里每个节点做的事：调应用服务 → 读聚合 → 推进 step → 需要人决策时 `interrupt()`。
- 人机中断点只有三处：开场卡、问卷、草案卡。答案由 API 收下后 `resume`，图接着跑。

```
图（infrastructure）                     领域（domain）
  node.start_round      →  StartRound        →  Round.start() / Evidence.record()
  node.seat_round       →  RunSeatRound      →  Round.attest()      [席位并行]
  node.integrate        →  IntegrateRound    →  Round.integrate()
  node.ask              →  （interrupt）      →  Round.waiting_on = questionnaire
  node.answers          →  SubmitAnswers     →  Round.answer()
  node.draft_confirm    →  （interrupt）      →  Round.waiting_on = draft
  node.commit           →  ConfirmDraft      →  ProposalCommitter → Document.apply()
```

## 8. 目录与依赖方向

```
backend/src/pmstudio/
├── domain/
│   ├── authoring/    document.py blocks.py version.py invariants.py
│   ├── provenance/   ledger.py evidence.py
│   ├── round/        round.py claim.py opinion.py questionnaire.py draft.py
│   ├── services/     traceability.py committer.py scope_guard.py assigner.py
│   └── shared/       ids.py errors.py target.py label.py
├── application/      use_cases.py ports.py dto.py
├── infrastructure/
│   ├── sqlite/       schema.sql repositories.py unit_of_work.py
│   ├── llm/          litellm_gateway.py prompts/ parsers.py
│   ├── seats/        catalog.py definitions/*.yaml
│   └── graph/        graph.py nodes/   ← LangGraph
├── api/              app.py routes.py sse.py dtos.py
└── bootstrap/        container.py settings.py

frontend/             React + Vite：文档区 / 闭环区 / 弹出层
tests/                domain/ application/ infrastructure/
```

**依赖方向**：`api → application → domain`；
`infrastructure` 实现 `application/ports.py` 里的协议，被 bootstrap 注入；
**`domain` 不 import 任何外部包**（不 import fastapi / langgraph / litellm / sqlite3）。
这条用测试守：扫 `domain/` 的 import，出现外部包即失败。

## 9. 事务与一致性

- **一轮推进**：每个用例一个小事务，聚合整体读写（SQLite 行级 + 单写者）。
- **确认写入**：`ConfirmDraft` 一个事务里完成"应用提案 + 生成版本 + 记录出处关联"。
- **跨聚合的最终一致**：事件驱动（进程内），v1 不做补偿逻辑；失败即整轮失败可重试。
- **单实例**：v1 单用户单进程，SQLite 用文件锁防双开。

## 10. 测试策略

| 层 | 测什么 | 依赖 |
|----|--------|------|
| domain | 不变量 D1–D6 / E1–E3 / R1–R6；每个聚合方法 | 无（纯对象） |
| domain services | 可追溯回溯、写范围校验、编号分配 | 假仓储 |
| application | 每个用例的编排与事务边界 | 假端口（含假模型） |
| infrastructure | SQLite 仓储往返；LiteLLM 网关的解析与失败 | 内存库；假 HTTP |
| api | 路由与 SSE 事件形状 | 假应用服务 |
| e2e | 一次完整迭代（输入 → 复述 → 席位 → 问卷 → 草案 → 确认 → 多一版） | 假模型 |

假模型是硬要求：**没有 API key 也要能跑通全流程与全部测试。**

## 11. 与界面的接口

界面只认三样东西：**文档投影**（`GetDocument`）、**轮状态投影**（`GetRound`，含本轮讨论流）、
**事件流**（SSE）。界面不做任何业务判断，"要不要出问卷""能不能写"全由领域决定。

---

## 12. 黑板与事件：要不要用

### 12.1 黑板：**不引入**（Round 聚合就是有类型的黑板）

经典黑板模式的收益是"多个知识源在共享工作区里增量协作"——谁有想法谁往上写，
控制器调度到问题解决。**我们主动放弃了它最典型的收益**：席位是并行且互不可见的
（互不可见是为了避免趋同）。席位之间不需要看见彼此的中间产物，
它们的协作是**阶段式**的（并行跑 → 汇合），不是增量式的。

那"一轮跑到一半关掉页面、第二天接着跑"谁满足？**Round 聚合。**

| 黑板模式的黑板 | Round 聚合 |
|----------------|-----------|
| 无类型的区块 | 有类型：claims / opinions / questionnaire / draft |
| 靠约定管写法 | 靠不变量管（R1–R6） |
| 谁都能写 | 只有应用服务能改，且必须过不变量 |

再引入一块额外黑板，就是把同一份状态放两个地方——又变回"两个真相源"。**不要。**

### 12.2 事件总线：**要，但只做通知与投影**

事件的三种可能用途要分开看：

| 用途 | 要不要 | 为什么 |
|------|--------|--------|
| 推给界面（SSE） | **要** | 界面需要增量更新，否则只能轮询 |
| 触发后续处理（回调式编排） | **不要** | 控制流被打散，R1–R6 无处安放，出问题难查 |
| 留痕与回放 | v1 不要 | 没有回放需求，先不加 |

**一条判据**：把全部订阅者摘掉，流程照样能跑完——这才叫事件只做通知。
若摘掉订阅者流程就跑不动，说明流程逻辑被藏进事件里了，必须挪回用例。

既有那套设计里，事件还要驱动"记忆失效链"（引用的内容改了 → 记忆失效）。
我们 v1 没有记忆层，**这条链没有消费者**，这是不引入事件回调的又一个理由。

## 13. 完整数据流（谁调用谁、哪步开事务、发什么事件）

规则：**模型调用一律在事务外**（网络调用不能包进长事务），事务只包住状态变更。

| 步 | 入口 | 做什么 | 事务 | 事件 |
|----|------|--------|------|------|
| 1 | `POST /rounds` | 建轮（step=started） | 事务 A | `round.updated` |
| 2 | — | 调 Analyzer 切分定性 | **无事务** | — |
| 3 | — | 主张落账本、step=claims_ready | 事务 B | `round.updated` |
| 4 | `POST /rounds/{id}/opening` | 应用标签/去处修改、记席位编制、step=seats_running | 一个事务 | `round.updated` |
| 5 | （后台）每个席位 | 调模型 → 校验信息包 → Round.attest（存原始意见，只有局部 id） | 每席位一个事务 | `packet.emitted` |
| 6 | 整合 | 合并去重 → 分配全局编号（写账本）→ 组装问卷或草案 | 一个事务 | `round.updated` |
| 7a | — | 有必须问的：step=awaiting_answers、waiting_on=questionnaire | 同 6 | 界面弹问卷 |
| 7b | — | 没有：生成草案，step=awaiting_confirm、waiting_on=draft | 同 6 | 界面弹草案卡 |
| 8 | `POST /rounds/{id}/answers` | 答案落账本（card_answer + question_id）、席位轮计数 +1、step=seats_running | 一个事务 | `round.updated` |
| 9 | — | 计数到上限仍有缺口 → 全部显式化 → 走 7b | — | — |
| 10 | `POST /rounds/{id}/confirm` | **唯一写入**：应用提案 → Document.apply → 新版本 → 记内容与出处的关联 → step=done | **一个事务** | `doc.changed`、`round.updated` |
| 11 | 任意模型调用失败 | step=failed + fail_reason | 一个事务 | `generation.failed` |
| 12 | `POST /rounds/{id}/retry` | 从 `step` 继续，已冻结的卡片不重问 | — | `round.updated` |

四个事件，每个都有明确订阅者：
`round.updated`（界面：步骤与等待状态）、`packet.emitted`（界面：闭环区逐条出现席位发言）、
`doc.changed`（界面：文档更新与版本号）、`generation.failed`（界面：错误与重试）。
