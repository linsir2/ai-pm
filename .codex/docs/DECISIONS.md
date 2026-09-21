# PM Studio 工程裁决记录

- 版本：v0.16
- 日期：2026-09-21
- 作用：记录**工程与契约层面的裁决**。产品级决策仍以 `PRD.md` 为准，字段级定义仍以 `CONTRACTS.md` 为准；本文件只回答"这条到底怎么定的、为什么"。
- 纪律：只写**已经拍板**的事。没定的进最后一节，不写进正文。

### 变更记录

**v0.16**（2026-09-21）

- **新增 §21：R1.5 / R1.5.1 落地留痕与 R1.6 契约追平**。这两次提交（`94c5246` / `bd2bdbc`）当时
  只留了 commit message，没进本文件、也没进 `CONTRACTS.md`——本版补记，并加三条机器钉防止再次发生
- **新增 §22：待拍板（`docs/redesign` 与现行目录结构的取舍、默认模型提供商）**。按"没定的不进正文"
  的纪律单开一节，拍板后并入正文
- **R1.6 趁真链路冒烟修掉一个静默失效**：M28 收模型输出时"落不了位"原来会产出一张 `proposals=()`
  的空填充卡，把裸 `ValidationError` 甩给上层；现在大声失败并映射 503（详见 §21.3）

**v0.15**（2026-09-18）

- **新增 §20：R1.3 接模型与 R1.4 写文档**
- **F3 裁决（确认 vs 纠正）**：采用方案 (A) — **显式 `verdict` 字段**（`CardAnswer.verdict: "confirm" | "correct"`）。
  理由：交互上"确认 + 顺口补充一句"很常见，只有显式字段能表达；隐式"空=确认"约定将来必须推翻
- **R1.4 范围**：`submit_cards`（确认路径）、M20 事务写入、M28 结构化成稿（模型返回 JSON 对齐冻结 Schema）、
  `phase = done` 收尾。**纠正路径留 R2**
- **M28 成稿契约**：模型返回 JSON 数组，每条对齐 `Proposal` 冻结 Schema（`target_label` / `op` / `content`），
  `Proposal.model_validate()` 校验后构造填充卡。**不允许返回纯文本** — 目录规则 §3 "接口只能是契约对象"
- **CR-007（C7）**：`CardAnswer` 加 `verdict` 字段
- **R1.3 已落地**：`harness/model_gateway.py`、`harness/retry.py`、`orchestration/consensus.py`、
  `orchestration/cards.py`、`orchestration/round_driver.py`（`start_round`）

**v0.14**（2026-09-17）

- **新增 §19：R1.2 上下文组装与裁剪**。八条裁决（范围调整：M22 推到 R1.3；模型形状只放窗口；
  token 用启发式且只有一处实现；缺失清单判据收紧成"逐模块落点"；M13 只做已有来源；
  历史输入按 C17/M15 口径；空块带上；单块超预算保留第一块）＋ 六笔账
- **`CR-005`（C10）**：补模型本体形状 `ModelBody`
- **M13 / M24 落地**：`memory/context_assembler.py`、`harness/trimming.py`

**v0.13**（2026-09-17）

- **新增 §18：R1.1 建项目**。六条裁决（注册中心先不落库、种子 id 用稳定语义 id、R1.1 不建 `api/`、
  §5.2 补 L5→L0 与 L0→L3 两条依赖边、缺失清单判据精确化、模板形状只放 `label` + `required`）
  ＋ 七条记账（各自带触发条件）
- **`CR-004`（C10）**：补模板本体形状 `TemplateField` / `TemplateBody`

**v0.12**（2026-09-17）

- **新增 §17：R1 开工前必修六条落地**。三条不动契约形状（事务可嵌套、轮次生命周期收紧、
  顶层字段唯一 = I22），三条动了契约（`CR-002` 给 C7 `CardAnswer` 加 `proposal_states`；
  `CR-003` 给 C13 的 `doc.changed` / `memory.updated` 加 `round_id`；C12/C13 的相位↔原因
  改成同一张表）
- **新增 I22**：同一个文档里顶层块的 `schema_label` 唯一——M20 靠 label 定位块
- **两条新口径**：① 用户叫停以 `done` 收尾（`user_stopped` 在此之前没有任何实现用过）；
  ② `dropRound` 不代填收尾原因——收尾是编排层的一次显式写

**v0.11**（2026-09-16）

- **新增 §16：R0 收尾与未决欠账**。三件事：R0.6 收尾、**里程碑记号改名 `M0–M7` → `R0–R7`**、
  **`ARCHITECTURE.md` 删除**（P9 从"降级为草稿"改成"删除"）
- **§16.2 R1 开工前必修六条**：2026-09-16 深度审查里**挡 R1 路**的部分（提案级要/不要无通道、
  开新轮静默清空未结束轮、`round.updated` 校验不对称、建项目不是一次事务、`label → block` 映射未定义、
  事件 payload 缺 `round_id`）
- **§16.3 记账不修八条**：不挡 R1 的缺陷按**触发条件**挂账，避免它们变成"没人知道的坑"
- **P5 / P9 两行改写**：P5 已由 v0.5 重开并定稿（SQLite 持久化，只装当前这一轮）；
  P9 收口为"该文件已删除，现行载体是 `directory.md` ＋ 本文件 §10"

**v0.10**（2026-09-15）

- **R0.6 的六条裁决**（见 §15）。最要紧的两条：冻结的载体放**代码**（`contracts/frozen.py`），
  文档只写规则与指路；"改它必须先写变更记录"这条由**测试**执行，不靠自觉
- **冻结冻的是形状，不是行为**：字段名 / 类型 / 约束 / 默认值进锁，模型的校验规则与跨对象
  不变量不进——它们已经有不变量表与自己的用例
- **C4 只登记、不冻形状**（层内契约，形状随 R1 的编排层一起定）。标记因此分两态：
  `frozen` 与 `deferred`——不假装它有形状，也不把它从 17 个里拿掉
- **`contracts/` 里没有模型在锁外**：§5.3 接口的入参 / 返回形状也进锁，只是不占 C 编号

**v0.9**（2026-09-15）

- **R0.5 的八条裁决**（见 §14），其中三条是"某件事没有落点"：
  M27 在 R0 里没有消费者 → 推迟到 R1；L7 的工作单元在 R0.5 里没有消费者 → 也推迟；
  恢复不能走 `open_round` → 单开一条路径
- **`round.end_reason` 补第四个值 `process_restart`**：不补的话恢复要么写不出合法值，
  要么把"进程挂了"和"生成失败"混成一件事
- **单实例锁定成库级 + `flock`**：进程死了操作系统自动放锁，不会留下"死锁文件让下次起不来"
- **修正 §13 的措辞**：上一版写"R0.5 装配时必须提供 L7 的工作单元"，按"说不出消费者不写"的
  纪律改成"等 R1 的轮末收尾需要时再加"

**v0.8**（2026-09-15）

- **R0.4 的八条裁决**（见 §13）：`context` 存这一轮的正文快照、L3 补 `openRound` / `dropRound`、
  `read` 可空、`claims` 读权限只声明不执行、广播失败不回滚、写即广播不去重、
  **`card_group.updated` 补 `from_state`**、**回填 `result_version_id` 只改账本**
- 后两条是这一轮最要紧的：不补 `from_state` 就有"用户刚确认完、系统立刻弹一张假冲突卡"这条路；
  回填归账本之后，I19（黑板只有编排层能写）与 §7.1 第 28 步的"一次事务"才能同时成立

**v0.7**（2026-09-15）

- **R0.3 的四条裁决**（见 §12）：被隔离的异常记 WARNING 日志、分发上限 1000（超了抛错）、
  流水的读口不进端口、事件重放不做
- 顺带补齐三处"实现必须知道但文档没写"的地方：事件身份词表、FIFO 的"一轮"含义、
  "8 个事件全部实现"指的是机制而不是"8 个都有人发"（都写进 CONTRACTS C13）

**v0.6**（2026-09-15）

- **R0.2 契约落地的九条裁决**（见 §11）：`card_group` 区块装活对象、变更影响面归模板数据、
  L7 接口分块定、`priority` 值域、`tool.invoked` 的 `role_id` 可空、`claim` 引用规则放宽到留痕、
  `export` 不进接口表、`PromptMessage` 定形状、契约冻结只留 R0.6
- **P4 存储改成标准库 `sqlite3`**：R0.1 已经按这个落地（`schema.sql` + 手写仓库），
  文档里的"SQLAlchemy 2.0"是上一版草稿留下的，撤掉
- 修掉两处文档自相矛盾：PRD 的 L0 三件套要求（改成只有工具和 skill）、PRD 的"变更影响面分析"范围外

**v0.5**（2026-09-14）

- **通信层定案三条**：
  - **写入即广播**：写黑板区块时由黑板按映射表自动发事件（`round`→`round.updated`、`card_group`→`card_group.updated`、其余区块不发）——消掉"写了状态忘了通知"这个失效模式
  - **先提交后广播**：事件必须在事务提交之后发
  - **事件流水**：每条 `publish` 落 append-only 表
- **黑板持久化**（SQLite），但**只装当前这一轮**：
  - 持久化的作用只有三条：崩溃恢复、前端可拉、写工作台与写账本同事务
  - **明确否掉"保留最近 N 轮"**：历史轮次的东西各有各的家（文档与版本 C2/C3、卡片历史、消息与摘要在 C17、结论在 C9、用了什么在 C11），再留几轮等于造第三个历史库，并把 M13 组装上下文的工作重复落一遍
  - 轮末：该落账本的落账本、该留 trace 的留 trace，删掉这一轮的区块；`rounds` 那一行永久保留
  - 每个对象只有一个家：黑板里别处也有家的对象只放 id 引用（`card_group` 放 `group_id`、`confirmed` 放 id）
  - `context` / `claims` **不存正文**，只存引用、优先级、可信度、被裁掉的部分
- **模板 schema 归注册中心**（M27），不再从 `seeds/` 运行时加载；`seeds/` 只是灌初始数据。将来支持多种文档 schema 就是多几条 `kind = 模板` 的条目，不改契约
- **R0 拆成 R0.1–R0.6**（存储底座 → 契约 → 总线与流水 → 黑板 → 装配与恢复 → 冻结），详见 `directory.md` §9.1

**v0.4**（2026-09-14）

- **D1–D7 全部拍板**：L1 归前端（后端只留 `api/`）、前后端分家、C4 放 `orchestration/`、保留 `common/`（只放标识/时间/错误）、文件名用语义名 + docstring 写模块号、`tools/` 分 `capabilities/` 与 `services/`、文档将来挪根 `docs/`
- **目录冻结**，载体是 `directory.md` v0.2
- **标识规格定死**：`<前缀>_<UTC 时间>_<随机后缀>`，前缀词表见 `directory.md` §3.3
- **事件分发定死进程内**：含分发语义（按订阅顺序 await、嵌套发布排队、同轮 FIFO）与异常隔离（一个订阅者抛异常不阻断其它订阅者）

**v0.3**（2026-09-14）

- **撤销两道守卫**（原 P6 架构守卫、P7 漂移守卫）：契约第一时间冻结、要改再商议，不用机器钉。撤掉之后，"层与层之间只认公共契约"靠**契约冻结 + 评审**保证，不靠静态检查
- **P2 包结构、P9 工程决策载体重开**：目录还没商议，`ARCHITECTURE.md` 降级为草稿，等骨架表定完再写
- **契约的分类判据改了**：不是"跨不跨前后端边界"，而是**生产者和消费者是否在同一层**——跨层就是层间契约（第一阶段必须定），单层内部流转的是层内契约（第一阶段不管）。前后端推迟，将来前端的外部契约是层间契约的子集

**v0.2**（2026-09-13）

- 第一轮 11 条契约缺口全部拍板，回写进 PRD v0.13 / CONTRACTS v0.12
- 第二轮补充事项全部拍板：消费时点、手动编辑版本粒度、工程决策落 `ARCHITECTURE.md`、`.codex/docs/` 纳入版本控制
- 工程决策 P1–P9 全部拍板
- 新增两个契约：**C16 Project**（项目本体 + 配置）、**C17 Conversation**（消息 + 滚动摘要）

---

## 0. 术语纠偏

### 0.1 "工具"特指能力工具，不是应用函数

**能力工具**是工具层（L5）里给 AI 用的那一类能力：联网搜索、检索，以及以后设计出来的同类工具。特征：进 L0 注册中心、必须有 `description` / `tags` / `when_to_use`、要能被 AI 自己挑、受 M23 权限约束。

**应用函数**是业务流程里固定的一步，比如"创建项目""导出文档"。特征：**不进注册中心、不需要 tags / when_to_use、不受 M23 管**——没有人会让 AI 去挑"要不要创建项目"。

对代码的约束：两类东西在 `ports/` 里必须分开命名、分目录（能力工具端口 vs 应用服务）。否则三个月后所有函数都会挂上 tags 往注册中心里塞。

---

## 1. 契约缺口裁决（对应评审清单 1–11）

1. **L0 必须能写**：注册中心补 `register` / `updateStatus` / `remove`。M30 要改 skill 状态只能经 M27，否则 §5.4"注册条目唯一写入口 = M27"落不了地。
2. **建项目不是工具**：它是应用函数，负责"建项目 + 按模板实例化 9 字段空骨架"，一次事务完成。见 §0.1。
3. **`base_versions` 要有返回通道**：`assembleContext` 的返回从"上下文块数组"改成 `{blocks, base_versions}`，快照由编排层写进 C1（round 区块只有编排层能写，I19）。
4. **M17 不写 `confirmed` 区块**：M17 只返回候选记忆，区块由编排层写（I19）。原文措辞要改。
5. **C6 `consumed` 的写者**：见 §2。
6. **权限 = 两组白名单**：见 §3。
7. **预算由 Harness 自己算**：见 §4。
8. **新增会话 / 消息实体**，M15 是它的家；滚动摘要与 M13 装配配合，见 §5。
9. **卡片上限恒为 5**：每批最多 5 张，见 §6。
10. **PRD 里 L3 那句话改回"层间不直接读写对方的状态"**：现在那句"层与层之间不直接互相调用"与 §5.2、§5.1 和 §7 全篇打架，是 v0.10 修分层表时的漏网。
11. **回退语义**：见 §7。

---

## 2. 讨论区候选的消费语义（缺口 5）

三个时刻要分清：

| 时刻 | 发生什么 |
|---|---|
| 用户勾选 | `status = selected`（或 `discarded`），**此时还没消费** |
| 下一轮主闭环组装上下文时 | M13 把所有 `selected` 转成 C5 材料包放进上下文，**同一次全部标成 `consumed`** |
| 此后每一轮 | 只取 `selected`，`consumed` 不再带进来 |

所以"消费"发生在**组装那一刻**，不是勾选那一刻；而且是**整批一次性消费**，不是逐条。这个字段存在的唯一理由，是防止同一批候选在之后每一轮被重复带进上下文。用户想再用一次，就再勾一次。

**写者**：谁消费谁改 → **M13**。所以 §5.4 的 C6 那一行要补上 M13（现在只写"M29 生成、用户改 status"，与 C6 字段表的"M13 写上"对不上）。

**待确认的边界**：消费时点是"组装时"，所以这一轮如果失败或没产出任何东西，候选已经标成 `consumed` 了，用户得重新勾。备选是改成"这一轮成功结束才算消费"。

---

## 3. 权限：两组白名单（缺口 6）

权限**不是**一个含糊的 `permissions` 字段，而是角色条目上的两组白名单：

| 字段 | 管什么 | 例子 |
|---|---|---|
| `allowed_tools[]` | 这个角色**能调哪些工具** | 某角色只能用 `retrieval`，不能用 `web_search` |
| `allowed_entries[]` | 这个角色**能在哪些入口出现** | 某角色只在讨论区出现，不参与主闭环生成 |

执行仍在 L6（M23），两个判定：`canUse(role_id, tool_id) -> allow / deny`；`canEnter(role_id, entry) -> allow / deny`（`entry` = `main` / `discussion`）。

配套约束：M7 讨论区预选角色、M8 主闭环挑角色，都要先按 `allowed_entries` 过滤；预设角色可带专属工具，用户自定义角色只能从统一工具池里选，这是**注册时**的校验（M27），不是运行时；声明在 L0、执行在 L6，这条不变。

---

## 4. 预算：Harness 内部算（缺口 7）

预算**不是调用方传进来的参数**，是 L6 自己算出来的：`模型上下文窗口 − 输出预留 − 系统开销`，再叠上**项目配置**。

`trim` 的签名相应改成 `trim(blocks, project_id, model_ref) -> {blocks, dropped}`，L6 内部用 `BudgetResolver` 解析。M24 仍然只按 `priority` 裁，不做语义判断。

因此需要一个**项目配置实体**。注意：C1–C15 里现在**连 Project 本身都没有契约**，这也是缺口 2 的根。**新增 C16 Project**，承载 `name` / `template_id` / `config`，其中 `config` 装预算这类项目级设置。

---

## 5. 会话、消息与滚动摘要（缺口 8）

**肯定要保留**：新增"会话 / 消息"实体**（新增 C17 Conversation）**——C1–C15 里现在完全没有，M15 没有家、§7.3 却要落"消息记录"。含**消息**与**滚动摘要**两部分。

- **M13 只管"这一轮"要带的东西**；之前的历史消息继续保留，不因为进入新一轮就丢。
- **超过预算就开始摘要**，然后从这一轮开始重新计算对话历史上下文。
- 分工：**M15** 决定"带全量历史还是带摘要"（对话历史这一段的家）；**M24** 只按 `priority` 裁，不做语义判断；**M13** 一个 token 都不裁。

---

## 6. 卡片上限恒为 5（缺口 9）

**每批最多 5 张，没有例外**。**填充卡单独成一批**（它的批里只有 1 张）。

所以 CONTRACTS C8 和 PRD 里"填充卡不算"那句括注要删掉——它不是例外，它是不共处。

---

## 7. 回退语义（缺口 11）

### 7.1 一次改动 = 一个文档版本

这条已经由 C3 定死了：**每次写入产生一个版本，版本就是回退的原子单位**。所以"回退到改动之前" = 回退到目标版本。

| trigger | 一次 = 什么 |
|---|---|
| `submit` | 一次卡片组确认（整组一次事务，写几处也只有一个版本） |
| `manual` | 一次手动提交（粒度待定，见 7.3） |
| `rollback` | 回退本身也产生一个新版本，不删不改历史（I8） |

### 7.2 回退有两个入口，都成立

**按版本回退**：指定 `version_id`，用它的 `snapshot` 写一次。

**撤销某次 AI 写入**：因为 `submit` 产生的版本上带 `group_id`，可以查"这个 group 产生的版本"再回退到它之前那版。这更贴合 PM 的心智——他记得的是"上次 AI 改的那批我不满意"，不是"第 37 版"。

回退之后一样发 `doc.changed {trigger: rollback}`，记忆层照常做失效判定。

### 7.3 待你定：手动编辑怎么算"一次"

| 选项 | 说明 | 代价 |
|---|---|---|
| (a) 每次显式保存 = 一版 | 边界清楚，回退粒度可预测 | 界面上要有明确的保存动作与反馈 |
| (b) 停止输入 N 秒 / 失焦自动提交 | 体验顺 | 回退粒度不可预测，用户不知道回到哪 |
| (c) 每次击键 = 一版 | — | 不现实，版本爆炸 |

倾向 (a)。

### 7.4 附带补齐：写接口的入参形状

§5.3 里 `writeDocument(trigger, payload)` 的 `payload` 一直是空的，这是框架要写的签名，不能靠猜：

| trigger | payload |
|---|---|
| `submit` | `{group_id}`——M20 自己去黑板读这一组，取 `state = kept` 的提案 |
| `manual` | `{block_ops[]}`，每项 `{block_id, op: replace\|append, content, expected_version}` |
| `rollback` | `{target_version_id}` |

另外 `submitCards(group_id, answers[])` 的 `answers[]` 补上结构：`{card_id, answer, status}`。

---

## 8. 黑板介质

**现在用内存**。它本来就是"这一轮的工作台"，一轮结束就把该长期留下的落 L7。

代价说白：**进程重启 = 当前轮丢失，用户得重开一轮**。后续肯定要做持久化，届时这条要重开——注意 §5.2 里 L6 只被允许落 trace 到 L7，"轮次状态持久化"归哪层要单独定，别顺手塞进 Harness 就完事。

---

## 9. 第二轮裁决（Q1–Q4）

| # | 事项 | 裁决 |
|---|---|---|
| Q1 | §2 的消费时点 | **组装时消费**。讨论区选完带进主闭环组装上下文时整批标 `consumed`；这一轮失败或没产出，用户要重新勾选 |
| Q2 | §7.3 手动编辑的版本粒度 | **显式保存 = 一版**。界面上要有明确的保存动作与反馈 |
| Q3 | 工程决策落在哪 | **独立文件 `ARCHITECTURE.md`**，PRD / CONTRACTS 各加一句指向（**已由 P9 收口**：该文件 2026-09-16 删除，见 §16.1） |
| Q4 | `.codex/docs/` 未进版本控制 | **已放出来**（`.gitignore` 去掉 `.codex/`），文档已首次提交 |

---

## 10. 工程决策（P1–P9，已拍板）

| # | 决策 | 结论 |
|---|---|---|
| P1 | 语言与栈 | Python 3.13 + pydantic v2 + pytest + ruff |
| P2 | 包结构 | **已冻结于 `directory.md` v0.2** |
| P3 | 端口并发模型 | **IO 端口全 async**，纯计算同步（因为 PRD §7.4 要"产出角色可并行"） |
| P4 | 存储 | SQLite（**标准库 `sqlite3`**，不是 ORM）：`schema.sql` + 手写仓库，迁移用手写顺序迁移 + `schema_version` |
| P5 | 黑板介质 | **已重开并定稿**：SQLite 持久化，但只装当前这一轮（v0.5 / §13）；`context` / `claims` 不存正文。**不顺手塞进 Harness** |
| P6 | 架构守卫 | **撤销**：不做。契约冻结 + 评审替代 |
| P7 | 文档漂移守卫 | **撤销**：不做。同上 |
| P8 | 未实现模块 | **不建桩、不建空壳**，用显式"缺失清单"表示未完成 |
| P9 | 工程决策载体 | **已收口**——`ARCHITECTURE.md` 2026-09-16 删除（草稿，与现行口径矛盾）；现行载体是 `directory.md`（§3 / §6.6 / §8）＋ 本文件 §10 |

P6 / P7（两道守卫）**已撤销**，没有实现口径可写；P5（黑板介质）的实现口径在 §13 与本文件 v0.5 变更记录；目录与依赖方向的硬规矩在 `directory.md` §8。

---

## 11. R0.2 契约落地（2026-09-15）

R0.2 = `contracts/` 从"文档里的表格"变成"代码里的唯一形状出处"。落地时撞到九处需要裁决的地方：

| # | 撞到什么 | 裁决 |
|---|---|---|
| 1 | C12 区块表说 `card_group` 装"当前的 C8 实例，含用户的回应"，不变量却说"别处也有家的对象只放 id" | **活对象放在区块里，冻结出来的东西落账本**。区块装当前轮的 C8 实例；`confirmed` 那一刻整组落 L7 卡片历史，`confirmed` 区块只留 id |
| 2 | C1 要求填充卡确认前执行"硬依赖表"，PRD §14 却把变更影响面列为范围外 | 硬依赖表是**模板数据**（`schema_label → schema_label`），归 C10 模板条目的 `content`；范围外的是 AI 语义层那一半 |
| 3 | PRD §6 说注册条目"每条"都要三件套，C10 说只有工具和 skill | 按 C10（契约是字段级唯一真相源），改 PRD 那句；理由：只有要给 AI 挑的条目才需要描述 |
| 4 | §5.3 每层都有接口表，L7 只有一句话"各实体的读写" | **L7 不预先定一个大 Protocol**：谁先当消费者谁定自己那一小块（R0.3 流水、R0.4 工作台与共享事务、R1 业务读写），避免凭空发明一整套存储接口 |
| 5 | C12 的 `priority` 没有值域，M24 却"只按它裁" | `≥ 0` 的整数，越大越先保留；同值按 `(source, ref)` 排序。数值由 M13 给，M24 只排序与裁剪 |
| 6 | C13 的 `tool.invoked` 需要 `role_id`，但检索由记忆层在组装时发起 | `role_id` **可空** |
| 7 | 引用归因规则 1 说"`claim` 只允许出现在 C4 内部"，但留痕（C11）必然带 `claim` 引用 | 规则改成：`claim` 只允许出现在**编排层内部的对象及其留痕**里（C4 + C11）；C7 / C9 不许 |
| 8 | `export` 在 §5.3 有签名，但返回写的是"交付物"，没有形状 | **不声明**：声明它就得发明返回类型。PRD §14 已列为范围外，等 M21 真做时定 |
| 9 | `complete(model_ref, messages)` 的 `messages` 不是契约对象 | 定一个极小形状 `PromptMessage { role, text }`——它是跨层刚需，不定就是一道无类型接缝 |

另外两条同批决定：**契约冻结只留在 R0.6**（R0.2 的形状要先被总线与黑板用一遍）；
**每个契约模型都有一条"字段集与文档逐字相符"的用例**，文档与代码从此不可能悄悄漂移。

**当前没有跨层调用方的方法**（先声明、R1 评审时删）：`MemoryPort.retrieve`、`MemoryPort.invalidate`。

---

## 12. R0.3 事件总线与流水（2026-09-15）

| # | 问题 | 裁决 |
|---|---|---|
| 1 | 一个订阅者抛异常被隔离之后，异常去哪？文档只说"不阻断其它订阅者" | **标准库 `logging` 记一条 WARNING**（事件类型 + 堆栈）。隔离不等于静默吞掉，否则"记忆失效没跑"永远查不出来 |
| 2 | 订阅者之间形成环（A 处理器再发 A）怎么办？ | 总线上限：**一次 `publish` 引发的分发超过 1000 条就抛 `EventDispatchOverflow`**，并清空队列。不静默截断，也不把进程挂死 |
| 3 | 流水要不要读口？ | **不进端口**：`EventLogPort` 只有 `append`。仓库另提供"按轮次读"，消费者是复盘界面与验收测试；这与 §5.2"L1 直接读存储"一致 |
| 4 | 事件重放 | **不做**。重放要求每个订阅者幂等，现在一个都没有 |
| 5 | 流水与黑板区块要不要同一个事务？ | **R0.3 不解决，留给 R0.4**：那时定黑板那一小块 L7 端口时一起定（C12 已经要求"写工作台与写账本同一个事务"）。先记在这里，不埋着 |

**落地形态**：`communication/event_bus.py`（M12）+ `storage/event_log.py`（`events` 表）+ 
`contracts/interfaces/storage.py` 的 `EventLogPort`（L7 分块定的第一块）+ `common/ids.py` 的 `evt` 前缀。

---

## 13. R0.4 黑板（2026-09-15）

| # | 问题 | 裁决 |
|---|---|---|
| 1 | C12 区块形状表与 §7.1 都写上下文块带 `content`，同一节的不变量却写"`context` / `claims` 不存正文" | **存**：区块里的正文是**这一轮的快照**，轮末随区块删；长期正文仍以 C2 / C5 / C9 为准。那两句"不存正文"删掉 |
| 2 | §5.3 的 L3 只有 `read` / `write`，C12 的"轮末删区块""用户开新一轮时再删"没有落点 | 补两个操作：`openRound(round_id)`（设当前轮 + 清别轮的残留）、`dropRound()` |
| 3 | `read(region)` 返回值写死 `RegionValue`，但开轮时只有 `round` 有值 | **可空**：没写过就是 `None`，不是错误 |
| 4 | §5.3 的 `read` 写"所有层都能调"，与 C12"`claims` 只有编排层能读"打架 | `read` 那行加 `claims` 例外；并明说它是**声明、不靠机器执行**（进程内没有身份边界，守卫已撤） |
| 5 | 广播失败要不要回滚区块 | **不回滚**——"先提交后广播"的推论：事务已提交，无从回滚。状态权威，通知后手 |
| 6 | 写同一个值要不要去重 | **不去重**：写即广播是机械规则；过滤是订阅者按 payload 判断的事 |
| 7 | `card_group.updated` 缺 `from_state`，而 M20 的触发条件只能靠它分清"刚确认"与"确认之后又被写" | **补 `from_state`**。否则 M20 回填后会被自己触发第二次，第二次比对时块版本已被它自己改过 → **假冲突卡** |
| 8 | I19（黑板只有编排层写）与 §7.1 第 28 步"M20 回填 `result_version_id`"冲突 | **回填只改账本里的卡片历史那一份**，黑板区块里的 C8 不参与。M20 的事务仍然只碰账本 |

**落地形态**：`communication/board.py`（M11：`Blackboard` 本体 + `BoardReader` / `BoardEditor` 两个句柄）
＋ `storage/board_store.py`（`rounds` / `board_regions` 两张表）＋ `contracts/interfaces/storage.py` 的
`BoardStorePort`（L7 分块定的第二块）。

**记一笔、留给后面**（R0.4 范围内没有消费者，所以现在不加参数）：C12 说持久化换来"写工作台和写账本
能在同一个事务里"，而真正需要它的场景是**轮末收尾**（落消息、落 trace、然后删区块，directory §6.5
用"然后"连成一件事）。**口径修正（见 §14 第 2 条）**：R0.5 也不做这件事——那时仍然没有消费者
（恢复只写一行 `rounds`）。等 R1 做轮末收尾时再加 L7 的工作单元句柄。

---

## 14. R0.5 装配与恢复（2026-09-15）

| # | 问题 | 裁决 |
|---|---|---|
| 1 | directory 的阶段说明写"R0 阶段 M27 最小可用"，但 §9.1 的 R0.1–R0.6 里没有它的位置 | **推迟到 R1 第一步**：模板 schema 要到 R1 建项目时才被读、角色要到编排挑人时才被读。在 R0 里做它没有消费者，违反"说不出的消费者一律删掉" |
| 2 | §13 写"R0.5 装配时必须提供 L7 的工作单元" | **改为等 R1**：R0.5 里没有消费者（恢复只写一行 `rounds`，是单对象写入）。真正需要共享事务的是轮末收尾 |
| 3 | C12 要求崩溃恢复"保留其五个区块"，而 L3 的写句柄要求先 `open_round`，`open_round` 会清掉别的轮的区块 | **恢复单开一条路径**（`Blackboard.recover_unfinished_rounds`），不进 `open_round` 的清理。文档写明"**恢复 ≠ 开新一轮**" |
| 4 | `round.end_reason` 只有三种值，恢复要写"进程重启" | 补 `process_restart`（第四个值）。否则要么写不出合法值，要么把两种不同的失败混成一个 |
| 5 | 单实例锁的粒度：文档写"防两个进程开同一个**项目**"，而库是一个文件 | **库级**：一个进程一个库。文档同步改口径 |
| 6 | 锁怎么实现 | **`flock` 一个 `<库文件>.lock`**：进程死了操作系统自动放锁，没有"崩一次就再也起不来"的坑。代价是 Windows 不适用（本机 Linux）。**锁跟着 `Database` 对象活**——丢掉引用就等于放锁，所以 `Runtime` 必须一直持有它 |
| 7 | 运行时要不要暴露 `BoardEditor` | **不暴露**：I19 说写句柄只给编排层，放进谁都能拿的运行时对象等于装配这一步自己破规矩。写句柄留在 `build_runtime` 内部，当前只交给恢复流程 |
| 8 | 恢复要不要发事件 | **发**。开机时没人订阅，但流水要能回答"这轮为什么变成 failed"；生产者用 `orchestration`（轮次状态的家） |

另外两条同批定下：**启动顺序钉死**（开库拿锁 → 恢复 → 才交出运行时；顺序反了，新一轮会先把
失败轮的区块清掉）；**"缺失清单与实际一致"要有判据**——清单 + 已实现 = PRD 附录 A 的 30 个编号，
清单里的模块不许有实现，都有测试钉着（`bootstrap/missing.py`）。

---

## 15. R0.6 契约冻结（2026-09-15）

| # | 问题 | 裁决 |
|---|---|---|
| 1 | "契约冻结"怎么落地才不会退化成一句口号 | **载体是代码**：`contracts/frozen.py` 三张表（冻结标记 / 形状锁 / 变更记录）。文档（CONTRACTS §0.2）只写规则与指路，不复制形状——形状只有一处，就不可能有两份说法 |
| 2 | "改它必须先写变更记录"这条规则谁执行 | **测试执行**：形状锁与实现不一致就红，报错里直接给"先写变更记录、再改锁、再把 `frozen_in` 指过去"的顺序。P6 撤掉的是架构守卫（静态检查依赖方向），不是"契约不许悄悄改"——这一条要靠机器钉，否则它只是文档里的一句好话 |
| 3 | 冻结冻的是形状还是行为 | **只冻形状**（字段名 / 类型 / 约束 / 默认值）。模型的校验规则与跨对象不变量不在锁里：它们归 CONTRACTS §6 的不变量表与 `invariants.py` 的用例 |
| 4 | C4 的形状还没定，17 个契约怎么都算"已冻结" | **标记分两态**：`frozen`（16 个，有形状锁）与 `deferred`（C4，层内契约，形状随 R1 的编排层一起定）。**不假装 C4 有形状**——写"未冻结"比编一个形状诚实，也比把它从 17 个里悄悄拿掉诚实 |
| 5 | `contracts/` 里还有 §5.3 接口的入参 / 返回形状（`PromptMessage` / `AssembleResult` / `TrimResult` / `WriteDocumentResult` / `CreateProjectResult`），它们不占 C 编号 | **也进锁**（`INTERFACE_SHAPES`）。判据是一条全覆盖：`contracts/` 里每个模型要么挂在某个 C 编号下，要么挂在这些接口形状里，**没有第三个去处**；测试两向对齐（表里不许有幽灵，现实中不许有漏网） |
| 6 | "变更记录"记在哪、记什么 | 记在 `CHANGE_RECORDS`：`CR-xxx` / 日期 / `CONTRACTS.md` 版本 / 一句话为什么动 / 动了哪些契约；首条 `CR-001` 覆盖 C1–C17（形状来自 R0.2–R0.5 的落地，即 CONTRACTS v0.9–v0.16 的全部修订）。每个契约的 `frozen_in` 必须指向一条**列了它**的记录，测试钉住 |

**落地形态**：`contracts/frozen.py`（冻结标记 / 形状锁 / 变更记录 / `python -m pmstudio.contracts.frozen`
的核对入口）＋ `tests/contracts/test_freeze.py`；`tests/contracts/test_contract_inventory.py` 的契约家族表
改成从冻结表读，避免"哪个模型属于哪个契约"有两处出处。

---

## 16. R0 收尾与未决欠账（2026-09-16）

### 16.1 R0 收尾

- **R0.6 落地并提交**：`contracts/frozen.py`（冻结标记 / 形状锁 / 变更记录 `CR-001`）＋
  `tests/contracts/test_freeze.py`；`test_contract_inventory.py` 的契约家族表改从冻结表读，
  "哪个模型属于哪个契约"从此只有一处出处
- **里程碑记号改名**：`M0–M7` → `R0–R7`，步骤 `M0.1–M0.6` → `R0.1–R0.6`。**模块编号仍是 `M1–M30`。**
  原因：原来里程碑与模块共用 `M`，`M1` 既指"项目容器"（模块）又指"最小生成闭环"（阶段），
  已经造成"`M1` 第一步指谁"这类引用歧义
- **`ARCHITECTURE.md` 删除**：它是上一轮的提案草稿，正文里至少三处与现行口径矛盾——
  `SQLite + SQLAlchemy 2.0`（P4 已撤，实为标准库 `sqlite3`）、`src/pmstudio/` 目录
  （实为 `backend/pmstudio/`）、两道守卫（P6 / P7 已撤销），却仍自称"工程与实现决策的唯一真相源"。
  现行载体：`directory.md` §3 / §6.6 / §8 ＋ 本文件 §10
- **约定**：本文件与 `PRD.md` / `CONTRACTS.md` 的"变更记录"里提到 `ARCHITECTURE.md` 的地方
  是**历史留痕**，不代表现行口径；现行口径只认上面那份清单
- **R0 退出条件**（`directory.md` §9）逐条对照：17 个契约冻结 ✓、黑板五区块可读写且写即广播 ✓、
  8 个事件能发能订且进流水 ✓、崩溃恢复可验证 ✓、全程无 AI 与层内实现 ✓、缺失清单两向对齐 ✓

### 16.2 R1 开工前必修

来自 2026-09-16 的深度审查。验证口径以**当前工作区重跑为准**：421 测试全绿、ruff 干净、
冻结表 46 个模型无幽灵无漏网（报告里记的 349 与当前工作区不一致，是统计时点不同）。
下面六条**挡 R1 的路**，开工前逐条落地：

| # | 问题 | 处置 | 验收 |
|---|---|---|---|
| 1 | **P1-2 提案级"要 / 不要"没有传输通道**：`CardAnswer` 只有 `{card_id, answer, status}`，而 M20 要取 `state = kept` 的提案——没有任何接口能写 `Proposal.state` | `CardAnswer` 加 `proposal_states`，**要求显式列出该卡的全部提案**，缺一个就报错（"忘了传 = 默认要"会把用户没要的内容写进文档） | 走 `CR-002`；`test_c7_card.py` 加"缺一条就红"的用例 |
| 2 | **P1-3 开新一轮会静默清空"还在等用户"的上一轮**：`open_round` 直接 `drop_other_regions`，上一轮的 `rounds` 行停在 `awaiting_user` 永不变更，重启还会被标成 `process_restart`（说假话） | **先选"禁止"**：`open_round` 前查未结束轮，有就抛 `ContractViolation`；`superseded`（取代）语义留到真有第二个入口的 R3 再引入 | `test_board.py` 加"有未结束轮时开新轮报错"的用例 |
| 3 | **P1-4 `round.updated` 与 `RoundRegion` 校验不对称**：payload 只在 `phase = done` 时要求 `end_reason`，且 `phase = done` ＋ `end_reason = failed` 这类矛盾组合能构造 | payload 校验与 `RoundRegion` 对齐（`done` / `failed` 都要求 `end_reason`），并加 phase ↔ reason 允许表 | `test_skeleton_events.py` 加组合用例。**只补严，无 CR** |
| 4 | **P2-1 建项目不是一次事务**：`Ledger.create_project` 与 `create_document` 各开一个事务，而 `Database.transaction` 嵌套直接 `OperationalError`（已实测）——C16 的"一次事务完成"目前无法表达 | `Database.transaction()` 支持嵌套（内层用 `SAVEPOINT`）；Ledger 现有方法不动 | `test_ledger.py` 加"中途失败全回滚"用例 |
| 5 | **P2-4 `label → block` 映射规则未定义**：`Scope.selected_fields` 与 `Proposal.target_label` 是字段名，写入路径却用 `block_id`，中间那一步没有落点 | 定死"**一个 `schema_label` 在一个文档里只有一个顶层块**"，写成 C2 不变量 ＋ `create_document` 建块时校验；M20 按 `target_label` → `schema_label` 定位 | 走 CR；`test_c2_document.py` 加重复 label 报错用例 |
| 6 | **P2-3 事件 payload 缺 `round_id`**：`doc.changed` / `memory.updated` 没有该字段 → 流水 `round_id` 为 NULL → `read_by_round` 查不到本轮的文档写入与记忆失效 | 两个 payload 加 `round_id`（给契约加字段是兼容的） | 走 CR；`test_event_log.py` 加"按轮查得回这三类事件"用例 |

### 16.3 记账不修（按触发条件）

下面这些**不挡 R1**，先挂在账上；到触发条件时必须裁决——别让它们变成"没人知道的坑"：

| # | 欠账 | 什么时候必须定 |
|---|---|---|
| 1 | **P1-1 讨论轮结束原因写不进 `round` 区块**：`DiscussionEndReason`（`all_spoke` / `user_stopped` / `round_limit`）与 `RoundEndReason`（`completed` / `user_stopped` / `failed` / `process_restart`）没有交集。**信息不丢**——C14 `DiscussionRound.end_reason` 自己存着，round 侧写 `completed` / `user_stopped` 即可 | R3 讨论区落地时；若那时仍需在 round 侧区分，再扩 `RoundEndReason`（加枚举值是兼容扩展） |
| 2 | **P2-2 M30 改记忆状态没有写入口**：C9 与 §5.4 都说"M30 改状态"，但 `MemoryPort` 只有 `record_candidates` / `invalidate`。另：M30 的层属自相矛盾——PRD / directory 把它归 L2，C13 的身份词表却把 `memory` 算成记忆层的生产者 | R6 复利闭环开工前（`MemoryPort` 补晋升 / 退役方法；层属定死一处） |
| 3 | **P2-5 没有任何"加块"通道**：`BlockOp` 只有 `replace` / `append`，`Ledger` 无 `add_block`，块集合在 `create_document` 之后是静态的；连带 `rollback` 的删除 / 重插路径实际不可达（是死代码）。用户手改也加不了块 | R2 之前（跳过卡要落到"待确认问题"分区，就得先能加块） |
| 4 | **P2-6 四处实现时会被撞到的小口径**：`BoardStorePort` docstring 说的"共享事务"已推迟；`ContextBlockSource.discussion` 与"转成 C5 材料包 ＋ `credibility = low`"两种表达重叠未定死；分歧卡的 `answer` 是 `option_id` 还是文本没写死；PRD §7.4 引用不存在（PRD 只有 §7.1–§7.3） | 各自被消费时 |
| 5 | **P3-2"谁写"列与 I19 脱节**：C8 写 `state = 界面`、C7 写 `answer = 用户`、C8 `result_version_id = M20`——而 I19 规定黑板只有编排层能写 | R1 实现卡片组时按 I19 修正（用户动作经编排层落区块） |
| 6 | **P3-3 版本号是写次数，不是内容指纹**：回退把所有块版本 +1，而 `is_memory_invalid` 只看版本 → 一次回退让引用未变块的记忆全部失效 | R4（记忆与上下文）开工前裁决 |
| 7 | **P3-4 事件总线的并发语义未定义**：`publish` 在另一条链 drain 中会早退（嵌套设计），并发任务下调用者先于分发拿到返回。单事件循环下无实害 | R1 引入并发任务前写明串行化口径 |
| 8 | **P3-6 存储侧口径不一致**：`ARCHITECTURE.md` 说 L6 只落 trace，但 C16 要它读项目 `config`（该文件已删除，这一条留在 CONTRACTS 侧对齐） | R1 做 M24 `trim` 时对齐（CONTRACTS §5.2 已写 L6 可读 C16 `config`） |

---

## 17. R1 开工前必修（2026-09-17）

§16.2 列了六条挡路的问题，本章记录它们**怎么定的、落成了什么样**。三条不动契约形状，
三条动了契约（`CR-002` / `CR-003` 与两条校验规则）。

| # | 问题 | 裁决 | 落地 |
|---|---|---|---|
| 1 | 建项目不是一次事务 | **事务可以嵌套**：最外层 `BEGIN IMMEDIATE`，内层 `SAVEPOINT`。理由不只是"顺手"——C12 早定了"写工作台与写账本要能共享一个事务边界"，而 `BoardStore` 与 `Ledger` 各开事务，不能嵌套这条要求就永远落不了地 | `storage/db.py` 的 `transaction()` 带深度计数；内层失败只回滚到自己的保存点，外层可以吞掉它继续提交；深度在 `finally` 里复位 |
| 2 | 开新轮静默清空未结束轮 | **禁止**，并顺手补上"轮末清理 ≠ 收尾"：`openRound` 发现别的未结束轮就抛 `ContractViolation`（同一 `round_id` 重开允许）；`dropRound` 在轮还没写成 `done` / `failed` 时抛错 | `communication/board.py`；`interfaces/communication.py` 两个 docstring 同步。`superseded`（取代）语义留到真有第二个入口的 R3 |
| 3 | 顶层字段唯一 | 定成 **I22**：顶层块的 `schema_label` 唯一，`DocumentSnapshot` 与 `Ledger.create_document` 两头校验；**只约束顶层**——定位只发生在顶层，嵌套重名等 R2 引入加块时再定 | `models/document.py` 的 `assert_unique_top_level_labels`；写路径"先校验后动手"，拒绝就一行都不写 |
| 4 | `round.updated` 与 `RoundRegion` 校验不对称 | 两头用**同一张表**（`PHASE_FOR_END_REASON`）：`completed` / `user_stopped` → `done`，`failed` / `process_restart` → `failed`；非终态不许带原因 | 表放 `contracts/enums.py`（C12 与 C13 都要用，放任何一边都会让另一边绕圈 import）；`skeleton/board.py` 与 `skeleton/events.py` 各自按它校验。**用户叫停以 `done` 收尾**是本次新定的口径 |
| 5 | 填充卡提案级裁决没有通道 | `CardAnswer` 加 `proposal_states`（`CR-002`）；"必须列全"写成跨对象规则 `check_proposal_states`——`CardAnswer` 自己只拿得到 `card_id`，"列全"要看得见卡片 | `models/card.py` + `contracts/invariants.py`。**只有 `answered` 要求列全**；`pending` / `skipped` 不许带值；非填充卡不许带 |
| 6 | 事件 payload 缺 `round_id` | `doc.changed` / `memory.updated` 加 `round_id`（`CR-003`），**可空**：用户手改不在一轮里（§5.2） | `skeleton/events.py`；`event_bus._build_entry` 本来就从 payload 取流水索引，补上字段即通。`project_id` 这次**不加**——没有"按项目查流水"的消费者，说不出消费者就不加（记在 §16.3） |

**为什么第 4 条不能只改 payload**：黑板是"先提交后广播"（C12）。region 宽松而 payload 严格的话，
一条矛盾的 region 能写进库、却构造不出 payload——就成了"库里写了、广播炸了、调用方拿到异常"。
所以两头必须同表，并专门加一条用例钉住"合法 region ⇒ 合法 payload"。

**这次改到的既有用例（4 条）**：`test_c7_card.py` 的 `CardAnswer` 字段集；`test_skeleton_events.py`
两处 payload 字段集；`test_skeleton_board.py` 原来拿 `FAILED + COMPLETED` 当合法配对（现按相位配原因）；
`test_board.py` 的 `drop_round` 用例（改成先收尾再清理）。

**落地形态**：新增 `tests/storage/test_transactions.py`；`CR-002` / `CR-003` 与随之更新的形状锁、
`frozen_in`（C7 → `CR-002`，C13 → `CR-003`）；CONTRACTS v0.19。测试 421 → 465；`ruff` 干净；
冻结表 46 个模型两向对齐。

**对 §16.2 的一处更正**：第 5 条当时写"走 CR"，但顶层字段唯一**没有改任何字段**（形状不变），
按"只冻形状"的口径不需要 CR——它落在 I22 与 `invariants.py`，已按此执行。

---

## 18. R1.1 建项目（2026-09-17）

R1 的第一刀：把"建项目"从测试里的手工拼装变成真实现——按模板实例化 9 个字段空骨架，一次事务。
模板 schema 归 L0 注册中心，初始数据在 `backend/seeds/`。

| # | 问题 | 裁决 | 落地 |
|---|---|---|---|
| 1 | 注册中心要不要落库 | **先不落库**：种子每次启动重新装载、结果完全一致；"跨重启的写"今天还没有入口 | `registry/entries.py` 是内存实现。代价写明：**重启后回到种子的样子**。触发条件见下面第 1 笔账 |
| 2 | 种子条目的 id | **由数据文件给定**（稳定语义 id，如 `reg_tpl_initial`），**不走 §3.3 的时间戳格式** | `directory.md` §3.3 补了例外；`registry/seeds.py` 只读不生成 id |
| 3 | R1.1 建不建 `api/` | **不建**：`api/` 的形状取决于前端协议，而协议在 `directory.md` §11 #7 里明确"前端开工前定" | 调用方（今天的测试、将来的 HTTP 层）直接拿 `Runtime.project_service` |
| 4 | §5.2 补不补依赖边 | **补两条**：L5 → L0（建项目要读模板定义，只读）、L0 → L3（改状态要发 `registry.updated`） | CONTRACTS §5.2 表 ＋ 两段"为什么" |
| 5 | 缺失清单的判据 | **精确化**：加"非模块实现"登记表，把"建项目是应用函数、不占 M 号"这个例外点出来 | `tests/bootstrap/test_missing_modules.py`；局限写在注释里——它不能证明 M18–M21 还没做，等 M20 落地时收紧成逐模块判据 |
| 6 | 模板形状放哪些字段 | **只放 `label` + `required`**：`required` 的消费者是 M28（必填字段缺内容产补信息卡）；常驻上下文（M13，R4）与字段间依赖提示（M2 / M28，R1.2+）真被读时再加 | `CR-004`；PRD 附录 B 的"作用"一列留在 PRD，不进数据文件（同一件事不写两遍） |

**这一轮的账**（每条带触发条件，别让它们变成没人知道的坑）

| # | 欠账 | 什么时候必须定 |
|---|---|---|
| 1 | **注册中心不落库**：用户删掉的 skill 重启会回来 | R6（M30 晋升 / 退役，或界面能删 skill 时）——那时才知道表要存什么 |
| 2 | **预算两个数字是默认值**（`output_reserve_tokens = 4096` / `system_overhead_tokens = 512`），正经出处是模型元数据表 | R1.2 接模型时（`directory.md` §11 #6） |
| 3 | **空骨架块的 `source_card_id = None`**：C2 把"空"解释成"用户手写"，而模板实例化出来的空块两者都不是 | R1.2 有手改流程时精确化（可能需要第三种"无来源的空骨架"） |
| 4 | **`registry.updated` 不绑轮次，按轮读口查不到它** | 复盘真的要看注册变更时。今天只有"直接查 `events` 表"这一条路——**读口不进端口**是 R0.3 的裁决，不是遗漏 |
| 5 | **`ProjectService` 不是完整的 `AppServicesPort`**：只实现了 `createProject` | R1.4（M20 文档写入）落地时，两者各自实现、由装配层合成端口。今天**不假装**它是整个端口 |
| 6 | **L5 拿到了具体的 `Database`**（为了开外层事务）：与"层间只认契约"略有距离 | L7 的读写口按消费者逐步端口化时（CONTRACTS §5.3 的 L7 注） |
| 7 | **模板 id 的版本语义**：改模板 = **新增**一条条目 id，别改老的那条 | 第一次要改模板时（PRD 说模板会持续迭代）。改老条目会让已建文档的 `template_id` 指到另一份字段分区 |
| 8 | **C10 的一条注册时校验还没做**："用户自定义角色只能从统一工具池里选"（预设角色可带专属工具）——它要等角色（M6）与工具池真的存在才有意义 | R1.2（M6 角色注册表）落地时，在 M27 的 `register` 里补这条校验 |

**改到的既有用例（2 处，都是本轮方案里说好的）**

- `tests/bootstrap/test_missing_modules.py`：加"非模块实现"登记表 ＋ "说做了的要有落点"的镜像断言（决策 5）
- `tests/bootstrap/test_round_skeleton.py`：第 1 步从手工拼装换成 `project_service.create_project`——
  端到端验收自此每一跳都是真实现

**落地形态**：`registry/`（M27：条目读写、按 kind 校验本体、改状态发事件）、
`tools/services/project_service.py`（建项目应用函数）、`seeds/template.initial.json`（9 字段）、
`CR-004`（C10 模板形状）；`bootstrap` 灌种子并把 `registry` / `project_service` 交给运行时。
测试 466 → 498；`ruff` 干净；冻结表 48 个模型两向对齐。

---

## 19. R1.2 上下文组装与裁剪（2026-09-17）

R1 的第二刀：`assembleContext`（M13 说带什么）＋ `trim`（M24 说带多少）。退出条件是
"一轮能拿到 `AssembleResult` 并按预算裁出 `TrimResult`"。

| # | 问题 | 裁决 | 落地 |
|---|---|---|---|
| 1 | R1.2 的范围 | **M13 + M24**，**M22（模型接入）推到 R1.3**。§7.1 第 4–9 步一次模型调用都不需要；而接模型要同时定"元数据形状 + 真接入方式（HTTP 依赖）+ 密钥怎么放"三件事——挤进同一步，这一步就没有干净的验收线。M8 复述卡是模型的第一个真消费者，跟它一起做接口才有真实场景可测 | 本轮不动 `harness/` 的模型侧 |
| 2 | 模型元数据形状 | **只放 `context_window_tokens`**（`CR-005`）。endpoint / 模型名 / 密钥来源等 M22 真正要用时再加（加字段兼容）；**密钥本身永不进条目**，只写"用哪个环境变量" | `ModelBody` + `seeds/model.default.json` |
| 3 | token 怎么数 | **显式启发式**：宽字符（CJK / 全角）1 字 1 token、其余 4 字符 1 token。偏保守（真实 BPE 对常见中文约 0.6–1 token/字）——宁可少带也不超窗。真分词器按模型定，**封装成 `estimate_tokens` 一个函数**，唯一的调用点是 M24，替换成本一行 | `harness/trimming.py` |
| 4 | 缺失清单的判据 | **从"包不存在"收紧成"逐模块落点文件不存在"**（并把背面也钉住：说做了的必须有文件）。原因：M13 落地后 `pmstudio.memory` 存在，继续按包判会把 M14–M17 / M22 / M23 / M25 / M26 一起冤判成"已实现" | `tests/bootstrap/test_missing_modules.py` 的两张表（含 20 个还没做的模块的计划落点） |
| 5 | M13 组装哪些来源 | **只做已有数据的来源**（文档全文 + 本轮输入）。简报（M14）、历史（M15）、检索（M16）、记忆（M17）、讨论（R3）等落地时**给 M13 加构造参数**即可——`assembleContext` 的入参形状不变（只有 `round`），加来源不破坏调用方 | `memory/context_assembler.py`；今天产出 10 个块（9 文档 + 1 输入） |
| 6 | 历史输入从哪来 | **C17（M15）**，不读 `rounds` 表。§7.1 第 5 行④原来写"任务至今各轮的 `user_input`"，而 `rounds` 归 L3——让 L4 去读会让 L4 依赖 L3（§5.2 没有这条边）；C17 才是"面向界面说过的话"的家（PRD：M15 管全量 vs 摘要） | CONTRACTS §7.1 ④ 已收口 |
| 7 | 空块带不带 | **带上**。`base_versions` 必须覆盖全部顶层块——漏一个，用户手改一个当前为空的块就查不出来（I10）；空块几乎不耗 token | `test_empty_blocks_are_kept_so_the_snapshot_stays_complete` |
| 8 | 单块就超预算 | **保留优先级最高的那一块**（`trim` 里 `kept` 非空才判超预算）。裁到空上下文比超一点预算更糟——复述只能靠猜（I14） | `test_the_first_block_survives_even_when_it_alone_exceeds_the_budget` |

**优先级档位表**（数值属于 M13；表与理由已同步进 CONTRACTS C12）：本轮输入 100 ＞ 文档 80 ＞
简报 70 ＞ 生效记忆 60（`decision` / `lesson` / `preference`）＞ 历史 50 ＞ 材料 40/35/30 ＞ 空档 20。
`fact` 类记忆不进常驻，走 M16 检索以材料形式进来。

**这一轮的账**

| # | 欠账 | 什么时候必须定 |
|---|---|---|
| 1 | **M13 只有两个来源**（文档 + 本轮输入）：简报、记忆、检索、历史都还没有家 | 各自里程碑（M14/M15/M16/M17 → R4；讨论区 → R3）。接的时候只动 `ContextAssembler.__init__` 的依赖 |
| 2 | **模型条目里没有 endpoint / 模型名 / 密钥来源** | R1.3（M22）。加字段是兼容的，但仍要走一条 CR；密钥走环境变量 |
| 3 | **`estimate_tokens` 是启发式**（不是任何一个模型的真分词） | R1.3 接模型时换成那个模型的 tokenizer。差异大了只改这一处 |
| 4 | **`ContextBlockSource.DISCUSSION` 目前没有生产者**：讨论区内容按 C5 的转换规则以 `material`（`credibility=low`）进来 | 与 §16.3 第 4 笔是同一件事（`ContextBlockSource.discussion` 与"转成 C5 材料包"两种表达重叠）；R3 讨论区落地前定死用哪一个 |
| 5 | **缺失清单里的落点文件名是"计划"**：20 个还没做的模块各自写了一个语义名 | 第一次真的放错位置或改名时——同步改那张表（表就是承诺） |
| 6 | **M13 还没做 C6 的消费标记**（"用户勾选的候选在组装时整批标 `consumed`"，§2/D4 定了写者是 M13） | R3（讨论区）。届时 M13 会有唯一的写动作 |

**落地形态**：`memory/context_assembler.py`（M13）、`harness/trimming.py`（M24：`estimate_tokens` /
`BudgetResolver` / `Trimmer`）、`seeds/model.default.json`、`CR-005`；`bootstrap` 把
`context_assembler` 与 `trimmer` 交给运行时；端到端验收从"建项目"延伸到"上下文就绪"。
测试 498 → 522；`ruff` 干净；冻结表 49 个模型两向对齐。

---

## 20. R1.3 接模型与 R1.4 写文档（2026-09-18）

R1 的第三刀（R1.3）和第四刀（R1.4）：模型接入 + 写文档。

### R1.3 接模型（已落地）

**退出条件**：一轮能从"用户输入"跑到"理解卡等待用户"，调一次真模型。

| # | 问题 | 裁决 |
|---|---|---|
| 1 | 模型选择规则 | **解析规则（L2 内部 `_resolve_model_ref()`）**：注册中心里 `kind=model` 且 `status=active` 的唯一一条即默认；0 条或 ≥2 条报错。**不加契约字段**；角色级模型记成账、到 M6 落地时再定 |
| 2 | `generation.failed` 的 `step` 谁来填 | **M26 读黑板当前轮**拿 `round_id` + `phase`（= step），据此上报；不在轮里调模型直接报错且不发事件 |
| 3 | 密钥缺失时的行为 | **惰性读**（第一次要调模型时才读），不是启动 fail-fast |

**落地形态**：`harness/model_gateway.py`（M22）、`harness/retry.py`（M26）、
`orchestration/consensus.py`（M8）、`orchestration/cards.py`（M9）、
`orchestration/round_driver.py`（`start_round`）；`contracts/models/registry.py` 加 `ModelBody` 三个字段
（CR-006）；`seeds/model.default.json` 同步；端到端验收从"上下文就绪"延伸到"理解卡等待用户"。
测试 522 → 541；`ruff` 干净；冻结表 49 → 50 个模型（ModelBody 加 3 行）。

### R1.4 写文档（本次）

**退出条件**："确认 → 填充卡 → 确认 → 写入 → 轮次完成"整条链跑通，文档多一个版本。

| # | 问题 | 裁决 |
|---|---|---|
| 1 | F3 确认 vs 纠正 | **方案 (A)：显式 `verdict` 字段**（`CardAnswer.verdict: "confirm" \| "correct"`）。理由：交互上"确认 + 顺口补充一句"很常见，只有显式字段能表达；隐式"空=确认"约定将来必须推翻 |
| 2 | M28 成稿返回类型 | **模型返回 JSON 数组，每条对齐 `Proposal` 冻结 Schema**（`target_label` / `op` / `content`）。`Proposal.model_validate()` 校验后构造填充卡。**不允许返回纯文本**（目录规则 §3） |
| 3 | 纠正路径范围 | **留 R2**：R1.4 只做确认路径（`verdict = "confirm"` → 继续 → M28 → M20 → 写入），纠正路径 R2 做 |
| 4 | `expected_version` 用哪个版本 | **用快照版本**（`base_versions[block_id]`），不是当前版本。这是 I10 冲突检测的核心 |
| 5 | M20 越界写 | **I17 强制**：submit 路径从 scope 校验，每条 proposal 的 `target_label` 必须落在 `selected_fields` 内（有选区时） |

**落地形态**：`tools/services/document_writer.py`（M20）、`orchestration/drafter.py`（M28）、
`orchestration/round_driver.py`（`submit_cards`）；C7 `CardAnswer` 加 `verdict` 字段（CR-007）；
端到端验收从"理解卡等待用户"延伸到"文档多一个版本、轮次完成"。

**这一轮的账**（R1.4 不做、触发条件明确）：

| # | 事 | 触发条件 |
|---|---|---|
| 1 | 纠正路径（`verdict = "correct"` → 重新组装 → 复述） | R2 卡片机制补全 |
| 2 | 角色级模型（`C10 Role` 加 `model_ref`） | M6 角色注册表落地时（R2+） |
| 3 | 多模型/每项目选模型（C16 `ProjectConfig.model_ref`） | 有消费者时 |

---

## 21. R1.5 / R1.5.1 落地留痕与 R1.6 契约追平（2026-09-20 / 09-21）

这两步的代码在 09-20 就落地并提交了（`94c5246` / `bd2bdbc`），但**只留了 commit message**：
契约变了六处、工程口径变了四处，`CONTRACTS.md` 与本文都没有记。本章补记，
并把"文档面不许落后机器面"变成机器会红的三条用例（§21.3 第 1 条）。

### 21.1 R1.5 API 层（`94c5246`）

**退出条件**：前端能用 HTTP 跑完"建项目 → 开轮 → 提交卡片 → 读文档与版本 → 订阅事件"。

| # | 事 | 结果 |
|---|---|---|
| 1 | HTTP 入口（不占 M 号） | `backend/web_api/`：`app.py` 五组路由 + `GET /events/stream`（SSE） |
| 2 | 视图投影 | `dto.py` 显式只暴露前端要的字段（`base_versions` / `dropped` / `parent_id` / `snapshot` 不外露） |
| 3 | 事件推送 | `sse.py`：先回放 `event_log.read_by_round` 的历史，再 0.5s 轮询增量 |
| 4 | 错误映射 | `errors.py`：`ContractViolation → 400`、`ValueError → 422`（503 在 R1.6 补，见 §21.3） |
| 5 | 依赖与测试路径 | `pyproject` 加 fastapi / uvicorn / httpx；`conftest.py` 把 `backend/` 放上 `sys.path` |
| 6 | 顺带修的读口 | `event_log.read_by_round(None)` 可读全部事件（复盘界面要用） |

### 21.2 R1.5.1 引用归因与 DDD 收尾（`bd2bdbc`）

| # | 事 | 结果 | 契约 |
|---|---|---|---|
| 1 | **引用归因（I21）** | M13 组装时给可引用块编连续证据号 `E1..En`；M28 把编号清单放进 prompt，模型返回的 `citations` 只能从这个集合里取，再反查成真实 `Citation`（BLOCK 带版本 / USER_INPUT 带 round_id） | **CR-008**（C12） |
| 2 | **I3 审计留痕** | `BlockOp` 加 `source_card_id`：M20 从填充卡取 `card_id` 透传，Ledger 落到 `Block.source_card_id`，`is_ai_written` 因此成立 | **CR-009**（C3） |
| 3 | **D1 修正** | `write_document(trigger, payload)` 收敛为严格两参：M20 自己回黑板读卡片组与轮次；`CardGroup` 收进领域行为（`apply_answer` / `kept_proposals`） | — |
| 4 | **端口签名锁（P0）** | `contracts/signature_lock.py`：`inspect.signature` 严格比对协议与实现（含负例），配 `test_port_implementations.py` 的"声明面 vs 实现面"映射表 | — |
| 5 | **无密钥可跑（P0）** | `harness/fake.py` 的 `FakeHarness`：M8→M28→M20 全闭环不打网络 | — |
| 6 | **数据流修复** | 确认理解卡后，RoundDriver 把 `CONTEXT` 区块里**带证据号的真实块**传给 Drafter——传空数组会让引用归因在真实链路里断掉 | — |
| 7 | 设计稿落地 | `docs/redesign/`（DDD + TDD 重设计，状态：待评审） | — |

**留痕缺口**（R1.6 追平）：`CR-001` / `CR-006` 从未写进 `CONTRACTS.md`；`CR-008` / `CR-009` 只进了 `frozen.py`；
`C3` / `C10` / `C12` 的字段表没跟着更新；`CONTRACTS.md` 头部还停在 v0.22。

### 21.3 R1.6 契约追平与真链路修复（`refactor/ddd-redesign` 工作区）

| # | 问题 | 裁决 | 落地 |
|---|---|---|---|
| 1 | **文档面 ↔ 机器面没人钉**：全仓没有一个用例读 `.codex/docs/`，字段集靠人工抄写，所以"改了锁不写文档"能藏两周 | **加三条用例**：每条 CR 都要在文档里查得到；头部版本号 = 最新 CR 的 `doc_version`；CR 加的字段必须写进对应契约章节的正文 | `tests/contracts/test_freeze.py`；`CONTRACTS.md` 补 v0.23 / v0.24 与 CR-001 / CR-006 留痕、C3 的 `BlockOp` 字段表、C10 的 `ModelBody` 补充字段、C12 的 `evidence_id` / `ref_version` |
| 2 | **进度段与缺清单脱钩**：README 说"R1 未开工"，而 R1.1–R1.5.1 已经落地 | README 的"已实现 / 未实现"逐号列出（不写区间），用例对着 `missing.py` 核；README 承诺的 `pytest` / `pytest-asyncio` / `ruff` 必须真在依赖里 | `tests/bootstrap/test_repo_docs.py`；`pyproject` 的 `[dependency-groups] dev`（原来只声明了 httpx，`uv run pytest` 跑不起来） |
| 3 | **密钥通道是假的**：`.env.example` 说"复制成 `backend/.env` 填密钥"，而 `python-dotenv` 声明了**没有消费者** | 补唯一消费者：HTTP 入口启动时读一次 `backend/.env`（不覆盖已有环境变量；文件不在不是错误） | `web_api/env.py` + `app.py`；`tests/api/test_env.py` |
| 4 | **真链路冒烟发现 M28 静默产空卡**：模型返回的 JSON 一条都落不了位时，旧实现把每条 `continue` 掉，构造出 `proposals=()` 的填充卡；而 C7 要求填充卡必须有提案，于是用户拿到的是裸 `ValidationError`（500），既不知道卡在哪一步、也不知道能不能重试（AC12） | **大声失败**：落不了位就抛 `GenerationFailure(retryable=True)`，报错带上"允许的字段 + 模型实际给的 key"；同时收三种形态（字段名对象 / 提案数组 / 包一层 `{"proposals": [...]}`），模型给的 `op` 不再被静默丢掉 | `orchestration/drafter.py`；`tests/orchestration/test_drafter.py` 五条新用例 |
| 5 | **两个同名 `GenerationFailure`**：`common/errors.py` 有一个（带 step/reason/retryable，但从未被抛）、`harness/model_gateway.py` 自己又定义了一个（签名不同、基类不同） | **合到一处**：家在 `common/errors.py`（错误是 `common/` 允许的三样之一），L6 从这里 import（老路径继续可用）；HTTP 层据此映射 **503**，兑现 P0 承诺 | `common/errors.py`、`harness/model_gateway.py`、`web_api/errors.py`；`tests/api/test_error_mapping.py` |

**这一轮的账**（每条带触发条件，别让它们变成没人知道的坑）：

| # | 欠账 | 什么时候必须定 |
|---|---|---|
| 1 | **"输出格式错误 → 自动重试"没做**：重试只发生在 M26 包着的一次 `complete` 内，而 M28 的解析失败在它外面——PRD §5.1 第 4 条要的是"校验 + 重试" | R2（把校验挪进 harness，或让 M26 包住 M28 那一步） |
| 2 | **模型输出问题的异常类型两处不一致**：非法 JSON → `ContractViolation`（400）；落不了位 → `GenerationFailure`（503） | 同上，一并统一成 `GenerationFailure` |
| 3 | **M28 忽略 `required`**：必填字段（模板里 `required = true`）缺内容时应该产**补信息卡**（PRD 附录 B / C10），现在照样产填充卡 | R2（补信息卡那条链） |
| 4 | **`待确认问题` / `开放问题` 不是给 AI 填的**（PRD 附录 B），但无选区时 M28 会把它们当可填字段——`selected` 来自文档顶层 label，而模板里没有"AI 可否填"的标志 | R2（跳过卡落"待确认问题"时一起定） |
| 5 | **真链路冒烟不是回归测试**：模型输出不确定，它只回答"这条链现在通不通" | 一直如此；要变成回归就得先有确定性替身（M22 的录制回放） |

### 21.4 核验（2026-09-21，`refactor/ddd-redesign`）

- 全量测试：**627 → 649 项**（648 passed + 1 skipped；跳过的是真链路冒烟——不给密钥是正常状态）
- `ruff check .`：**21 处 → 0**（其中 `tests/harness/test_model_gateway.py` 的 `F821` 是真缺陷：
  注解里引用了未定义的名字，补上模块级 import）
- `python -m pmstudio.contracts.frozen`：形状锁与代码一致；49 个模型在锁里，不多不少
- 真链路（DeepSeek）：`DEEPSEEK_API_KEY=... uv run pytest tests/bootstrap/test_live_model_smoke.py -v -s`
  跑通"输入 → 理解卡 → 填充卡（9 条提案，带 `[E10]` 引用归因）→ M20 事务写入 → 文档一版 → 轮次 `done`"

---

## 22. 待拍板（没定的事放这里，不进正文）

### 22.1 `docs/redesign` 与现行目录结构：迁还是不迁

`docs/redesign/` 是 DDD + TDD 的重设计稿（状态：设计稿 v1 · 待评审）。它和现行实现有三处**落点不同，
语义一致**：

| 项 | redesign 写的是 | 现行实现 | 差在哪 |
|---|---|---|---|
| 包结构 | `domain / application / ports / infrastructure / api / bootstrap` | `contracts / communication / orchestration / memory / tools / harness / storage / registry / bootstrap` + `web_api` | P0 的"迁移包结构"没做；但 DDD 的目的（行为收进聚合根、不变量有唯一的家）已就近落地，如 `CardGroup.apply_answer` / `kept_proposals` |
| 端口位置 | `ports/`，签名锁测试叫 `test_port_signatures.py` | `contracts/interfaces/` + `contracts/signature_lock.py` | 机制已落地且更严（严格相等 + 负例）；只是位置与文件名不同 |
| API 路径 | `POST /rounds/{id}/cards` | `POST /card-groups/{group_id}/submit` | 路由按"卡片组"组织，与 C8 的模型一致 |

**两条路**：

- **(A) 文档跟代码（推荐）**：只改 `docs/redesign/02-contracts.md` 与 `04-milestones.md` 里对落点的描述，
  把"已落地但落点不同"标出来；不迁目录。代价：几处文档编辑。收益：零回归风险，而 redesign 的三条
  承诺（聚合行为、端口签名锁、无密钥可跑）都已经成立。
- **(B) 代码跟文档**：把包结构迁成 redesign 的分层。代价：动 60+ 文件的 import、全部测试、以及
  **冻结的 `directory.md` §3** 与 P2；收益：分层名与设计稿字面一致。

判断：**(A)**。迁移前先回答一个问题——**现在的目录让哪个具体的开发任务变难了？** 答不上来就不迁。

### 22.2 默认模型提供商（`seeds/model.default.json`）

种子里的默认模型现在是 `dashscope/qwen3.8-flash` + `DASHSCOPE_API_KEY`。换成 DeepSeek 要动三处：

- `backend/seeds/model.default.json`：`model` → `deepseek/deepseek-chat`、`api_key_env` → `DEEPSEEK_API_KEY`
- `backend/.env.example`：把示例变量名换掉
- **两处测试断言跟着数据走**：`tests/registry/test_seeds.py:80-81`（钉了 model / api_key_env）；
  `tests/harness/test_trimming.py:139`（钉了窗口 32768——**保持 32768 就不用改这条**）

口径上没问题：`ModelBody` 的形状不变（C10 的字段早在 CR-005/CR-006 定完），换的是**数据**；
"注册中心里唯一一条 active 的 model 条目 = 默认模型"这条解析规则也不受影响。

R1.6 的做法是**保守的**：种子不动，冒烟用例在运行时换条目（`remove` + `register`）验证真链路。
要不要把默认换成 DeepSeek，说一声就改——那两行断言不是"为迁就代码而改测试"，是数据本身换了。
