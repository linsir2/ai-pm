# 检查点 · 2026-09-21

> 用途：新会话或换人接手时，读这一份就能知道"做到哪、为什么这么做、下一步是什么"。
> 更新时机：长暂停前、切分支前、交给别的 agent 前。

## 1. 在做什么

给 PM Studio 做**主闭环**：一段零散输入 → 切分定性 → 开场批（复述/写范围/席位/遗留）
→ 席位发现 → 整合成问卷 → 作答 → 落位成稿 → 校验 → 草案卡 → 确认写入 → 版本。
当前**只做设计，尚未开始编码**。

## 2. 仓库状态

- 分支：`new-branch`（工作分支）。`main` / `redesign` 上有另一套完整设计与后端实现
  （到 R1.5.1），**只作参考，不搬契约、不复用代码**。
- 未跟踪：`.codex/`、`AGENTS.md`、`DESIGN.md`（都在首次提交之前）。
- 本次会话产出：`.codex/docs/设计/` 共 9 份（00–07 + 99），过程记录在 `.codex/docs/过程记录/`。

## 3. 已经定的（不要再推翻）

| 决策 | 内容 |
|------|------|
| 形态 | 单用户本地 Web 应用，Python + FastAPI + LangGraph + SQLite + React |
| 骨架 | 13 章固定结构，只有 s5 的功能子树可生长 |
| 修订 | 原地演进 + 版本快照；一次改动 = 一版；版本只增不改 |
| 编排 | 图做阶段骨架，席位做阶段内判断；人机中断三处（开场批 / 问卷 / 草案卡） |
| 流程 | **三个阶段**（主张 / 席位 / 整合）+ 三次中断，加纯代码守卫；不拆成六七个环节 |
| 席位职责 | **只做一件事：出专业意见**（疑问 / 判断，各带依据与理由）；组装是整合者的活 |
| 问卷 | **不是必然环节**：没有必须问用户的东西就跳过，直接出草案 |
| 答案回灌 | 答案进账本后**重新组装上下文、再跑一次缩编席位轮**，不是消费选项就完 |
| 循环 | 席位轮最多 3 次；到上限仍有缺口就全部显式化 |
| 界面 | 文档区主导 + 闭环区（待你处理 + 本轮讨论流，展示每个席位交回的信息包） |
| 内容来源 | 六类出处：用户输入 / 问卷答案 / 席位推导 / 席位提议 / 系统默认 / 外部材料 |
| 可追溯 | 顺着 refs → derived_from 必须落到用户输入或问卷答案；到不了的只能"提议"且默认不勾 |
| v1 限制 | 席位**不能新增 feature 子树**，只能在已有功能字段内补内容 |
| 权限 | 席位与整合者一律不能写文档，写入只走确认端点 |
| 席位 | 领域席位由输入提名，用户可增删；Integrator 只出问卷，不碰草案 |
| 卡片 | 一批最多 5 张、一卡一议题、草案卡单独成批；**卡片里不编辑内容**（编辑归文档） |
| 复述 | 必须包含至少一个"只有读了文档才知道的信息"，否则视为复读 |
| 证据 | 全局唯一编号，只增；修订靠新编号指向旧编号（supersede） |
| 记忆 | 项目简报（= 产品里的 PROJECT.md）：120 行上限、逐条确认、三档（有用/一般/废话） |
| 继承 | 卡片交卷语义、写范围由写入端强制、落位与成稿分开——都借自 main 的设计 |

## 4. 明确否掉的方案（别再提）

- 纯 ReAct 自由循环：目标形状已知，自由循环只换来不可复现。
- 用代码做张力检测/去重：矛盾往往跨字段、非同字面，只能由智能体判定。
- 矛盾对先跑一轮辩论：席位 finding 里已带立场与依据，辩论不产生新信息。
- 出题前让用户选智能体清单：用户不知道选谁，改成"提名 + 用户做减法"。
- 卡片里编辑内容：会引出"卡片改的与文档改的谁优先"。
- 一次弹多组卡片：改成一卷问卷（交卷语义）。
- **席位只出问题、内容全部来自用户**：会让产品退化成问卷填写器，已否；席位改为同时推导内容。
- 把流程拆成六七个环节（含独立候选召回层、独立落位环节、独立写范围卡）：冗余，已合并。
- 开场批四张卡：写范围并入复述卡的"去处"列，遗留项降为只读提示，压成两张。
- 席位做三件事（发现 + 推导内容 + 判定要问什么）：违反职责单一，已改；席位只出专业意见，
  把意见组装成内容候选与题目是整合者的活。
- 问卷作为固定环节：已否；没有必须问用户的东西就整段跳过。
- 答案机械消费（预写 option_means、直接转编号）：已否；答案要回到席位轮重新判断。

## 5. 剩下来的活

**先看 `../设计/04-未决.md`**：三条实现选择（存储 SQLite / 模型走 OpenAI 兼容 / 前端 React）、
内置哪些领域席位、席位每轮最多推几条内容、清单项默认值由谁定。

已定的补充：v1 **不做**写范围选择器（范围就是复述卡里"去处"的集合，写入端强制）；
不做独立落位环节（归位在切分时完成）；门禁只做"必填字段是否为空"并在草案卡提示，不挡写入。

**然后再动代码**：按 07 第七节的三条 lane 并行（领域层 / 编排层 / API+前端），
先定领域层与编排层之间的几个函数签名。

## 6. 已经跑过什么

- 没有跑过测试（还没有代码）。
- 只做过只读检查：`git branch/log`、读取 main 与 redesign 分支的文档（导出到 /tmp 阅读）。
- 一次失败的尝试：并行子智能体派出去后收不到任务，返回空话——因此立了"空返回不等于无发现"这条规矩。

## 7. 风险与假设

- **假设**：单用户、文档规模不大，因此 v1 不做索引层与上下文裁剪（问题 9 已记为关键缺口）。
- **风险**：开局就把席位、整合、问卷全塞进第一版，容易一次跑不通；缓解＝先跑直线骨架。
- **风险**：设计文档已 9 份，容易腐坏；缓解＝只在 `.codex/docs/设计/` 维护，别处不复述。

## 8. 下一步（最佳动作）

1. 作者拍板 `docs/plans/2026-09-22-P1-领域层.md` §3 的 6 处决策（默认按建议实现）。
2. 按该计划逐任务执行 P1（Task 0 → Task 12，每任务一次提交，TDD：先红 → 实现 → 绿 → 提交）。
3. P1 完成后写 P2 计划（application + infrastructure + API + 前端最小集）。

## 9. P1 计划已就绪（2026-09-22）

> **2026-09-22 更新：P1 已完成。** 见本节末尾"执行结果"；下一站是 P2 计划。

- 计划：`docs/plans/2026-09-22-P1-领域层.md`，13 个任务。
- 覆盖：`Document` 聚合、`EvidenceLedger`、`Round` 聚合、四个领域服务
  （Traceability / Assigner / ScopeGuard / Committer）、四道结构性守卫
  （扫 import / 上下文隔离 / 唯一写路径 / 生产路径无假实现）。
- 验收方式：22 个不变量码 × (≥1 正例 + ≥1 反例)，由 `test_invariant_matrix.py` 机械校验；
  外加"案例 A 领域走查"（从用户输入到 v2，不调模型）。
- **计划里核验出的设计问题**（需要作者确认，详见计划 §3）：
  ① `02` 的"写范围 = 复述卡去处"与 `03` 案例 A 互相矛盾（案例 A 写了 s7/s8/s11，都不在去处里）；
  ② `05` 目录图把 ports 放在 `application/` 会违反依赖方向；
  ③ "跨上下文只传 id"照字面执行，`Document.apply` 没法写；
  ④ block 类型由谁定没写；
  ⑤ 事件名 `run.updated` / `round.updated` 不一致；
  ⑥ `Round` 缺席位编制约束（计划里补了 R7）。
- 早先的 4+3 个选择已收敛：存储 SQLite、模型 LiteLLM、前端 React、开发方式 DDD；
  假实现只在测试里（本次定）。它们都进了 P1 计划的 §3 决策表。

### 执行结果（2026-09-23，P2 后端主闭环已完成）

- 代码：`backend/src/pmstudio/{domain(六处小改),application,infrastructure,api,bootstrap}`
  + `backend/tests/{architecture,domain,application,infrastructure,api,bootstrap,e2e}`。
- 验证：`cd backend && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider`
  → **306 passed**（含 7 条 e2e：两条闭环路径 + 断点续传两条 + 席位/模型失败两条）；
  `python3 -m ruff check --no-cache .` → 无告警；仓库零生成物。
- **真模型证据**（qwen3.8-flash via DashScope，`backend/scripts/smoke.py`）：
  `OK version=2 round=r-5afdc9ed project=p-8bc02359` —— 12 条主张 → 6 个席位（3 轮席位轮）
  → 3 轮问卷共 15 题 → 草案 17 条（带 E19/E20/E21 出处）→ 确认写入 v2，
  有内容的章节 s5/s6/s8/s9/s10/s11，缺口与写范围提示都在草案卡上显形。
- 真模型暴露并已修掉 8 类问题，其中两处是**漏实现的规格**（到席位轮上限强制出草案；
  答案必须带 E 编号进上下文）与一处**状态码漏成 409 的 bug**；详见 `learnings.md` L9/L10。
- 还没做：Task 22–25 前端（`frontend/` 尚未创建）、Task 26 的文档同步与浏览器手测。

### 执行结果（2026-09-23，P2 前端最小集已完成）

- 代码：`frontend/`（React 18 + Vite 6 + TS strict，无运行时依赖，除 React 外零第三方库）。
  三栏（左栏项目/文档结构/功能子树 · 中间文档当纸 · 右栏本轮讨论流）+ 底部常驻条 + 两个抽屉
  （问卷 / 草案只覆盖右栏）。
- 验证：`npx tsc --noEmit` 无输出；`npx vitest run` → **6 个文件 69 条纯函数全绿**
  （章节状态 / 功能子树结构 / 作答与勾选载荷 / 事件归约 / 进度七格 / 出处索引）。
- 设计文档：`.codex/docs/设计/06-前端.md`（布局、数据流、逐项核对表、视觉与无障碍、遗留）。
- **真模型证据**（前端经 Vite 代理直连后端，qwen3.8-flash）：
  建项目 `p-3b0ad2b4` → 一轮输入 → 10 条主张（6 种标签）→ 6 个席位 → 5 题问卷
  → 作答（3 答 2 跳）→ 第二轮席位 + 再出 5 题 → 作答（2 答 3 跳）→ 第三轮席位
  **失败**（`seat.permissions` 意见引用了不存在的编号 `E34`，`model_packet_unparsable`）
  → 界面显示失败原因 + "接着跑这一轮"，点它只补没交回的席位。
- 这一轮练出来的三条界面规矩（写进 `AGENTS.md` 与 `06-前端.md`）：
  ① 事件只推进进度与"该重读了"，数据从后端读回来；
  ② 组件不做业务判断，判断都在纯函数里；
  ③ 后端给不出的东西界面不做（版本历史、搜索、章节完整度、答题进度、历史轮发言都不做）。
- 还没做：Task 26 的浏览器手测复核（已截图核对四个状态：复述卡前的输入态、席位在跑、失败、
  问卷抽屉）、`04-未决.md` 的前端遗留条目、以及上面那轮"失败后接着跑"的最终草案与写入。

### 执行结果（2026-09-24，P3 第一批：席位提名交给 Analyzer + 未决 B–D 订正）

计划：`docs/plans/2026-09-24-P3-席位提名与未决订正.md`（9 个任务，已全部完成）。

**改了什么**

1. **席位名单由判断产生**（以前是"常驻 + 关键词命中"）：
   - 报文加 `seats[]`（每条 `{seat_id, why}`，必填、1–7 条）；挑到目录外 → 整轮失败；
   - 提示词加 `$seat_catalog` 槽位与"怎么挑席位"四条；`ClaimAnalyzer` 返回 `Analysis(claims, seats)`；
     `StartRound.analyze` 与主张同一事务落库（`Round.nominated_seats`）；
   - 投影：`nominated_seats[].why`（主口径）+ `source`（agent/catalog）+ 新增 `available_seats`；
     只有老轮次才回落目录匹配，界面照实说"这名单没经过判断"。
2. **复述卡**：每条席位显示分析者给的理由；常驻/命中降为小标签；底部"目录里还有 N 个，想加就加"。
3. **注册表 7 → 10**：补状态机与流转 / 第三方集成 / 合规与数据安全（都靠关键词命中）；
   提交注册表外的 seat_id → 422（以前会拖到执行时炸 500）。
4. **问卷封顶两批**（C6）：`MAX_QUESTIONNAIRES = 2`，到顶降级成 s11 开放问题，
   理由写进证据与缺口行；席位轮上限（3 次）退为兜底。
5. **草案卡**：加「本批全接受（N 条）」与「全不勾」。
6. **文档**：`04-未决.md` B–D 逐条落定（C1/C2/C4/C6/D3 写成现状，C5/C8/D5 标 P3）；
   `01` F8、`02` §4②、`06-前端` 同步。

**真模型证据**（qwen3.8-flash，临时库 `/tmp/pmstudio-p3.db`，不碰作者的项目数据）

| 输入 | 分析者挑的席位 | 没挑的 |
|---|---|---|
| 订单导出 / 按时间筛选 / 数据量几万 | export_import(触发) + business_rules + data_scale + frontend_interaction + permissions，各带贴着输入的理由 | **tech_approach（常驻但没挑）**、notification、compliance、state_machine、integration |
| 按审核状态筛 / 对接 CRM / 客户信息脱敏 | **state_machine + compliance + integration（三个新席位全中）** + export_import + business_rules + tech_approach + data_scale | frontend_interaction、notification、permissions |

结论：名单跟着输入走（两个方向都能变），常驻席位不再"每轮必到"。

**C6 真模型全路径**（同一个临时库，第一轮那 5 个席位）：

| 步 | 结果 |
|---|---|
| 席位轮 1 → 整合 | 第 **1** 批问卷，5 题（时间筛选口径 / 文件格式 / 行数阈值 / 是否异步 / PII 脱敏） |
| 作答（3 答 2 跳）→ 席位轮 2 → 整合 | 第 **2** 批问卷，5 题（超量策略 / 可接受耗时 / 脱敏规则 / 并发 / 空数据） |
| 作答（2 答 3 跳）→ 席位轮 3 → 整合 | **不再问**：`step=awaiting_confirm`，草案 **38 条**；剩下的题全部降级成 s11 开放问题，理由写着"问卷已满 2 批，不再追问" |

改动前这里会出第 3 批问卷（用户体感"没完没了"）。

**界面复述卡**（浏览器实测，第 2 轮的那份名单）：
顶上"这 7 个是分析席位读完你这段话之后挑的（每条都写了理由）；目录里还有 3 个可以自己加"；
每条显示"命中「状态」「审核」"这类小标签 + 分析者的理由 + "它管什么"；
底部折叠区"目录里还有 3 个席位，想加就加"。

**数字**：后端 338 passed + `ruff` 干净；前端 `tsc` 干净 + 77 passed（新增 `seatNominationSummary`、
`allProposalIds`、复述卡/草案卡改动带来的纯函数用例）。

**下一步（P3 剩余）**：C5（丢弃的 finding 留痕）、C8（答案轮缩编）、C4 的三轮统计、
"继续深挖"入口、跨轮编号跳转、版本历史与 diff、索引层。

### 执行结果（2026-09-24，P4：引用通道 + 整合失败的出口）

计划：`docs/plans/2026-09-24-P4-引用通道与失败出口.md`（7 个任务，6 个已提交，见下）。

**起因**：作者真跑 `p-edc2b350` 的一轮整轮失败，界面报「整合两次都过不了校验：题目的依据
编号不存在：O1」，点"继续/重试"又 502；同时第 2 次席位轮交回的 9 条疑问一条都没法答。
诊断与结论写在 `04-未决.md` C9（含复现方法与概率：4 次重放里 1 次泄漏，模型自己拼出
`seat.business_rules:O1` 这种地址当编号用）。

**改了什么**

1. **词表分离**（Task 1）：`render_reviews` 每条意见标两种身份——
   `（席位条目 seat.x:O2 · 出处编号 E7）`；疑问写"没有出处编号"；判断自己的 E 编号也露出来了。
   两处提示词小标题与硬规矩同步改口，报错文案也指明"`seat.x:O1` 是地址不是编号"。
2. **归因通道**（Task 2）：报文去掉 `asked_by`（模型会瞎认），加 `from_opinions`（席位条目地址，
   **非证据**）；`asked_by` 由装配层算（依据编号所属席位 ∪ 地址解析出的席位）；解析不上的进
   `Questionnaire.dropped` 留痕，投影成 `RoundView.dropped_attributions`，问卷卡上一行灰字。
   **账本语义不动：疑问不是证据，不进账本。**
3. **失败出口**（Task 3）：整合两次仍失败 → 席位疑问原样落成 s11 开放问题（每条配
   `system_default` 出处，注明"整合失败降级"）+ 出草案卡，缺口行写失败原因与未归并条数；
   一条疑问都没有时才判整轮失败。
4. **失败语义与并发**（Task 4）：`Round.fail()` 幂等（失败点只记第一次，不再被并发覆盖成
   `failed`）；坏 `at_step` 明确报错；`RESUME_ROUTES` 补 `failed` 兜底；`RoundRunner` 按 round
   串行化，重入 → `round_busy` 409。
5. **前端**（Task 5）：问卷抽屉显示"哪几条归因没对上号"；`tsc` 干净、77 passed。

**数字**：后端 351 passed + `ruff` 干净；前端 `tsc` 干净 + 77 passed。

**真模型证据（Task 7，已拿到的三条）**

1. 改过的整合提示词 + 作者真数据重放（qwen3.8-flash）：`evidence_refs` 里只有 E 编号
   （引不到就留空），席位条目改走 `from_opinions`（`seat.business_rules:O1`…）——
   以前正是这里被塞进 `evidence_refs` 才判死整轮。
2. 同一份报文过装配层：5 道题留下（上限 5）、归因丢弃 0 条、`asked_by` 来自归因。
3. 整合必失败的降级（作者库副本 + 复现 O1 的 stub）：草案 9 条全落 s11（那 9 条疑问原样），
   缺口行写明原因；再点重试 **0 次模型调用**，不再撞墙。

**还没拿到**：完整 `scripts/smoke.py` 跑到确认写入——`.env` 的 `deepseek-v4-pro-0813` 与
`qwen3.8-flash` 都在席位轮卡住十几分钟没返回，疑点是**网关没设请求超时**
（`LiteLLMGateway.complete` 不传 timeout），provider 一慢这一轮就永远"正在跑"。下轮候选。

### 设计结果（2026-09-24，P5 方案：把项目补扎实）

作者提问"我还差啥功能，感觉设计不扎实"。核对代码后给出三条根因，写成方案
`docs/plans/2026-09-24-P5-方案-把项目补扎实.md`：**能不能交付 / 凭什么信它 / 卡住了怎么办**。
六条拍板全部同意原建议（记录在 `04-未决.md` G），结论已回写进设计文档：

| 线 | 决定了什么 | 落在哪 |
|---|---|---|
| A 交付闭环 | 六种可执行判据（`ReadinessChecker`）+ s8 六个**可寻址**章节槽 + 导出 Markdown（s13 由版本生成、D5 只带编号摘要）+ 缺口进提示词；门禁提示不挡 | `01` §4/§7、`02` §4.7/§4.8/§10 |
| B 信任 | C5 后半（`Round.dropped` 统一留痕）、版本列表/diff/回退（`rollback_to` 已在领域里躺着）、跨轮编号按需查 | `04` C5、`02` §4.6 |
| C 韧性 | 网关超时、放弃这一轮（`Step.ABANDONED`）、模型留样、最小 eval；C8 缩编要先把席位 TOML 的 `sections`/`fields` 补上 | `02` §7/§7.1、`04` C8/E3 |

**下一步**：P5a 已执行完（见下）；接着拆 P5b（信任三件套）与 P5c（韧性 + 缩编）。

### 执行结果（2026-09-24，P5a：交付闭环——就绪度 + s8 章节槽 + 导出）

计划：`docs/plans/2026-09-24-P5a-交付闭环-就绪度与导出.md`（10 个任务，全部完成）。

**改了什么**

1. **s8 六个章节槽进骨架（Task 1、2）**：`SECTION_SLOTS` 放 `shared/sections.py`（与 13 章同处，
   避免 shared→authoring 反向依赖）；`Target.field` 分两个命名空间——有 `feature_slug` 取功能字段，
   没有就取该章槽表（`Target.body("s8", field="performance")`）。手改也能落进槽。
   `BlockView.field_title` 由后端给字段中文名，前端不再抄一份。
2. **就绪度（Task 3）**：`domain/authoring/readiness.py`，六种判据、纯代码、只读 `DocumentView`；
   结构缺失才是 blocker，语义条款一律 warn；同一 (章节, code) 只出一条；s13 不判。
   `DocumentView.feature_gaps` = `Document.gaps()` 的投影（不重写必填规则）。
3. **阻塞型未决（Task 4）**：`Question.blocking` 由规则算（必填功能字段 / 必填章节），
   跳过的阻塞题在 s11 带 `〔未决·阻塞〕` 前缀；老 payload 读回 `False`。
4. **缺口进提示词（Task 5）**：`render_document_state(view, readiness=...)`，三处调用点都换成它；
   提示词里写明"补缺口不等于填满，拿不准就出题"。
5. **投影与端点（Task 6、8）**：`WorkspaceView.readiness`；`GET /readiness`；
   `GET /export/markdown`（中文文件名 RFC 5987 双写）。端点白名单守卫同步更新（现 14 个）。
6. **导出渲染（Task 7）**：`application/deliverable.py` 纯函数——头部盖章就绪度、13 章、
   空章显式、s13 由 `Version[]` 生成、附录 A 只给编号+类型+摘要（D5）、附录 B 偏好区标明
   "不进交付物"、附录 C 未完成项；同一输入两次导出字节一致。
7. **前端（Task 9）**：版本条"就差 N 项"、顶栏导出按钮、章节头列出该章缺口、s8 空槽显示"待补"、
   判"有内容"改成按**非空条目**；写文档后整份工作区重读（否则就绪度停在旧数字）。

**数字**：后端 398 passed + `ruff` 干净；前端 `tsc` 干净 + 85 passed（新增 readiness 7 条、
status 空槽 1 条）。

**本地端到端证据（不依赖模型）**：新建项目 → `GET /readiness` 报"阻塞 8 项 / 提示 0 项"
（空文档不重复吵，s6/s7/s9 的提示要有内容才报）→ `GET /export/markdown` 200，
`Content-Disposition: attachment; filename="deliverable-v1.md"; filename*=UTF-8''订单中台-v1.md`，
正文首行 `# 产品文档 · 订单中台（v1）`、第三行 `> 就绪度：未通过（阻塞 8 项 / 提示 0 项）`，
空章逐条写出"（本章为空：至少 1 条…）"。

**真模型走一遍：没拿到**。两次尝试（`.env` 的 `deepseek-v4-pro-0813`、覆盖成
`qwen3.8-flash`）都卡在第一次模型调用上，8 分钟不返回——根因是网关没有请求超时
（`04-未决.md` E3），provider 一慢这一轮就永远"正在跑"。因此 P5a 的证据是**本地端到端**
（上面那段）：不依赖模型，接口与渲染都对得上。**真模型那一步并进 P5c 第一件事**
（先给 `LiteLLMGateway` 加超时，再回来跑）。

作者库里那一轮（`at_step=failed` 的坏行）现在会明确报错要求重开一轮，不再 500。

### 执行结果（2026-09-22，P1 已完成）

- 代码：`backend/src/pmstudio/domain/`（shared / authoring / provenance / round / services / ports）
  + `backend/tests/`（architecture / domain / support）。
- 验证：`cd backend && python3 -m pytest -q` → **141 passed**；`python3 -m ruff check .` → 无告警。
- 四道守卫全绿：domain 无外部依赖（白名单式，`sqlite3` 也会被拦）、三上下文互不 import、
  全项目只有 `services/committer.py` 持有 `DocumentWriter`、`src/` 下无 fake/mock/stub 标识符。
- 不变量矩阵：22 个码（D1–D6 / E1–E4 / R1–R7 / LB1–LB3 / AG1 / GB1）逐个有正例与反例，
  由 `tests/domain/test_invariant_matrix.py` 扫测试名强制。
- 案例 A 领域走查：`tests/domain/test_case_a_walkthrough.py`（8 条主张 → 4 席位 → 5 题问卷 →
  答题+跳过 → 缩编席位轮 → 草案三组 → 确认写入 v2 → 逐条回溯校验）。
- 执行中发现并修掉的**计划缺陷**（详见最终汇报）：
  ① 守卫实现"允许全部标准库"与它自己的测试矛盾（`sqlite3` 必须被拦）→ 改显式白名单；
  ② `Origin` 与 `Proposal` 的双重 refs 校验互斥 → R5 落在 `Proposal`，GB1 落在 `Entry/WriteInstruction/Claim`；
  ③ `EvidenceKind` 放 provenance 会让 round 反向 import → 移到 `shared/evidence_kind.py`；
  ④ `DraftId` 在 Task 1 类型清单里漏了 → 补进 `shared/ids.py`；
  ⑤ 若干测试里 `authorized=` 写错位置、`len(assignments)` 数错 → 按"测试错就修测试"的原则修正。
- 下一步：写 P2 计划（application 用例 + SQLite 仓储 + LiteLLM 网关 + LangGraph + API/SSE + 前端最小集）。

### 执行结果（2026-09-28，P5b：信任三件套）

计划：`docs/plans/2026-09-24-P5b-信任三件套.md`（12 个任务，一步一提交，全部完成）。

**做了什么**

1. **留痕一条通道（T1–T3、T10）**：`Round.dropped[]`，三个来源——`integrator`（报文新增
   `dropped_opinions[{handle, reason}]`）/ `assembly`（归因对不上号）/ `unaccounted`（装配层核出
   "既没说丢、也没交代落到哪儿"）。每条带 `handle / seat_id / pass_index / reason / source`，
   落库、进 DTO、闭环区每个席位包底下一行；`Questionnaire.dropped` 与 `RoundView.dropped_attributions`
   收口退休（旧库问卷上的 `dropped` 兼容读 → 迁成 `assembly` 条目）。
2. **版本三件（T4–T6、T8、T9）**：`domain/authoring/diff.py`（纯函数、按 `entry_id`）、
   `GET /versions`、`GET /versions/{a}/diff?to=b`、`POST /versions/{n}/rollback`
   （走 `ProposalCommitter.rollback_to`，写路径白名单不变）、前端版本抽屉（列表 / 差异 / 二次确认回退）。
3. **出处查询（T7、T8、T11）**：`GET /projects/{id}/evidence/{eid}` 给原话、来路、链条、`used_by`；
   前端本地索引查不到就查后端 → 有 `used_by` 滚到那条内容（`EntryList` 补 `id="entry-<id>"`），
   只有 `round_id` 就切过去看那一轮（**只读** + 横幅 + 回到最新）。

**验证（三条命令的真实输出）**

- `cd backend && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -o addopts=""`
  → **440 passed**（P5b 之前是 398）；`python3 -m ruff check --no-cache .` → All checks passed。
- `cd frontend && npx tsc --noEmit && npx vitest run` → tsc 干净、**97 passed**（之前 85）。
- 端点白名单：**14 → 18**（`tests/api/test_openapi_contract.py` 同步更新）。
- **真组装冒烟**（真 Settings + 真 SQLite + 真 container，非测试夹具）：列版本 200、
  diff 200、回退 200（新版本号 2）、`?to=99` → 404 `unknown_version`、
  不存在的编号 → 404 `unknown_evidence`；**回退后再读 workspace 仍 200**（这条以前会炸）。

**执行中发现并修掉的真问题（方案里没写，是核验出来的）**

1. **回退会让整个项目打不开**：`Document.rollback_to` 恢复旧快照时不动墓碑，而 `_validate`
   要求"活块 id"与"墓碑 id"不重叠 → "建功能 → 删功能 → 回退到功能还在的那一版"写出一个
   落库没事、下次 `get()` 重建聚合抛 `DuplicateBlockId` 的文档。已修（重算墓碑 + 候选状态先过校验）。
2. **留痕只在"出问卷"那条路上活着**：`Assembly.dropped` 只在 `questionnaire_of(...)` 被消费，
   走草案的一轮里全丢。现在两条路都写。
3. **`EntryList` 的条目没有锚点**：`used_by` 拿到了 entry_id 也滚不过去。
4. **diff 的功能名漂移**：功能名（「名称」字段）是分组标签的一部分，两版之间改名会让同一批条目
   在两侧落到不同组、看着像"全删了又全加了"。修法：两版**共用一张功能名表**（`after` 优先）。

**前端有两处按计划收窄**（都写进设计里了）：`unplacedDrops` 的口径是"挂不到**本趟**席位包上"
（含更早那趟的留痕与地址对不上的），不是"说不清是哪一条"——闭环区只显示本趟席位包，
更早那趟的留痕只能这样摆出来，不许静默消失。

**下一步（P5c，按作者指定顺序）**

1. **网关超时**（第一件事，否则真模型验证做不了）：`PMSTUDIO_MODEL_TIMEOUT`（默认 120 秒）
   + 按 `TRANSIENT_HINTS` 重试。
2. **放弃这一轮**：`Step.ABANDONED` + `task.cancel()`（模型调用在事务外，取消才安全）。
3. **模型留样**（`PMSTUDIO_TRACE_MODEL=1`，可开关）+ **最小 eval**。
4. **C8 答案轮缩编**（要先给席位 TOML 补 `sections` / `fields`，新增 `Round.active_seats`）。
5. 最后**统一真模型验证**（P5a + P5b 的功能一起跑）。

### 执行结果（2026-09-28，P5c：韧性 + 缩编）

计划：`docs/plans/2026-09-28-P5c-韧性-超时放弃留样eval.md`（9 个任务，一步一提交，全部完成）。

**做了什么**

1. **网关超时（T1）**：`PMSTUDIO_MODEL_TIMEOUT`（默认 90 秒，按次算）+ **`num_retries=0`**；
   失败详情带 `timeout_seconds / attempts / cause`。两条离线测试钉住：传参，以及"静默服务端"下
   1 秒超时必须在 3 秒内抛出来（不关内部重试是 ~4 秒）。
2. **放弃这一轮（T2–T4）**：`Step.ABANDONED`（终点、带原因、幂等）+ `Round.abandon()`
   \+ `RoundCanceller` 端口 + `RoundRunner.cancel()`（记当前任务 → `task.cancel()` →
   `asyncio.wait` 等它退出）+ `AbandonRound` 用例 + `POST /rounds/{id}/abandon`（端点 18→19）
   \+ 界面「放弃这一轮」。**先写状态、再取消**；取消异常不吞，前端用标志位吞掉在途请求的报错。
3. **模型留样（T5）**：`model_calls` 表（零迁移）+ `PMSTUDIO_TRACE_MODEL`（默认关）+
   `LiteLLMGateway(recorder=…)`；存提示词**原文**（截断 2 万字符）+ 回复 + sha + usage + 耗时 + round_id。
4. **最小 eval（T6）**：`evals/assertions.py`（七条结构性断言，纯函数）+ `evals/cases/*.json`（8 条）
   \+ `scripts/eval.py`（真模型、不进 CI、直接调用用例不走 HTTP）。
5. **C8 缩编（T7–T8）**：10 个席位 TOML 补 `sections` / `fields`、`Round.active_seats`
   （`pending_seats` 基于它算、`attest` 校验它）、`application/narrowing.py::narrow_seats()`
   （① `asked_by` ∪ ② 映射命中 ∩ 用户编制；命中为空全跑）+ 界面照实说"这一趟只请了谁"。

**验证（本机）**

- `cd backend && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -o addopts=""`
  → **486 passed**（P5c 之前 440）；`ruff check --no-cache .` → All checks passed。
- `cd frontend && npx tsc --noEmit && npx vitest run` → tsc 干净、**97 passed**。
- 端点白名单 **18 → 19**。

**统一真模型验证（作者 .env：`openai/qwen3.7-flash-2026-07-15` + DashScope）**

| 条件 | 结果 |
|---|---|
| 默认 90 秒超时 | **失败**：`integration` 三次尝试全超时（`attempts=3`、`timeout value=90.0, time taken=90.29s`）→ 整轮 502 |
| `PMSTUDIO_MODEL_TIMEOUT=300` | **跑通**：6 条主张 → 3 个席位 → 草案 → 确认写入 v2；**第一次真正落进 s8 的三个章节槽**（performance / availability / security） |
| `scripts/eval.py --case edge-vague` | **7/7 断言通过**（一条用例约 8 分钟；问了两批问卷、题目全跳过） |

**诊断 · 为什么超时（我的活，不是功能）**

依据：`model_calls` 留样（`PMSTUDIO_TRACE_MODEL=1`）+ `ModelCallFailed.details`。两次真跑共 14 次调用：

| purpose | 提示词（字符） | 耗时 | `reasoning_tokens` / 输出 |
|---|---|---|---|
| `claims` | 6 307 | 81.6–84.0s | 4 253–4 880 / 5 626 ≈ 82–87% |
| `seat_review` | 3 085–3 234 | 57.3–84.4s | 3 303–4 579 / 3 766–4 556 ≈ 83–88% |
| `integration` | 10 022–12 544 | **116.9 / 164.4 / 179.4s** | 4 272–7 290 / 6 450–9 104 ≈ 66–80% |

结论：

1. **`integration` 是这个模型上的热点**：它要读文档状态 + 主张 + 各席位意见 + 字段表 + 槽表 +
   JSON Schema，提示词 1 万～1.25 万字符，单次 2–3 分钟——**90 秒对它不成立**。席位轮也在 57–84 秒。
2. **时间几乎全花在"思考"上**：completion 里 66–88% 是 `reasoning_tokens`，实际输出只有一两千 token。
3. **重试只是把同一堵墙撞三次**：一次 90 秒、三次 274 秒后整轮失败；对"提示词太长"这类确定性原因，
   重试没有价值（对网络抖动仍然有价值）。

**建议（配置项，等作者拍板；代码没改）**：① 在这个模型上**关掉思考**（预计快 3–5 倍）；
② `PMSTUDIO_MODEL_TIMEOUT` 提到 240–300 兜底；③ 或换更快的模型/快照。

**真模型当场暴露并修掉的三个问题**（`04-未决.md` G3、learnings L16/L17）

1. 留样把整轮搞失败（litellm 的 `usage` 里是 pydantic 包装对象 → `json.dumps` TypeError）。
2. **章节槽（P5a）对 AI 路径不可达**：报文层要求 field 必配 `feature_slug`，整合者写
   `{section_id: s8, field: compatibility}` 被判非法 → 两次整合失败、整轮降级到 s11。
3. 模型把章节表整行抄进 `section_id`（`"s3 用户与受众"`）→ 502；又把功能字段名当章节字段用
   （`{section_id: s1, field: one_liner}`）→ 提示词补了反例。

**待作者拍板**：默认超时值/是否关思考；"去处写错要不要一条降级而不是整轮失败"（`04` G3 末尾）。

**下一步**：按拍板调参数 → 跑完整 eval（8 条）→ 之后再考虑"从失败的那一次调用续跑"与索引层。
