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

**我自己的一个失误（如实记下）**：eval 第一次跑时把项目建进了**工作库**
（`backend/data/pmstudio.db` 多了 `p-449cdee9 · eval · edge-vague`，停在草案卡）。
原因是 `Settings.from_env()` 里 `PMSTUDIO_DB` 优先于 `default_db`，我却按 `default_db` 选临时库。
**没有写进文档**（eval 不确认草案，只有空项目 + 一轮 + 它的出处）；
脚本已修：eval 现在**覆盖**库路径（`replace(settings, db_path=…)`），并且**拒绝**跑在工作库上，
另加三条离线测试。清理那一行（等你点头再执行）：

```sql
DELETE FROM rounds   WHERE project_id = 'p-449cdee9';
DELETE FROM evidence_ledgers WHERE project_id = 'p-449cdee9';
DELETE FROM document_versions WHERE project_id = 'p-449cdee9';
DELETE FROM documents WHERE project_id = 'p-449cdee9';
```

**下一步**：按拍板调参数 → 跑完整 eval（8 条）→ 之后再考虑"从失败的那一次调用续跑"与索引层。

### 追加（2026-09-28 二轮：①关思考 + 去处写错降级）

作者拍板：先试 ①（关思考），②（抬超时）留作兜底；去处写错改成"只丢那一条"。

**① 关思考：成了，而且很彻底**（同一模型 `qwen3.7-flash-2026-07-15`、同一输入、`timeout=90`）：

| purpose | 开思考 | 关思考 | 提速 |
|---|---|---|---|
| claims | 63.2s（另一次 81.6s） | **13.7s** | ~5× |
| seat_review | 366.9 / 370.6 / 371.1s（另一批 57–84s） | **5.7 / 14.0 / 14.5 / 17.1s** | 25–60× |
| integration | 104.3 / 93.0s（另一批 116.9–179.4s） | **20.3s** | ~5–9× |

- 做法：`PMSTUDIO_MODEL_PARAMS='{"extra_body": {"enable_thinking": false}}'`（配置透传，
  我们自己的 `timeout` / `num_retries` 永远覆盖它——有测试钉着）。
- 效果：**默认 90 秒超时下整轮跑通**（7 主张 → 4 席位 → 草案），整轮模型时间 10+ 分钟 → ~1.5 分钟。
  所以 **② 不需要现在做**（默认仍是 90；真要更慢的供应商，抬到 240–300 是一行配置）。
- 作者的 `backend/.env` 已加上这一行（可随时删）。`.env.example` 里也写清了用法与实测。

**去处写错：只丢那一条**（真模型实测的场景已固化成测试）：

- `assembly._target_or_drop()`：提案 / 题目 / 清单项三处统一——去处不合法 → 记
  `DroppedItem(handle="提案 #3", reason="去处不合法：…", source=assembly)` → 跳过这一条继续。
- 边界没松：**引用不存在的编号仍然整轮失败**（内容没有出处 = 追溯链断了，GB1）；
  报文不是 JSON、提案缺依据也照旧失败。
- 前后端都跟着改了：提示词写清"写错的代价"（宁可落章节级也别硬塞 field）；
  闭环区那行文案补上"去处写错的"这一种。

**⚠️ 二轮又查出一个新问题（没改，等你拍板）：`confirm` 被 E4 拦下**

关思考那一跑走到最后一步失败：`409 invariant:E4 提案 p05 回溯不到用户输入或问卷答案`。
取证（留样库 + 账本）：

- p05/p06 是整合者标成 **`derived`** 的提案（s8 的 observability / security），
  但它们唯一的依据 `E9`/`E10` 在账本里是 **`agent_proposed`**（席位判断但**没给依据**，
  `derived_from=[]`）→ 链条到不了任何用户输入。
- 按 `02` §4.1 的设计，这类内容**应该以"它提议的"出现**（默认不勾选）；
  现在装配层直接采信模型写的 `origin_type=derived`，于是它进了"它替你判断的"那一组，
  用户勾了 → 提交时才撞 409，而且卡片上没有任何提示说明它不可追溯。
- 两个选项：**①（推荐）装配层按账本算 `origin.type`**：refs 回溯不到 user_input / card_answer 的，
  一律降级成 `proposed`（设计里本来就写着，只是没实现；纯代码、可测）；② 保持现状，
  只在草案卡上标"这条不可追溯"，让用户自己决定——但那还是会把 409 留给用户。

### 追加（2026-09-28 三轮：①落地 + E4 修复 + 清库 + 闭环跑到底）

作者拍板："按照你推荐的来；可以删掉"。

1. **来源分组按账本算**（推荐项 ① 落地）：`assembly._origin_type()` 用
   `TraceabilityChecker` 检查——模型自报 `user`/`derived` 但回溯不到 `user_input`/`card_answer` 的，
   一律降级成 `proposed`（默认不勾）；提交端的 `E4` 不变式一字未动。
   测试：装配层 3 条（降级 / 不误伤可追溯的 / `user` 也查）+ 用例级 1 条
   （真模型那一幕：一条可追溯 + 一条不可追溯 → 分组正确，**勾选后写入成功，不再 409**）。
2. **删掉我误建的项目**：备份到 `/tmp/pmstudio-backup-1790577110.db` 后，
   `DELETE FROM rounds/evidence_ledgers/document_versions/documents WHERE project_id='p-449cdee9'`。
   现在库里只剩作者自己的 `p-edc2b350 · 产品文档工作台`（1 版 + 1 轮，原样）。
3. **修 `.env` 的引号**：`PMSTUDIO_MODEL_PARAMS` 的值必须带单引号，否则 `source .env` 会把 `{`
   当命令（`dev.py` 自己的解析器没事）。`.env.example` 已改成带引号的写法并注明原因（learnings L19）。
4. **闭环第一次跑到底**（关思考、默认 90 秒超时、真模型）：
   claims → **4 个席位 × 3 趟**（第 2 趟 4 个、第 3 趟缩编到 3 个）→ 2 批问卷 → 草案
   → **`confirm` 写入成功 v2**；15 次调用共 **235.8 秒**，最慢一次 41.9s（90 秒上限有 2 倍余量）。
   这一轮的 s11 里挂着 3 条〔未决·阻塞〕（问卷已满两批，剩余题目按 C6 显式化）——符合设计。

**至此 P5c 的三条线（韧性 / 留样与 eval / 缩编）都跑通了真模型，且没有已知的阻断问题。**
下一步建议：跑完整 8 条 eval（关思考后估计 15 分钟内），把"提示词没退化"这件事第一次量化。

### 追加（2026-09-28 完整 8 条 eval 第一次跑完：6/8 + 两个失败根因）

命令（关思考、默认 90 秒、留样开）：

```bash
cd backend && set -a && . ./.env && set +a && PMSTUDIO_TRACE_MODEL=1 uv run python scripts/eval.py
```

**结果 6/8**：`case-b-login`、`real-round-orders-export`、`edge-only-preference`、
`edge-one-constraint`、`edge-contradiction`、`edge-vague` 七条断言全过；
红的只有 `case-a-orders` 与 `edge-long`，**都不是断言失败，而是 claims 这一步崩**。

**先修了 harness**（提交 `b680b26`）：一条用例崩会冒到 `main`，8 条在第 1 条就整体退出，
连"哪几条红了"都拿不到 → `run_one()` 兜住异常、记 FAIL、剩下的照跑（含一条离线测试）。

**失败根因（两次都留了样，逐条复算过）**

| 用例 | 现场 | 性质 |
|---|---|---|
| `case-a-orders` | `need` 的 `raw_span = '想做个订单导出的功能...最好能按时间筛选'`：模型用省略号把两段不连续原话拼成一条 | **守卫误杀**：最长连续匹配 10 字，门槛 10.5 字，**差 0.5 字判死整轮**；两段原话在输入里都真实存在 |
| `edge-long`（全跑那次） | `raw_span = '(隐含于上下文，仅提及订单)'`：这是模型写的**说明**，不是原话 | **守卫干得对**（真编造）：原文里最长只匹配到"订单"2 字 |
| `edge-long`（单独重跑那次） | `integration` 供应商 **InternalServerError ×3**，该次留样 `duration_ms=97751 / reply 空` | 供应商抖动，不是超时；见下面诊断 |

频次：全跑 71 次调用里，含省略号的复述只有 **2 处**，且**都恰好差 0.5 字**被误杀；
`case-a` 单独重跑 3 次里 **2 次**产出**逐字相同**的那条省略号复述 → 不是随机噪声，
这个输入上它稳定这么写。

**诊断（我的活，不进产品代码）：那次 `integration` 为什么慢/为什么死**

1. **不是超时**：3 次尝试合计 97.75 秒，减去 `BACKOFF_SECONDS=(1.0, 3.0)` 的 4 秒，
   每次尝试约 **31 秒**就拿到了供应商的 500。90 秒/次的墙钟从未触顶。
2. **墙钟被我们自己的重试圈放大**：`MAX_ATTEMPTS=3`，所以"一次逻辑调用"最坏
   ≈ 3×90 + 4 = 274 秒。这次是 3×31 + 4 = 97.75 秒。看单次慢要看 `model_calls.duration_ms`
   是"这一圈的总和"，不能当"一次调用"读。
3. **不是提示词撑爆**：这一条 `integration` 的 prompt 11328 字；同一轮 8 条用例里
   `integration` 最长 prompt 11707 字也跑过了。
4. **全跑那一轮的时延健康**：71 次调用共 989.99 秒模型时间；`claims` 4.9–25.6s、
   `seat_review` 3.5–27.0s、`integration` 8.3–38.2s，**没有一次超 50 秒**（上限 90）→
   关思考这个口径是站得住的，超时不用抬。

**等拍板的两处（都还没改代码）**

1. **`grounded_span` 认省略号**（推荐）：按 `...` / `…` 切段，**每段各自** grounding，
   全段都真实存在才算过，返回值取最长那一段（保证跳回原文落在真实片段上）。
   这样 `A...B` 不再误杀，"纯编造"照样死。
2. **提示词加硬规则**：`raw_span` 必须是**原文里连续出现**的一段；没有原话就不要写，
   绝对不许把「(隐含于上下文…)」这类说明当原话填。反例写进提示词（真踩过就固化）。
3. 可选（口径变更，要拍板）：`PacketParseError` 是否也走"我们自己那一圈重试一次"。
   现状是 claims/integration 报文不合格 = 整轮 `failed`，用户只能手动重试。

### 追加（2026-09-28 作者拍板：①+② 落地，完整 eval 跑了两轮对比）

拍板原话："按照你推荐的来，记得查清楚修改口径等"。落地三处（口径写进 `02` §11.0.4）：

1. `grounded_span` 认省略号：**明写拼接标记就按段判**（每段各自走同一条判据、全段都要站得住、
   返回最长真实片段）；没写标记仍走整串最长匹配（与改前一字不变）；整串逐字命中优先（不切段）。
2. `analyzer.md` + `ClaimDraft.raw_span` 字段描述：必须**连续**、不许省略号拼段、
   **原话里找不到落点就不要切这条主张**（`（隐含于上下文…）` 这种说明不是原话）。
3. 顺带修的两个同类坑（都是"纯格式走形判死整轮"）：
   席位 id 要连 `seat.` 前缀照抄（真模型写过裸名 `tech_approach`）；
   整合者硬规矩补"同一个去处一批里只出一题"（eval 那条断言**只考不教**）。

**三轮完整 eval 对比（同一套输入、关思考、90s 超时）**

| 轮次 | 通过 | 红的是谁 | 性质 |
|---|---|---|---|
| 第一轮（改前） | 6/8 | `case-a`、`edge-long` | 都是 claims 这一步崩 |
| 第二轮（①+② 后） | 6/8 | `edge-one-constraint`、`edge-long` | 席位裸名 / 复述说明当原话（一次复现） |
| 第三轮（三处提示词都改后） | **7/8** | `edge-long` | 两条**断言**失败（不再是崩） |

- `case-a` 改后 2 次运行（第二轮 + 第三轮）都过；改前 3 次里崩 2 次。
- `edge-one-constraint` 第三轮过（席位前缀 + 同一去重题两条提示词都生效）。

**第三轮 `edge-long` 的两条断言失败（都还没改，等拍板）**

1. `no_duplicate_questions`：同一个去处 `s5/order-export.rules` 问了两遍——
   "手机号脱敏的具体口径" 与 "敏感信息范围界定"。提示词已经写明"同一去处一批里只出一题"，
   这次仍然漏了（这一条是**抖动**，不是必然）。
2. `no_unauthorized_features`：6 条 advisory 全是"这条落在 X，不在本轮去处里
   （**席位替你判断的，确认前请看一眼**）"——落点 s4 / s8.security / s8.performance / s9 / s10 / s12。
   **这条与设计口径直接冲突**：`02` §3①（2026-09-22 定）写着"其余'跑出本轮去处'**不拒绝**，
   而是在草案卡顶部显式标注"，理由就是"静默拒绝会让席位的专业判断整批白干"。
   断言注释里的前提也写错了：`ScopeGuard` **只管**"跑出本轮去处"，不标"未授权的新功能"；
   未授权新功能走的是 `assemble` 的 `pending_features` → 出一道 `new_feature_yes` 决策题
   （用户点头才授权）。**这条断言以前从没被跑到过**：`edge-long` 前两轮都在 claims 就死了。

**我的建议（等拍板）**：把 `no_unauthorized_features` 换成"**跑出本轮去处的条目必须逐条带标注**"
（不再把"跑出本轮去处"当失败），硬约束继续由领域层与决策题守着；`no_duplicate_questions` 那条
给提示词补一个"两件事合并成一题"的真例（L13 的规矩：真踩过就固化进提示词）。
两条都落地的话，第三轮这份报告就是 8/8。

### 追加（2026-09-28 P5d：claims 重装一圈 + 真模型验证）

作者拍板第三项（"你推荐的第三项，给我出完整的实现报告，用 writing-plans，好好设计，然后实现"）。
计划：`docs/plans/2026-09-28-P5d-报文不合格的重装.md`（按 writing-plans 写：Goal/Architecture/
Spec/Global Constraints + T1–T4 的逐步 TDD + 自检）。

**三个提交**

| 提交 | 内容 |
|---|---|
| `e4948fb` | `application/repair.py::repair_note()`（纯函数）+ 复述错误带 `position` |
| `ec40969` | `analyzer.md` 加 `$validation_error` 槽、`analyze(..., validation_error="")`（与整合者同形状） |
| `f8e5a63` | `StartRound.analyze` 重装一圈（`MAX_CLAIMS_ATTEMPTS = 2`） |

**口径**（写进 `02` §7 的调用上限表 + 新增 §11.0.5）：claims 报文不合格 → 带上一次错在哪
再调一次 → 两次都过不了才 `fail_round`（理由"主张两次都过不了校验：…"）；**网关类失败不重装**
（网关自己已经 3 次尝试 + 退避）。纠错指引**不复制模型写错的那串**（只说第几条、哪个字段、要求）。

**单测**：`523 passed` · `ruff` All checks passed · 前端 `tsc` 干净 + `97 passed`。
新增四条行为钉子：重装带指引 / 指引不回喂错串 / 两次失败判死且理由写明 / 网关失败只调一次。

**真模型验证（完整 8 条 eval，第四轮）**：**7/8**，`100` 次模型调用、**一轮都没崩**
（8 轮全部走到草案或问卷）。唯一红的 `case-a-orders` 是 `no_duplicate_questions`
（`s8.availability` 问了两遍）——与 P5d 无关的老问题，见上一节。

**重装真的用上了（一次，而且救回一整轮）**：

```
r-a95e8bdc（就是 edge-long）：claims 调了 2 次；第一次报
  「主张 c1 过不了领域校验：未知章节：s1 背景与问题」
第二次写对 → 那一轮一路走到 awaiting_confirm（出了草案），failure=None。
```

我按留样回放了一次那条坏报文来定性：5 条复述的 `raw_span` **全部能对齐原文**（复述没问题），
席位 id 也全合法——死的只是 `section_id` 照抄了整行标题。这条规矩本来就在提示词里
（`SECTIONS_TEXT` 末行："section_id 只能填上面那个短 id，如 s3；不要带标题、不要带空格"），
模型还是偶尔照抄整行；**这次正是重装兜住的**。

**遗留（都不在 P5d 范围，等拍板）**

1. `no_duplicate_questions`：第四次跑了，第三次复发（`edge-one-constraint` → `edge-long` →
   `case-a-orders`，每次换一条用例）。提示词那条规矩已经加过，实测不够——建议**装配层机械去重**
   （同一去处只留第一道，其余写进 `Round.dropped` 留痕），或承认它是抖动把断言改宽。
2. `no_unauthorized_features` 与 `02` §3① 的口径冲突（上一节详述）。

### 追加（2026-09-28 第五轮：两条断言的判据修正 → **8/8 全绿**）

作者拍板："第一个遗留是啥啊，怎么就把第二道题变成留痕了，难道不是去提问吗，还有这个冲突，
按照你推荐的来"。**第一项我原来的提法是错的**（把第二道题写成留痕 = 把用户该填的题吞了），
修正后的口径与技术改动一起落地：

| 项 | 旧判据 | 新判据 |
|---|---|---|
| `no_duplicate_questions` | 同一个 `target_label` 出现两次就算重复 | `(target_label, 归一化标题)` 相同才算——**同一去处的两件不同事照问** |
| `no_unauthorized_features` | 有 `advisory` 就判失败（与 `02` §3① 冲突） | `outside_scope_items_are_labeled`：卡片标注数 == 顶部提示数、且标注是人话 |

同时**撤掉**我上一轮加错的那条整合者提示词（"同一个去处一批里只出一题"），改成
"同一件事不要问两遍；同一去处的两件不同事各出一题（各有自己的选项与跳过后果）"。
`03` 的验收清单第 2 条也跟着写清（"题目不重复"= 同一件事不重复问）。

**第五轮完整 eval：8/8（第一次满分）** · 104 次模型调用 · 最慢单次 39.6s（上限 90）·
8 轮全部走到草案或问卷、**没有一轮触发 claims 重装**（这次模型干净，重装是安全网不是常态）。

**新口径下被放行的那两条**（旧断言会判死这一轮）：`case-a` 那轮有 2 条"席位替你判断的"提案
（`s8.performance` / `s8.availability`，`origin=derived`）——正是 `02` §3① 说的"不拒绝、
只标注"，新断言确认它们**在卡片与顶部提示里都带了标注**。

第五轮另一件值得记的事：**同一去处多题这次一次都没出现**（8 轮全 0）——不确定是新提示词的作用
还是采样运气，先记下来，下一轮再看。

### 追加（2026-09-28 P6：超限不判死 + 引用/席位容错，已实现待真模型验证）

**来历**：作者让我用真模型把主闭环从零跑四个项目（15 轮 / 250 次调用），
结果**每一份文档都没到"可交付"，而且每一次整轮判死都不是内容错、是形状错**
（报告 `.codex/runs/2026-09-28/报告.md` F2/F3/F4/F11/F13）：
提案 44>30、席位意见 10>8、未采用申报 35>30、编号写 `E07/E08`、提名目录外的 `seat.observability`。

**落地（九个提交，TDD 一任务一提交）**：

| 提交 | 内容 |
|---|---|
| `c9bc2d6` | 上限重定：意见 12 / 提案 60 / 未采用申报 84，并改提示词口径（`seat_review.md`、`LIMITS_TEXT`） |
| `6b246f3` | `parse_packet_trimmed()`：只有"全是 too_long"才截断，别的错照旧抛；严格解析带上 `overflow` 细节 |
| `49c3725` | `repair_note()` 的 `overflow` 分支：说清"你给了几条、上限几条、怎么精简" |
| `1bca336` | `Round.note_dropped()`：席位轮与 claims 阶段也能留痕 |
| `e671791` | 席位侧：`SeatOutcome` + 单席位重装一圈 + 超限截断留痕（`review()` 返回值变了，测试同步迁移） |
| `8b15bba` | 整合侧：`IntegrationOutcome` + 提案/申报超限先重装、再截断 + 缺口行 |
| `f16ead6` | `evidence_refs.normalize_ref()`：`E08`→`E8`、全角、大小写；`candidates_for()` 给相邻候选 |
| `e7299f1` | 三处引用入口接归一化；`unknown_ref_message` 认出"提案编号当出处" |
| `5e639bd` | 目录外的席位提名丢人不丢轮（只有全不认才判死） |

**验证到哪一步**：后端 **551 passed** · `ruff` All checks passed · 工作区只剩预期改动。
**真模型验证（8 条 eval + 一条复现项目）还没跑**——那是下一步。

**口径都写进 `02` §7（调用上限表）与 §11.0.7**（三条口径 + 上限算术 + 为什么不放松 GB1）。

#### P6 真模型验证结果（2026-09-28 当天跑完）

**① 完整 eval（8 条，`/tmp/pmstudio-p6.db`，留样开）**：**6/8**，93 次模型调用。
两个红单跑各 1/1 通过 → **都是抖动**（`case-a-orders` 是断言抖动；`edge-contradiction` 是下面那类新失败）。
**关键判据通过：没有任何一轮因为 `too_long` 整轮失败。**

**② 真实留样回放（改前死、改后过，直接证据）**：

| 回放对象 | 改前 | 改后 |
|---|---|---|
| CRF 第二轮那份 **44 条提案**的真实回复（`/tmp/pmstudio-runs/run.db` 留样 138） | 整轮 502、44 条全丢 | **严格解析直接通过**（44 ≤ 60），无截断 |
| 试点那两次 **35 条 / 33 条 `dropped_opinions`**（留样 211 / 212） | 整轮 502 | **严格解析直接通过**（≤84） |
| 真跑里出现过的零填充编号 `E08` / `E07`（留样 27 / 48 / 234 / 240） | 整轮 502 + 4–6 席位重跑 | `normalize_ref` → `E8` / `E7`，照常进卡片 |

**③ 端到端真跑（新代码 + HTTP，`p-63311985`）**：claims → 开场（6 席位，69.9s）→ 问卷
→ 作答 → 席位轮 → 整合，全部 200；重试一次后继续往下走。`seat_review` 一共 **12 次 = 2 轮 × 6 席位**
（**没有一次重跑**），全程只有 1 次 502。

**④ 新暴露的失败类（不在 P6 范围，记为下一步）**：模型把**整包回成数组**（`model_type`：
`Input should be a valid dictionary or instance of IntegrationPacket`），两次重装都是数组 → 判死。
本次验证里出现两次（eval 的 `edge-contradiction` + 端到端那一轮）。
**建议**：`repair_note` 加一个 `model_type` 分支（"只输出那个对象，不要输出数组/围栏"），
并考虑把 `parse_packet_trimmed` 的容忍面扩到"顶层是数组"这类**可识别**的形状错误——
但**不许猜**（数组到底是 questions 还是 proposals 不能瞎映射）。

### 追加（2026-09-28 P7：整包回成数组 → 形状重建 + 整合重装，已实现且真模型验证通过）

**上面那条"下一步"做完了，而且顺带挖出两个真 bug。**

| 提交 | 内容 |
|---|---|
| `aed3a9d` | `schemas`：顶层数组按**元素签名**重建（`handle`→申报 / `title`→题 / `text`+`reason`→清单 / `evidence_refs`→提案；顺序即判据）；归不了位就判死并带 `packet_shape` 细节。**只给 `IntegrationPacket` 开**（它四个容器都有默认值，补壳不用造字段；claims/席位补壳就得凭空造 `basis`） |
| `49eeab1` | **整合的解析错也重装**：以前 `except ApplicationError` 把 `PacketParseError` 和网关失败一起判死——留样 15 与 502 **同一秒**、留样 16 在 55 秒后（我手动重试才有），证明纠错指引对整合从来没生效过。现在只有 `ModelCallFailed` 立刻失败 |
| `e618025` | `repair_note` 的形状分支（"顶层要对象、允许哪些键"） |
| `2f6d429` | **席位解析错也重装**——P6 只实现了"超限"那一支（我的 T5 测试也只覆盖超限），真跑抓到：某席位把 `evidence_refs` 写成 `null` → 整轮 `席位失败` |
| `73464e0` | **提案编号按"最大编号 + 1"续编**——老 bug：装配层丢掉去处不合法的提案后编号有空洞（p01、p03…），后面按 `len+1` 续编就撞号，`DuplicateProposalId` 把整轮打成 500（真跑里是 `p15` 重复） |

**验证（全部通过）**

| 项 | 结果 |
|---|---|
| 单测 / lint | **564 passed** · `ruff` All checks passed（P6 基线 551） |
| 留样回放 | 端到端留样 15（5 题数组）与 eval 留样 66（4 题数组）→ **都重建成功**（改前是 `model_type` 判死） |
| `edge-contradiction` 单条 | 第一次仍崩（席位 `evidence_refs: null`）→ 补上席权重装后 **1/1 通过** |
| 完整 8 条 eval | **8/8**（`/tmp/pmstudio-p7-full.db`，98 次调用，**0 次失败**） |
| 端到端真跑 | `p-1b369ea7`：17 次调用（claims 1 / 席位 13 / 整合 3）、**0 次 502**、走到草案卡、**46 条提案**落库（>30，旧上限下必死）、导出 v2 7311 字符 |

**结论**：P6 + P7 之后，真跑里出现过的"整轮判死"四类（超限 / 零填充编号 / 目录外席位 / 整包数组）
**都有自愈通道**；本轮验证里唯一还没修的是"席位 `null` 字面量"这类**字段级类型错**——
它现在靠"该席位重装一次"兜（已实测有效），不是判死。

### 追加（2026-09-29 P8 + P9：字段级容错 / 功能候选告警 / 未决队列，已实现且真跑验证）

**P8（字段级容错 + 零功能候选告警）**：四个提交

| 提交 | 内容 |
|---|---|
| `f6025ea` | 字段级归一化：列表字段 `null→[]`、`list[str]` 收字符串→单元素、非 Optional 有默认值的 `null`→默认；黑名单 `op`/`entry_id`/`form`（改写入语义的一律不救） |
| `25c519c` | `repair_note` 的字段错误给人话（字段路径 + 该是什么形状 + 枚举取值） |
| `3fc1cc7` | `$authorized_features` 进提示词：**主张点名的功能已授权，内容必须落到功能字段** |
| `b151187` | 零功能候选 → 草案卡**置顶告警**（DTO 纯投影，不进文档） |

**P8 验证**：单测 592 · `ruff` 全绿 · **完整 eval 8/8**（111 次调用、零失败）·
留样回放（P7 那条 `evidence_refs: null` 的席位报文：改前整席位判死 → 改后 5 条意见全保留）·
叙述式输入真跑：s5 从 **0 条提案变成 12 条**（F1 解掉）。

**P8 暴露的副作用（待作者定，未实现）**：功能内容写多了，`write_scope`（按主张的**精确字段**算）把
同一功能的其它字段也判"不在本轮去处里" → 一轮出现 **20 条 advisory**，把置顶位置刷掉。
两个候选：**A** 写范围按功能级匹配（同 `feature_slug` 的其它字段也算在范围内）；
**B** advisories 按章节/功能折叠计数。详见 `02` §11.0.9。

**P9（未决队列）**：六个提交（`9a6d74b` 队列 → `0281d7d` 溢出进 deferred → `8cca6f7` 下一批优先问/闸门落 s11
→ `76075df` s11 未决项 → carried 题 → `569f72c` 答过机械撤下 → `387ff3e` 未决清单进提示词），
外加 `5831616`（真跑抓到的修：carried 来源按 **`form_id#question_id`** 记——`question_id` 每批都从 `q1` 重排）。

**P9 验证**：

| 项 | 结果 |
|---|---|
| 单测 / lint | **605 passed** · `ruff` All checks passed（P8 基线 592） |
| 跨轮真跑 `p-473420bd` | 第 1 轮跳过两题 → v2 里 **6 条 s11 未决条**；第 2 轮第一批问卷**把最老的 2 条问回来**（带 `from_entry`），第二批问下一对、前两条不重复；草案里 **3 条 `remove`**（模型自己写的"〔已定〕…"）；确认 v3 后**版本 diff 显示这 3 条被删掉** |
| 完整 eval | **7/8**；唯一红是网关慢（整合那步 94s / 139s 撞 90s 墙钟，报 `InternalServerError` / `Timeout`） |
| 超时实验 | 同一条用例加 `PMSTUDIO_MODEL_TIMEOUT=180` 复跑 → **1/1 通过** ⇒ 失败是**墙钟**不是代码 |

**新的运行口径建议（要作者定）**：当前 DashScope 端明显比 9-28 慢（席位实测 108–125s 才算一次成功调用），
`.env` 的 `PMSTUDIO_MODEL_TIMEOUT=90` 已经不够。建议抬到 **180**（改环境变量，不动代码）。

---

## P10 · 跨批去重 · 答案去向 · 矛盾判据 · 留样补真（2026-09-29）

**拍板**：D1–D6 全同意（「都同意」）。计划 `docs/plans/2026-09-29-P10-跨批去重-答案去向-矛盾判据-留样补真.md`。

**根因只有一个，症状六处**：题号原来按**批次**发（每批 `q1…q5`），于是"按题号回查"的六处全指错——
真跑证据：第二批的答案在提示词里挂**第二批的题面**、写**第一批的 E 编号**（另一条的编号根本没出现），
写范围实测 `['s2','s8']`（应是 `['s2','s5']`）。**证据编号本身没问题**（账本按项目单调发号）。

| 提交 | 内容 |
|---|---|
| `ef1e69b` | `.env`/`.env.example` 超时 90 → **180**（实测整合步平均 43s、最慢 120s） |
| `67b07b1` | 写范围**功能级**匹配（D1）：只对功能字段放宽，章节槽不放宽 |
| `7a6915d` | `outside_scope_items_are_labeled` 与置顶告警解耦（P8 埋的假红） |
| `d70ea70` | **题号轮内唯一**（`asked_question_count` 接着发）+ `questionnaire_history` 追加不覆盖（D4/D5） |
| `17b2624` | 六处"按题号回查"全改：题面回历史、编号按 `(轮次, 题号)` 认、撞两条就不显示 |
| `b883ba7` | 跨批去重：本轮问过的题（含跳过）不再问第二遍（D2） |
| `9eb17ad` `3965f3f` | 答案**引用或机械落地**（D3）+ 自查补的崩点（功能字段名不合法只跳过+留话） |
| `dd6b46d` | `conflict` 判据扩到"本轮输入内部互斥"+ 正反例 + eval 断言（**判据没生效**，见下） |
| `885fec2` | 留样**每次尝试**一条（attempt/ok/error + 单次耗时）+ 提示词**头尾保留** + 幂等 `ALTER TABLE` |

**真跑 `p-0b212145`（2 轮 → v2 23k / v3 40k 字符，库 `/tmp/pmstudio-p10.db`）**：

| 项 | 结果 |
|---|---|
| 单测 / lint | **640 passed** · `ruff` All checks passed · 端点白名单 19 个未动 |
| 题号 | 两轮都是 `q1–q5` → **`q6–q10`**（真跑坐实 D4） |
| 答案去向 | 第 1 轮 **4 条**、第 2 轮 **6 条**机械落地（`origin=user` + 自己的 E 编号）；导出的 `用户已定：` 4 / 11 次 |
| remove | 第 2 轮 **4 条 remove** 撤下被问回来的 s11 未决条，各引**那一批**的答案编号（E100/E101/E116/E117） |
| 写范围 | advisories 从 P8 的 20 条洪水 → 12 条章节级 → 第 2 轮 **0 条** |
| 留样 | 42 行、0 失败、0 重试；**20 行提示词超 20000**，头尾都在（结尾是输出 schema） |
| s11 | `removed 4 / added 18`：P9 的"答过就撤下"第二次被验证，但**消化仍慢于产生**（F14 未解） |
| 完整 eval | `edge-contradiction` **1/1 红**：5 题全是 `gap`，没有一道 `conflict` |

**两个真跑发现（等作者拍板，本计划没动）**：

1. **F15 · 同一件事被问两遍**：s11 未决条的文本**没记它本来落在哪一章**，问回来时去处只能是 `s11`；
   模型按内容重出题时给**真正的去处**（`s5/…`）⇒ `question_key` 判成两道题。
   真跑实例：`q1 s11「点检记录驳回重提是否有时间限制？」` vs `q3 s5/inspection-record.rules「点检记录驳回重提的时间限制？」`。
   修法候选：把原去处写进 s11 条目（`〔去处：…〕`）+ `deferred_from_open_items` 解析回来（文本格式要一起改，有测试钉住）。
2. **T8 判据没成**：模型读得懂张力，但一律落成 `gap`（口径未定）或 `decision`（选一个），几乎不写 `conflict`。
   候选：① 放宽成"conflict 或 decision 都算"；② 撤掉断言只留提示词；③ 交给装配层机械判（暂无可靠信号）。

**运行口径**：`.env` 里 `PMSTUDIO_MODEL_TIMEOUT=180` 已经是当前基线；真跑库一律放 `/tmp`，
**不许碰** `backend/data/pmstudio.db`。

---

## P11 · s11 条目带去处 · 答案不蒸发 · `kind` 必填（2026-09-29）

**拍板**：先做 1、2（s11 带去处 + 答案不蒸发；`kind` 必填 + 断言放宽），第 3 条（analyzer 标互斥对子）排下一轮。
计划 `docs/plans/2026-09-29-P11-s11条目带去处-答案不蒸发-conflict落点.md`。

| 提交 | 内容 |
|---|---|
| `d442bcf` | `target_from_label()`：`Target.label()` 的反函数（认不出来返回 None，不猜） |
| `ada346d` | s11 条目尾部带 `〔去处：<label>〕`；`parse_open_item` 改 `OpenItem` dataclass（老格式 `target_label=None`） |
| `b05e7ec` | carried 题回到条目里写的去处（认不出回落 s11）+ 整合者的未决清单带上去处 |
| `dc1dc7e` | 答案"落不落地"的判据从**来源**改成**去处**：carried 题的答案落回原章节 |
| `27ee3d4` | `QuestionDraft.kind` 必填（默认值不再吞掉判断）+ 重装指引给三种取值与判据 |
| `74c9cd9` | eval 断言放宽成 `contradiction_is_labelled`（conflict 或 decision） |
| `4f13367` | **真跑抓到的洞**：`remove` 不算"引用了答案"（它只是记账） |

**真跑验证**（`.codex/runs/2026-09-29/P11验证.md`，库 `/tmp/pmstudio-p11.db` = P10 库副本）：

| 项 | 结果 |
|---|---|
| 单测 / lint | **669 passed** · `ruff` All checks passed · 端点 19 个未动 |
| s11 带去处 | 新产生的未决条逐条带 `〔去处：s5/…〕`；导出的 v3 里出现 11 次；**老格式条目照旧回落 s11** |
| carried 回原章节 | `q1 target=s5/device-inspection.exceptions`、`q2 target=s8.cost`（P10 时只能是 s11） |
| 答案进正文 | E78 → `s8.cost`、E95 → `s5/device-inspection.exceptions`（P10 那四条答案一字未进文档）；另有 1 条纯机械落地（E79） |
| `kind` 必填 | **模型第一次写出 `kind=conflict`**（"点检记录 E7 与 E8 冲突，如何裁决？"） |
| eval | `edge-contradiction` **仍红**：5 题全 gap——定性为"用例输入是**张力**（100 万行 + 3 秒）不是**逻辑互斥**"；两个候选等拍板（换用例输入 / 撤掉这条 extra 断言） |
| 残留复现 | **同去处、措辞近义**的两道题仍会各出一次（"…保存三年后的处置策略？" vs "…在三年后如何处置？"）——去重按归一问法撞不上，计划 §6 挂着 |

**运行口径**：真跑库一律 `/tmp`（本轮用 P10 库的**副本** `/tmp/pmstudio-p11.db`），**不许碰**
`backend/data/pmstudio.db`；`.env` 的 `PMSTUDIO_MODEL_TIMEOUT=180` 仍是当前基线。

## P12 · 未决队列两端 · 卡片可信度 · 矛盾的机械通道（2026-09-29）

**拍板**：09-28 报告里还没修完的 F5/F8/F10/F12/F14 + P11 留的第 3 条（analyzer 标互斥对子 +
装配层机械盖章），T1–T9 一次做完。计划
`docs/plans/2026-09-29-P12-遗留清单-未决队列与卡片可信度-矛盾的机械通道.md`（含**修订 v2**：
交作者前对着代码核验，改了 8 处——题序口径、收口位置与留痕、`dry_run` 落点、清单项不跨轮、
互斥对子改用序号）。

| 提交 | 内容 |
|---|---|
| `c21ef9d` | 计划修订 v2（核验 8 处） |
| `4a8923b` | T1 引了用户主张的题排前（F12） |
| `801ace4` | T2 同去处让位（**只认字段级去处**；章节级会让整章新题熄火，既有用例当场抓到） |
| `0ce930f` | T3 每批问回 3 条未决项（F14；不设清零目标） |
| `754f0b5` | T4 草案卡带补齐度 `s5 已填 x/6 …`（F10） |
| `c9aed7e` | T5 清单项改成轮次累积 + `$previous_list_items`（F8） |
| `cc0e49d` | T6 未申报意见的留痕带上它说了什么（F5） |
| `7b91cc9` | T7 claims 报文加 `contradictions`（**序号**不是 id）+ 落库 + 渲染给整合者 |
| `e3f55ad` | T8 覆盖互斥两侧的题由装配层盖章 `kind=conflict` |

**真跑验证**（`.codex/runs/2026-09-29/P12验证.md`，库 `/tmp/pmstudio-p12-eval06.db`）：

| 项 | 结果 |
|---|---|
| 单测 / lint | **699 passed** · `ruff` All checks passed · 端点 19 个未动 |
| eval | `edge-contradiction` 换**真互斥**输入后 **1/1 PASS**（含 `contradiction_is_labelled`）——P11 那条红用例收口 |
| 留样 | claims 报出 `contradictions: [{claim_a:1, claim_b:3, why:"…两者立场互斥"}]`；整合者这次**自己也写了 `conflict`**（`$contradictions` 槽位 + 判据话术起作用），而 E1+E3 那道题同时会被装配层盖章（两条通道同向） |
| 落库 | `rounds.payload` 里 `contradictions` / `list_items` 两把键都在，值形状正确 |

**执行中改过的两处测试**（都是夹具把旧口径写死，不是行为错）：
1. `test_open_items_answered_in_different_batches_are_all_retired`：预置 3 条未决项、
   默认每批吃 2 条；配额变 3 后第一批就全问完了，"跨批"无从验 → 夹具加第 4 条（断言从 3 条改 4 条）。
2. `test_claims_packet_array_is_not_guessed`：claims 报文的 `expected_keys` 从 3 个变 4 个
   （新增 `contradictions`）——这条断言本来就是钉报文形状的。

**运行口径**：真跑库一律 `/tmp`（本轮 `/tmp/pmstudio-p12-eval06.db`），**不许碰**
`backend/data/pmstudio.db`；`PMSTUDIO_MODEL_TIMEOUT=180` 仍是当前基线。

## P13 · 续跑口径 · 清单项的去向 · 未决队列先吃堵交付的（2026-09-29）

**拍板**：包 A「闭环自己能收的尾」——E3 剩余 + P12 留的"清单项去向 / s11 消化"两条；
C4（跨轮统计）与 C6（继续深挖入口）**留 P14**（核验：前者要新查询、后者要新端点 + 前端）。
计划 `docs/plans/2026-09-29-P13-续跑口径-清单项去向-未决队列优先级.md`。

| 提交 | 内容 |
|---|---|
| `047a746` | 计划（待拍板） |
| `168251a` | T1 `Round.retry_hint`：失败原因留到重试的第一次尝试（集成路径） |
| `8a95a52` | T2 claims 路径同样带上；**没有 hint 就不开事务** |
| `9d02392` | T3 走真图的钉子：整合失败后重试**不重跑席位** |
| `c577221` | T4 `RoundView.failure_at_step` + `retry_from` |
| `8504db8` | T5 没处理的清单项在出草案时落 s11（`open_item_proposal` 抽出共用格式） |
| `5e90639` | T6 carried 先挑阻塞未决项，其次最老 |
| `cd12db1` | T7 同一 `(去处, 归一化标题)` 不落第二遍 |

**核验（推翻了 09-28 报告的一条旧结论）**：报告 F2"重试是整轮重来、会重跑 4 个席位"
**已经不是事实**——`RunSeatRound` 只跑 `pending_seats`；P13 补了**走真图**的用例
（`test_retry_after_a_failed_integration_does_not_re_run_the_seats`）钉住它。
E3 真正缺的是"重试带上次报错"，T1/T2 收口。

**口径**：`retry_hint` 只记**报文类**失败、**一次性**、网关类不递；`failure.reason` 给我们看、
`retry_hint` 给模型看；清单项只在**出草案**那一刻落 s11 且**不标阻塞**；carried = 阻塞优先、其次最老；
判重键 `(去处, 归一化标题)` 只跟**当前活跃**条目比。

**执行中被既有用例挡回来两次**（改的都是实现/夹具，不是放宽断言）：
1. T2 一开始无条件 `with self._uow:` → `test_start_round_records_claims_with_eids_in_two_transactions`
   与 `test_run_seat_round_attests_and_numbers_judgments_only` 的 `uow.commits` 计数当场抓到
   （"不许为只读开事务"）→ 改成**只有真的取到 hint 才开**。
2. T6 第一版把局部变量命名成 `parsed`，与循环里的 `parse_open_item` 结果撞名 → 13 条用例
   报 `'OpenItem' object has no attribute 'append'` → 改名 `collected`。

**运行口径**：真跑库一律 `/tmp`，**不许碰** `backend/data/pmstudio.db`；
`PMSTUDIO_MODEL_TIMEOUT=180` 仍是当前基线。

## P14 · 包 A 剩余：C4 跨轮统计 · C6 继续深挖（2026-09-29）

**拍板**：包 A 剩下的两条（C4 / C6）；包 B（B1–B4 + 界-2）排在 P15。
计划 `docs/plans/2026-09-29-P14-剩余包A与包B-两批.md`。

| 提交 | 内容 |
|---|---|
| `f708a1f` | 计划（待拍板） |
| `a699b03` | T1 确认时记下"它提议的"给/收（`Round.confirm(..., proposed_offered, proposed_accepted)`） |
| `35d46ec` | T2 `WorkspaceIndex.recent_rounds`（只读投影，不建表） |
| `b996661` | T3 连续三轮没采用就在草案卡上说一句（纯函数 + 存事实 + `advisories`） |
| `8892cb5` | T4 `Round.dig()`（合法转移 + 额度 + 队列留着） |
| `69dcdd2` | T5 `dig` 命令 + `POST /rounds/{id}/dig`（**端点 19→20**） |
| `2a5b413` | T6 前端按钮 + `DraftDrawer.test.tsx`（组件测试） |

**执行中改掉的设计（测试当场挡回来的）**：计划里写的"dig 新开一次席位轮"**是错的**——
常规两批流程已经用满 3 趟席位轮（开场 1 + 每批答案后 1），dig 会被 `SeatRoundLimitReached`
直接拒掉，功能等于没有。改成：

1. dig **不**新开席位轮（`pending_seats` 已空 → 席位零重跑），只花**一次整合调用**；
2. 但必须**同步**放宽"批后那一趟"席位轮额度（`extra_seat_rounds`），否则用户答完这一批
   走不动；
3. `_stop_asking_reason` 的席位轮闸门也要读同一个数（`Round.seat_round_limit`）——
   否则 dig 之后整合那边先响，照样降级成 s11。**这条是真的踩到了**：第一次真跑
   dig 之后又出一份草案，gaps 里写着"已到席位轮上限，不再追问"。

**核验**：单测 **741 passed** · `ruff` 全绿 · 前端 `vitest 99 passed` + `tsc` 无输出 ·
端点 **20** 个（`EXPECTED_PATHS` 同步）。

## P15 · 包 B：卡片与文档可信度 + 包 C「整块能力」设计（2026-09-29）

**拍板**：作者「都按照你推荐的来」。方案
`docs/plans/2026-09-30-P15-剩余核对-包B-包C整块能力.md`（§0 逐条核对 §1 包 B 计划 §2 包 C 设计）。
**包 C（索引层 / 工具层）本轮只出设计，不开工。**

| 提交 | 内容 |
|---|---|
| `e688550` | 方案文档（待拍板） |
| `3f75a12` | T1 `GET /schema`——13 章说明（`SectionSpec.purpose`）+ 功能字段表 + 三处上限（**端点 20→21**） |
| `e42377a` | T2 前端改读 `/schema`：字段中文名 / 席位轮上限 / 章节头说明；抄的几份降级为离线回落 |
| `1b51132` | T3 `RoundView.answer_sets`（已答/跳过/答了什么）+ 抽屉显示 |
| `8236cb6` | T4 手改能给 `{feature_slug, field}`，字段块不存在由 domain 建（F8-①） |
| `e42170b` | T5 复述卡落到功能字段（F7）+ 前端"补一个还没写的字段" |
| `162ade5` | T6 `QuestionnaireDrawer.test.tsx` + `DraftDrawer.test.tsx` 补两条（F11） |
| `71d12da` | T7 SSE 建流首帧 `: ready`（界-2） |

**执行中改掉的设计 / 发现的新东西**（都写进了 `docs/plans/...-P15-...md` 与本文件）：

1. **`MAX_OPEN_ITEMS_PER_BATCH` 从 `use_cases.py` 搬进 `domain/round/questionnaire.py`**：
   `/schema` 要读它，但 `dto` 不能 import `use_cases`（`use_cases` 已经 import `dto`，会成环）。
   与 `MAX_QUESTIONNAIRES` 放在一起（同一类上限、同一个真相源，只是搬家不是复制）。
2. **前端抄的常量有**三**份不是两份**：计划只数到 `labels.ts` 与 `progress.ts`，
   实现时发现 `LoopPane.tsx` 还有一份 `MAX_SEAT_ROUNDS = 3`，一并消掉。
3. **改了 4 条既有前端/后端测试的断言**（都是"契约本身变了"，不是迁就实现）：
   `initialClaimEdits` / `buildClaimEdits` 从"去处只到章节级"改成"完整 label"（F7）；
   `test_event_stream_formats_named_sse_frames` 的 `chunks[0]` → `chunks[1]`、
   `test_event_stream_sends_heartbeat_when_idle` 补上首帧（界-2）。

**核验**：见 `.codex/runs/2026-09-29/P15验证.md`。

## P16 · 包 C-1 索引层：轮次列表 · 上一轮主张 · 全局搜索（2026-09-30）

**拍板**：作者「都按照你推荐的来」——包 C 先做 **C-1 索引层**（C-2 工具层留后）。
计划 `docs/plans/2026-09-30-P16-索引层.md`（三个任务：C-1a 轮次列表 / C-1b 上一轮主张 / C-1c 全局搜索）。

| 提交 | 内容 |
|---|---|
| `4826c15` | C-1a `WorkspaceIndex.list_rounds` + `ListRounds` + `GET /projects/{id}/rounds`（与 `POST` 共用路径 ⇒ 端点**还是 21**）+ 闭环区 `RoundHistory` |
| `7cc51b8` | C-1b `RoundHeadView.claim_headlines`（头 3 条主张摘要） |
| `025f5cb` | C-1c `dto.search_hits` + `WorkspaceIndex.recent_opinions` + `SearchProject` + `GET /projects/{id}/search`（**端点 21→22**）+ 顶栏 `SearchBox` |

> 更正：`4826c15` / `025f5cb` 两条提交信息里写的"21→22""22→23"是错的——`GET` 与
> `POST /projects/{id}/rounds` 在 `EXPECTED_PATHS`（集合）里占同一条，所以这一批**净增 1 条**：
> 21 → **22**。

**三条只读口径**（回写进 `02` §11.0.27）：① 索引层**不是第二份真相源**——不建表、不缓存、现查现算，
跨轮的真相源仍是文档与轮次；② **空查询返回空**，绝不"不填就是全量"；到 50 条 `truncated=True`，
界面写"只显示前 N 条"；③ `json.loads` 坏行**跳过不抛**、老 payload 缺键回落。

**真数据核验**（`/tmp/pmstudio-eval.db` 8 个项目 + `/tmp/pmstudio-p11.db` 老库）：
轮次列表给出输入摘要 / 主张数 / 问卷数 / 写入版本 / 头三条主张；搜索四类都能命中（`entry` / `feature` /
`evidence` / `opinion`）、「导出」在长输入项目上 50 条并标 `truncated=True`、空白查询 0 条；
老库（P10/P11 写的 payload）读回来正常。

**核验**：见 `.codex/runs/2026-09-30/P16验证.md`。

## P17 · 包 C-2a · 工具层上半：材料信息包 + 上传（2026-09-30，**进行中**）

**拍板**：作者「都按照你推荐的来」+「不要假实现」，并按我在 §11 的两条建议执行：
**原始字节进 SQLite**（换解析器要能重解析）、**块级编号且只给带进提示词的块发号**。
方案（v3，含审阅 16 条）`docs/plans/2026-09-30-P17-工具层.md`。

**已完成（C-2a 后台全通）**：

| 提交 | 内容 |
|---|---|
| `9b5a7c9` | T1 材料信息包 + `LocalMaterialParser`（UTF-8 / pdfminer / bs4+lxml，切块带锚点） |
| `9ef8ac4` | T2 存储：`materials`（含原始字节）+ `material_chunks`，按 `(project, sha256)` 去重，列表只回摘要 |
| `24c8a90` | T4 `select_chunks`（唯一决策点）+ `render_materials` / `render_web_results`（预算截断照实说） |
| `d6dec71` | T3 材料进本轮：选块 → 只给带进去的块发号 → `Round.materials` → 三处提示词 `$external_material` 槽 |
| `2dcfa23` | T5 上传/列出端点（multipart 真解析，**端点 22→23**）+ 用例上限与去重 |
| `250c426` | 真跑抓到的修复：坏 PDF/网页报 `UnsupportedMaterial`(422)，不许漏成 500 |

**真核验**（`/tmp/p17_material_drive.py`，真 SQLite + 真解析器 + 真 multipart）：
上传 `需求.md`（local-text，2 块）与 `spec.pdf`（pdfminer，1 块）→ 列表只回摘要 →
起轮带这两份 → 真库 payload 里 `materials = [(m-…9b2d#c00, E1), (…#c01, E2), (m-…4ed1#c00, E3)]`，
账本里三条 `external`，`external_ref` 分别是 `attachment:需求.md#c00` / `#c01` / `attachment:spec.pdf#c00`，
原文片段就是真抽出来的文字（PDF 那份抽到 `Hello export`）。

**本批改过的既有测试（3 条，都是契约变更不是迁就实现）**：
① `test_prompt_library_renders_slots_and_reports_missing`（analyzer 多一个槽，它逐个列出槽名）；
② `test_round_command_holds_no_business_state`（命令白名单加 `material_ids`——它与 `user_input`/`seats` 同类）；
③ P16 那批同类：见该节。

**还没做**：T6 前端（输入框上传、材料卡）、`RoundView.materials` 投影（UI 契约，T6 的前置）、
C-2b 联网（T7 目录与配置 → T8 Tavily → T9 分析者提名+复述卡 → T10 执行与落出处 → T11 真跑）。
Tavily 密钥**已验证可用**（HTTP 200，单条 content ≈ 1.1k 字符），因此把检索单条截到 300 字。

## P17 续 · 证据链补洞 + T6 前端 + C-2b 上半（2026-09-30，**进行中**）

| 提交 | 内容 |
|---|---|
| `f4eaf97` | 证据链补洞：`EvidenceLookupView.external_ref` + 导出附录 A 带外部指称（**不新建机制**，只是把 `external` 这一 kind 的两个显示口补齐） |
| `94d057d` | `RoundView.materials`（本轮带进来的块 + 编号 + 锚点）——前端的材料卡契约 |
| `f4cc98d` | **T6 前端**：输入框带材料（`MaterialPicker`，真上传）→ `buildStart` 带 `material_ids` → 复述卡/闭环区显示"本轮材料"（可点编号回原文） |
| `e150464` | **T7+T8**：工具目录（TOML + `requires` 过滤：没密钥就不出现）+ Tavily 真客户端（每条结果一个 `ParsedMaterial`，密钥不进结果） |

**设计决定（作者要求"好好想想"）**已写进 `docs/plans/2026-09-30-P17-工具层.md` §13：

- **事实来源三层阶梯**：本地事实（文档 + 已有材料，零成本）→ 用户事实（问卷，用户是权威）
  → 外部事实（联网，只有前两层拿不到、且"这是查得到的事实"才走）；
- **联网两道闸门**：分析者提名 0–3 条检索词（带 why）→ 用户在复述卡勾选；**没勾就没有网络调用**；
- **不许联网的清单**（写进提示词判据）：用户已说过的 / 文档材料里已有的 / **产品取舍与偏好** /
  想让系统替你拍板的事；
- **检索 vs 联网**：检索（`MaterialRetriever`，本地）v1 只服务 F6 搜索；"按主张自动取块"留 C-2c
  （`LIKE` 对整句的命中率还没量过）；联网只在复述卡那一次，"草案卡再查一批"也留 C-2c。

**执行时定的一处**：`WebSearchGateway` 返回 `ParsedMaterial`（草稿），不是 `MaterialPacket`——
盖章只有存储层能做，网关不知道项目是谁（与 `MaterialParser` 同一形状）。

**还没做（C-2b 下半）**：T9（`ClaimsPacket.web_queries` + `Round.web_queries/chosen` + 复述卡勾选）→
T10（`SubmitOpening` 里事务外调 Tavily → 落块级 `external` 出处 → 与附件同一句渲染）→
T11（拿真密钥真跑一轮，看导出里的"外部材料"编号）。
