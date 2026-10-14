# 检查点 · 2026-10-10（**最新状态在下面这块；再往下是历史流水，当档案看**）

> 用途：新会话或换人接手时，读这一份就能知道"做到哪、为什么这么做、下一步是什么"。
> 更新时机：长暂停前、切分支前、交给别的 agent 前。
>
> **读法**：先读下面「当前状态」；历史流水（P1–P38、M1–M15、各次执行结果）保留作档案，
> 里面的"下一步/剩下来的活"**全部过期**，以当前状态为准。

## 当前状态（2026-10-14，M18 已交付）

**分支** `new-branch` · 工作区干净 · 后端 `uv run pytest` **2075 passed** ·
`ruff` 全绿 · `check_api_parity` **48/48** · 前端 `tsc` 干净 / `vitest` **440 passed**。
（系统 Python 缺 `bs4` 会漏采 `tests/api|e2e`——真全量必须用 `.venv`；无头 CDP 扩不出键盘选段。）

**新增 4 个提交**（提交信息即档案，含完整核验）：

| 提交 | 内容 | 核验 |
|---|---|---|
| `41586e2` | 右列 Codex 化 + 轻量化（去链子/状态行素化/答案进流/入口去重） | tsc+vitest/parity/三档真屏 |
| `5649344` | M16 引用归因：幻觉编号最后尝试摘掉降级（席位/提问/讨论说/讨论卡四处）+ known 补全 + `$allowed_eids` + 复述卡取消已加席位 | pytest 2058→2075/ruff/parity；**真跑工作库失败轮 r-7ac1912a 重试跑过**（E12 摘掉+留痕） |
| `0039d52` | M17 入口收口：文档侧只留选区浮条（`SelectionBar` 双动作、全设备）、调研入口收进讨论区、超长中央截断+明示 | tsc+vitest 426/parity；真屏 B1–B7 |
| `8749f0a` | **M18 讨论区重做为自主多角色 panel**：开跑配置（主题+角色多选1–4+轮数1–5）→ 前端循环 `run-round`（轮内并行发言+每轮综合）→ 副栏按轮分组+共识/分歧/未决；停止/再跑/插一句/拉人；失败保留已落 turns、无综合，重试补剩余 | pytest 2075/ruff/parity 48/48；tsc+vitest 440；**真跑隔离跑道 3角色×2轮跑满停**（真模型内容+2综合+插一句+3成卡，C1 带真 E2/E5） |

**设计纪律变更（别踩回去）**：
- `02 §11.0.65`：编号不认识只在最后一次尝试降级（摘+留痕），结构错照旧判死；`known` = 提示词看得见的编号（opinions_so_far/记忆/观察已并入）。
- **M18（`02 §11.0.66`）**：终止 = 跑满前端定的 N 轮（默认 3，上限 5），**不要黑箱收敛判断**；失败保留已落库 turns（自包含合法），不用整轮原子丢弃；取消不写状态（线程还是 open）；旧 1:1 线程 = 单成员 panel，无迁移。
- D2 改写（`02 §11.0.51` + `06`）：条目行不再挂讨论/调研；选区浮条全设备可用。
- entry scope 真实语义 = 全文+焦点（不窄化）；`text` cap 500 中央截断+明示，不禁用。

**下一步（按序，需拍板才动手）**：
1. ④「跳过该席位」（M16 拆出：动 domain + 新端点）；
2. `04-未决.md` §C/E 与 `07` §7.11 后续（清单会过期，以文件为准）。

**跑道**：8100/5273 工作库；8101/5274 隔离跑道（`/tmp/uxrun.db`）。真跑默认隔离跑道；
动工作库真轮（重试 r-7ac1912a）是用户特批的例外。`PYTHONDONTWRITEBYTECODE=1` + `-p no:cacheprovider`
才不留 `__pycache__`/缓存。

---

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

## 8. 下一步（最佳动作）· **已过期，看顶部「当前状态」**

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

## P17b · C-2b 下半：联网检索闭环（2026-10-01，**已交付**）

| 提交 | 内容 |
|---|---|
| `49d544d` | **T9**：分析者提名 0–3 条检索词（`web_queries`，带 why；超限/没 why **丢那条+留痕**不重跑）+ `WebQuery` 值对象 + `Round.web_queries / web_queries_chosen`（payload 两把键，老行回落）+ `RoundView.web_queries / web_search_available` + `OpeningRequest.web_queries` + 复述卡那一栏（**默认不勾**） |
| `f63ea93` | **T10**：`SubmitOpening` 事务外查 Tavily（`limit` 来自 TOML）→ URL 去重 → `select_chunks(cost=web_block_cost)` → 一个事务落勾选+逐块 `external` 出处+`Round.materials`；失败/空结果/预算挤掉 → `Round.dropped(source=tool)`；`external_material_text` 按 origin 分两段；前端"工具这一趟"单列一段 |

**真跑（T11）**：`/tmp/p17b_web_drive.py`（真模型 deepseek-v4-flash + 真 Tavily）
→ 5 条结果落成 E4–E8（ref 是 URL）、席位与整合者提示词里都带上了、导出附录 A 有网址、**留样 0 次密钥泄漏**；
坏 key 那一跑 401 → 留痕"检索失败：联网搜索失败：HTTP 401"，轮次照跑到 `awaiting_answers`（**不判死**）。
报告：`.codex/runs/2026-10-01/P17验证-C2b.md`。

**本批钉死的两条口径**（`02` §11.0.29）：

1. **选块与渲染同一把尺子**：`web_block_cost` = 标题 + URL + 摘要 + `WEB_EID_RESERVE`，
   保证"编号 == 带进提示词的块"在联网这条路上也成立（两把尺子会出现"发了号却没进提示词"的编号）；
2. **先查后写**：工具调用在事务外，写只有一个事务——先写的话崩在中间时重试会直接进席位轮，
   用户授权的检索被静默跳过。

**执行中抓到的真 bug**：两条查询搜到同一个网址时，包去重了但**账本里写了两条指向同一块的出处**
（单测 `test_the_same_url_twice_is_one_material` 抓到）；修法是发号前按 `(material_id, chunk_id)` 再收一次。

**改过的既有测试（都是契约变更）**：`test_graph_state.py`（命令多一个字段）、
`test_packet_overflow.py`（claims 报文 expected_keys 多一条）、`payload.test.ts`（起轮体多一把 `web_queries`）。

**下一步**：C-2c（`web_fetch` 抓单页 / docx-xlsx / **docling 重解析** / 按主张取块 / 附件删除），
以及"材料列表里区分检索来的"这种显示口的小补丁。

## P18 · C-2c-1：材料找得到（2026-10-01，**已交付**）

| 提交 | 内容 |
|---|---|
| `7f66bb4` | `MaterialRetriever` 端口 + `SqliteLikeRetriever`（`LIKE ... ESCAPE '\'`、±40 字命中窗口、排序=次数→位置→块长、`score` 与顺序同向）；`MaterialHit` 补 `title`（**死字段终于有生产者**） |
| `f576d23` | F6 第五类来源=材料：`search_hits(materials=…)`、`kind="material"`、`ref=chunk_id`；`SearchProject` 注入只读端口（空查询不问检索器）；前端 `KIND_TEXT`/`pickSearchHit`/`jumpToChunk`/`data-chunk`/`materialHitTarget` |
| `720d443` | 材料列表标来源（上传 / 联网检索 / 抓来的网页；认不出照实显示原值） |

**真跑**：`/tmp/p18_search_drive.py`（真 multipart → 真 SQLite → 真 LIKE，**不调模型**）：
搜「异步队列」「只给管理员」「5 万行」各命中 1 条（带锚点"第 1–4 行"与块 id）；
搜不到的词、空查询、`5 万%` 都是 0 条。报告 `.codex/runs/2026-10-01/P18验证.md`。

**口径**（`02` §11.0.30）：块级命中；空查询回空且**不进检索器**；`%`/`_`/`\` 按字面（`ESCAPE '\'`）；
只在本项目里找；`score` 的消费者就是 F6 的展示顺序（**不重排**）。
**不加端点（仍 23）、不建表、不加索引**；换 FTS5/向量只改 `container.py` 一行。

**执行中改过 1 条自己刚写的测试**（测试写错：把 tuple 当 list 断言），已在验证报告里写明。

**下一步**：C-2c-2（删除 + 重解析，共用"编号发出即冻结"口径）→ 一次**全量真跑审计**
（回答"现在能不能交付"）→ C-2c-3 按主张取块 → C-2c-4 web_fetch/docling。

## P19 · C-2c-2：材料的删除与重解析（编号发出即冻结）（2026-10-01，**已交付**）

| 提交 | 内容 |
|---|---|
| `89ca228` | T1：`WorkspaceIndex.material_references()`（扫 `rounds.payload.materials[]`，不限轮数、坏行跳过）+ `MaterialSummaryView.eids` + 共享 `list_material_views()` |
| `873d156` | T2：`UnknownMaterial`(404) / `MaterialFrozen`(409)、`MaterialStore.delete` 补进协议、`DeleteMaterial` 用例、`DELETE /attachments/{id}` → 端点 **24** |
| `d1aa48e` | T3：`MaterialStore.replace_chunks`（就地换块）、`ReparseMaterial`（没引用→就地；被引用→变体键另出一份；联网材料→422）、`POST …/reparse` → 端点 **25** |
| `3ecc042` | T4：材料库面板 `MaterialLibrary`（带进这一轮 / 重解析 / 删掉）+ 底部入口 + 库状态整体替换 |

**口径**（`02` §11.0.31）：一份材料只要有**一块**发过编号就**冻结**——不能删（409 列出编号）、
不能就地重解析（会改指编号）。没被用过的可就地重读（同一个 id、原始字节不动）。
被引用过的重解析**另出一份**（变体键 `sha256("reparse:{旧id}|{parser}@{version}")`，幂等），
旧的留着当冻结记录。联网来的材料不重解析（是网页快照）。

**真跑**：`/tmp/p19_material_drive.py`（真 multipart + 真 SQLite + 真端点，模型故意配假以省时间）：
上传两份 → 起轮引用"甲"（分析失败但引用落库）→ 删"乙" 200 / 删"甲" 409 带 `E1、E2` →
重解析"甲" 出 2 份、再点一次仍是 2 份。报告 `.codex/runs/2026-10-01/P19验证.md`。

**执行中改过 3 条自己刚写的测试**（都是测试写错）：跨项目删除那条的上传前置、
bytes 字面量非 ASCII、前端 `getByText(/3 块/)` 撞多元素。

**下一步**：**全量真跑审计**（方法见 `docs/plans/2026-10-01-P19-C2c2-材料冻结口径.md` §11）→
C-2c-3 按主张取块 → C-2c-4 `web_fetch`/docling/docx-xlsx。

## P20 · 全量真跑审计（2026-10-01，**已交付**）

**方法重审**：`docs/plans/2026-10-01-P20-全量真跑审计.md`（取代 P19 §11；八处要改 + 四条车道 +
取数口径 + 预算）。**结果**：`.codex/runs/2026-10-01/报告.md`（四份导出 + `汇总.json` +
每车道 `probe.json`/`transcript.jsonl`/`decisions.md`）。

**一句话结论**：四条车道（叙述式 / 逐条列举+补字段 / **带材料** / **联网+加功能**）、
**13 个跑完的轮次、210 次模型调用、83.3 分钟模型时间、3 次 502（全是报文校验，传输层 0 失败）**，
产出四份 **2.7–3.3 万字**文档（功能树 7–9 棵，编号抽样 80/80 可解析）。
**0 份"就绪度 0 阻塞"**；最近的 **L3（带材料）只差 1 项**（s11 两条阻塞未决，已在下一轮必问队里）。

**新发现**（详见 `04-未决.md` G4 与报告第三节）：**F16 起轮即判死**（`claims` 12 条上限 +
`assumption` 挂错标签，各判死一整轮，且没有 `/retry`）、**F17 席位报文占位符整轮判死**、
**F18 骨架前四章默认没人写**、F19 `unaccounted` 未降、F20 未决题只涨不降。
**修好的 8 条**：F1 零功能候选（叙述式第 1 轮长出 6 棵功能树，09-28 是 0 棵）、F2 条数超限、
F6 跨批去重、F7 矛盾判据、F9 写范围、F11 留痕封顶、F13 未知席位、F15 重复提问。

**审计纪律自检**：`git diff --stat` 只有作者自己的 `.gitignore`；`backend/data/pmstudio.db` 的
mtime 仍是 09-28；`tests/architecture` 22 passed。**产品代码一行没改。**

**工具**（`.codex/runs/2026-10-01/bin/`，gitignored）：`drive.py`（加了 `upload`/`abandon`/
`--materials`/`--ask-opening`/起轮 502 重试/502 后回读状态）、`autopm.py`（复述卡真裁席位 + 真勾检索词、
问卷不许 skip、草案按来源判、预算闸、扮 PM 的容错）、`audit_probe.py`（只读取数 + `--selftest` 11 条）。
执行中工具自己修掉 4 个 bug（旧 `DRIVE` 路径、漏带 `enable_thinking:false`、模型回数组、卡口失败顶停车道）。

**下一步**：按报告第六节的三条（F16 起轮判死 → F18 骨架填写度 → F20 未决消化）挑一条做；
之后再回到 C-2c-3 按主张取块 / C-2c-4 `web_fetch`·docling·docx-xlsx。

## P21 · 起轮不判死 + 骨架填写度 + 未决消化（2026-10-01，**已交付**）

方案：`docs/plans/2026-10-01-P21-起轮不判死-骨架填写度-未决消化.md`（六任务）。
验证：`.codex/runs/2026-10-01/P21验证.md`。

| 提交 | 内容 |
|---|---|
| `ca56000` | `ClaimsPacket.claims` 上限 **12→20**；分析者改走 `parse_packet_trimmed`，溢出写成留痕（`主张 #21 起`） |
| `e138e97` | `claims_from_packet` → `(claims, dropped)`：非 `guess` 带 `assumption` **脱字段不丢主张**（`c{i}` 序号还被互斥对子指认着） |
| `5c5bf4e` | `analyzer.md` 硬规矩 4 补**归位对照表**（现状→s1 / 目标→s2 / 角色→s3 / 范围→s4 / 假设→s10 / 上线验收→s12 / 量级时限→s8 槽 / 功能做法→s5） |
| `c4a1f2e` | `assembly.backbone_gap_lines()` + 整合时接进 **草案卡 `gaps`**："文档里这几章还是空的（这一轮也没往那儿写）" |
| `ec3377a` | `assemble()` 新题排序键加 `0 if question.blocking else 1`：**阻塞题先拿配额** |
| `5cfbfbc` | 真跑抽样抓到的 bug：骨架缺口要看**块类型**——往 s12 写一条正文 ≠ 填了验收标准 |

**口径**（`02` §11.0.32/33/34）。**没动** `MAX_OPEN_ITEMS_PER_BATCH`(3) / `MAX_QUESTIONNAIRES`(2) /
写范围白名单 / 文档 schema / 端点（仍 25）。

**三条硬证据**：① 审计里**当初判死的那两条真实回包**（`model_calls` id 33/34）确定性回放——
现在一条过（12 条 + 留痕）、一条 17 条全保留；② 现在 `.env` 的 `qwen3.7-flash` 真跑单轮：
那份"起轮 502"的输入 **36.1 秒起轮成功**（切出 16 条，旧上限 12 会判死），
归位到了 **s1/s2/s3/s4/s10**（审计那一轮是零条）；③ 该轮 **s11 = 0 条、0 阻塞**。

**测试**：后端 **898 passed**（基线 882 → +16）· ruff 全绿 · 端点契约 25 · 结构守卫 22。
**执行中改过 2 处我自己刚写的测试断言**（断言了不存在的文案"骨架"、锚点用了方案里发明的编号）
——都不是产品代码的问题，验证报告里写明了。验证期间**产品代码之外的产物**都在 `.codex/runs/`。

**下一步**：C-2c-3 按主张取块 / C-2c-4 `web_fetch`·docling·docx-xlsx；
或再跑一次多轮真跑审计，看 s11 与就绪度到底能不能收敛到 0 阻塞。

## P22 · F17 占位符不再判死整轮 · F19 unaccounted 收口 · C-2c-3 测量否决（2026-10-01，**已交付**）

**拍板**：作者「按照推荐的来」——F17 范围=席位 + 主张（集成者单开一轮）、先重装再丢弃；
F19 先补提示词与 eval 断言、再修判据；C-2c-3 先做 Task 0 测量再定深度。

| 提交 | 内容 |
|---|---|
| `c029a8b` | **F17 解析层**：`schemas.Discarded` + `parse_packet_lenient()`（摘掉 `list[BaseModel]` 里的非对象元素并上报）；`parse_packet_trimmed` 的签名与行为**一个字没改**（新增用例钉住"它不许静默摘"） |
| `c029a8b` | **F17 席位层**：`SeatOutcome.discarded`；`_review_one` 先重装一次（`repair_note` 的 `discarded` 分支，不回喂占位符），第二次仍错 → `_discarded_items` 写进 `Round.dropped`（`seat.x:opinions[2]`），整轮继续 |
| `c029a8b` | **F17 主张层**：分析者改走 lenient；`ClaimPositions`（原始序号 → 落下来的第几条）重映射互斥对子，指到被摘掉那条的对子丢那一条 + 留痕；`discarded_claim_drops` 留痕 |
| `c029a8b` | **F19 提示词**：`$limits` 与 `integrator.md` 补"采纳了某条意见就要引它"（判断引它自己的 E 编号、疑问写 `from_opinions` 地址）——原来只有"没采用的要申报"，考的是从没交代过的事（L22） |
| `c029a8b` | **F19 判据**：`produced_questions` 改成 `questions + deferred`（本批没排上的题也算产出）；尾句按**形态**分两句（疑问"没被变成题" / 判断"没进任何提案或题目"） |
| `c029a8b` | **F19 断言**：`evals/assertions.py` 新增 `unaccounted_at_most`（阈值 2，挂进默认七条之后成八条） |
| （不进库） | **测量脚本**：`.codex/runs/2026-10-01/bin/measure_unaccounted.py`（167 条留痕按来历切开）、`measure_chunks.py`（Task 0 三策略对照）——`.codex/runs/` 是 gitignored 的现场记录 |

**度量（只读真跑库，不花模型钱）**

| 事 | 数字 |
|---|---|
| `unaccounted` 总量 | 四车道 13 个轮次共 **167** 条 |
| 其中 疑问（无编号，只能靠 `from_opinions`） | **60**（36%）——提示词那条规矩直接覆盖 |
| 其中 判断（自己的 E 编号被某处引过） | **55**（33%）——陈旧条目或引用发生在别处 |
| 其中 判断（编号从头到尾没被引） | **52**（31%）——真没用也没申报 |
| T2（deferred 题也算产出）在这批数据上的可测效果 | ≤1 条（逻辑确凿，主要杠杆还是提示词） |
| **C-2c-3 Task 0**：材料预算到底卡没卡 | **从未卡住**：L3 三份真材料共 2424 字 / 上限 4000（59 块全带）；L4 1200 字 |
| 三种选块策略（按序 / 按用户原话 / 按主张） | 真实预算下**选出的块完全一样**（59/59），有落点的主张都是 7/12 → **收益为 0** |
| 人为把预算压到 1000 / 600 | 按序 2/12 → 按主张 4/12；600 时 1/12 → 3/12（**只有预算真紧张才有效**） |

**结论**：**C-2c-3 不做**（触发的条件在真数据里没出现过）；整合者那条路的非对象元素**照旧判死**
（它还没有"把丢掉的提案写进留痕"的出口）；`MAX_OPEN_ITEMS_PER_BATCH` **不动**。

**核验**：后端 **914 passed**（基线 898 → +16）· `ruff` All checks passed ·
前端 `tsc` 无输出 + `vitest 148 passed`（前端**一行没改**）· 端点契约 25 条未动 · 结构守卫 22 绿。
验证报告：`.codex/runs/2026-10-01/P22验证.md`。

### P22 后半 · 真跑回归（同一天，跑完才发现上面那条 F19 的结论要改）

**F17 确定性回放（零成本，决定性）**：拿审计里那份**逐字真实**的回包
（`/tmp/pmstudio-audit-L2.db` 的 `model_calls` id=41，`opinions[1] = "O2_placeholder"`）——

| | 结果 |
|---|---|
| 改前 `parse_packet_trimmed` | `PacketParseError`，定位 `('opinions', 1)`（不是 `too_long`）→ 整轮判死 |
| 改后 `parse_packet_lenient` | 收下 1 条意见 + `Discarded(opinions, 2)` + 话术 |
| 用例级回放（同报文过一整个席位轮） | 轮次停在 `seats_running`（改前是 `failed`）· 2 次席位调用（重装一次）· 1 条留痕 · 无 `generation.failed` |

**F19 真跑（L1 车道两轮，26 次模型调用）——目标没达成，如实记**：

| 口径 | 改前（审计 L1，4 轮） | 改后（P22b，2 轮） |
|---|---|---|
| 意见 / unaccounted | 316 / **34**（10.8%） | 97 / **34**（35.1%） |
| 形态构成 | 疑问 13 · 判断(被引) 8 · 判断(没用没申报) 13 | 疑问 2 · 判断(被引) 9 · 判断(没用没申报) 23 |

提示词那条规矩**没有把总量降下来**；`unaccounted` 是**模型行为率**，不是结构不变量，
所以它**不进 eval 默认断言集**（我原先加的 `unaccounted_at_most: 2` 会被真跑超阈 4–17 倍，
已按证据移出默认集，只保留类型供 `extra_assertions` 单点使用）。
下一步是**机械兜底**：出草案时对没交代的意见做一次定向重装（与超限/形状错同一招）。

**真跑顺手抓到一条更重的（已修 + 已真跑验证）——`02` §11.0.36**：
席位判断的账本幂等键 `p{趟}:{条目}` **没有轮次维度**，而趟数每轮从 1 重来 ——
第二轮复用第一轮的编号，**账本文本与意见对不上**（用户点编号看到的是别的判断）。
审计四车道 **122 条判断里 14 条错位（11.5%）**；三处读取端（给整合者看的编号、给界面看的编号、
归因核对）全跟着错。修法：身份带回轮次（`(轮次, 趟, 席位, 条目 id)`）、
`find_by_local` 精确命中 + 老行只给读取端兜底、写入端幂等检查 `allow_legacy=False`、
两处投影的 `round_id` 变必填。
**真跑复量：68 条判断错位 0 条**（脚本 `.codex/runs/2026-10-01/bin/verify_round_scope.py`）。

**数字**：后端 **920 passed**（P22 前半 914 → +6）· `ruff` 全绿 · 前端未动。

## P23 · 归因补交代 + 整合的 lenient 解析（2026-10-01，**已交付**）

**拍板**：作者「都按照推荐的来」——A 每次都补（问卷那几批也补）、话术复用
`$validation_error`、B 的全占位符包走既有降级、做完**真跑验证**。

| 提交 | 内容 |
|---|---|
| `851e5e6` | **A1**：`repair.accounting_note()`——只给 handle + 三条出口（判断引编号 / 疑问写 `from_opinions` / 没采用就申报），不回喂正文 |
| `851e5e6` | **A2**：`_accounted()` / `_unaccounted_for()`——"谁交代过"的唯一实现，**判定（要不要点名）与写库（留痕）共用**，免得两处漂移 |
| `851e5e6` | **A3**：`_integrate_with_repair` 的 `for` → `while`，两本账分开（校验 2 / 归因 1）；装配后算 unaccounted，不为空就点名重装一次 |
| `851e5e6` | **B**：`IntegrationOutcome.discarded` + `parse_packet_lenient`；重装一次 → 仍错就丢那一条（`提案 #2（不是对象）`）+ 草案卡缺口行；**全占位符的包判成报错**（否则 `draft_of is None` → `ShapeError` → 409） |
| `851e5e6` | `repair.py` 的 `discarded` 话术去掉席位专用措辞（"写成一条疑问"→"写成对应容器里的一条正经内容"），席位/主张/整合三处共用 |

**真跑（同车道两轮，同一个模型）——部分有效，没解决**：

| 口径 | P22b（只有提示词） | P23（点名重装） |
|---|---|---|
| 席位意见 / `unaccounted` | 97 / 34（35.1%） | 81 / **18（22.2%）** |
| 逐轮 | 16 / 18 | **0** / 18 |
| 整合调用 | 6 | **12** |

**模型确实会照办**（三次点名 3/4/3 条，它补了 2/4/3 条），但**只补一次不收敛**：
名单按第 1 份报文算，重写后的报文又漏了别的，而额度只有 1 次。第二轮 3 趟席位
本该 3 次整合，实际用了 9 次。**下一步（未做）**：额度提到 2 并每轮重算名单，
或从源头压产量。报告：`.codex/runs/2026-10-01/P23验证.md`。

**数字**：后端 **933 passed**（P22 的 920 → +13）· `ruff` 全绿 · 端点契约 25 条与
结构守卫 22 条未动 · 前端未改。

## P24 · 归因补交代改成小调用（2026-10-01，**已交付**）

**拍板**：作者「按照你说的备选那个来做；其余的按照你推荐来」——即 P23 那版"点名重装整包"
换成"**小调用只问去向、并回原报文、不重写**"，外加"没进展就收手"与"小调用失败不判死"
两条护栏，`accounting_note` 退役。

| 提交 | 内容 |
|---|---|
| `ebc3cf2` | `context.render_selected_opinions()` + `render_citations()`：小调用只看"点名的那几条"与"核算真正读过的东西"（同一把尺子） |
| `ebc3cf2` | `schemas.DispositionsPacket`（**只有** `dropped_opinions`）+ `prompts/opinion_dispositions.md` + `Integrator.dispositions()`（新 purpose，槽位机械比对照旧） |
| `ebc3cf2` | `_integrate_with_repair` 拆两段：整包段（校验重装 ≤2）→ 补交代段（小调用 ≤2 + 护栏）；`_assemble_and_gaps()` 抽出（装配 + 留痕 + 缺口行，合并后重跑）；`_merge_dispositions()`（只认点过名的 handle、按 handle 去重） |
| `ebc3cf2` | 删掉 `repair.accounting_note()` 与它的用例（没有消费者了） |

**真跑（同车道两轮、同模型 `qwen3.7-flash`）**：

| 口径 | P22b（提示词） | P23（整包重装） | **P24（小调用）** |
|---|---|---|---|
| 席位意见 / `unaccounted` | 97 / 34（35.1%） | 81 / 18（22.2%） | **81 / 12（14.8%）** |
| 逐轮 | 16 / 18 | 0 / 18 | **0 / 12** |
| 整包调用 / 小调用 | 6 / — | 12 / — | **7 / 3** |
| 点名补上率 | — | 90% | **100%（8/8）** |

**那 12 条的根因（逐报文核过）**：全部来自趟 3 的**降级出口**（整包两次没过校验：
模型写了不存在的条目 id）——疑问原样落 s11、没落地的判断照旧报；那条路早退 `return`，
**不经过补交代**。**机制本身达标**（补上率 100%、小调用 3 次、整包回落到 7、溯源错位 0）。

**下一步（未做）**：把"要改的条目不存在"从整包判死降成"丢那一条 + 留痕"（与 `02` §11.0.2
对"去处写错"的做法完全同一件）。报告：`.codex/runs/2026-10-01/P24验证.md`。

**数字**：后端 **940 passed** · `ruff` 全绿 · 端点契约 25 条与结构守卫 22 条未动 · 前端未改。

## P25 · 「要改的条目不存在」不再判死整轮（2026-10-01，**已交付**）

**拍板**：作者「要；先出方案，然后再执行」。方案 `docs/plans/2026-10-01-P25-条目不存在不再判死.md`。

| 提交 | 内容 |
|---|---|
| `c81ffc5` | **A**：`assembly._entry_target_missing` + `assemble` 提案循环 —— `replace`/`remove` 指到不存在的条目 → **丢那一条 + 留痕**（与"去处写错"同一条口径，§11.0.2）；`_proposal` 里那处 raise 保留做自证，规则只有一份实现 |
| `c81ffc5` | **B**：`use_cases` 第一段循环加守卫 —— 装配完"题/提案/清单项"**什么都不剩** → `PacketParseError`（走重装→降级），**替掉 HTTP 409**；顺带堵掉"空包"这个老洞 |

**真跑（同车道两轮、同模型）**：

| 口径 | P22b | P23 | P24 | **P25** |
|---|---|---|---|---|
| 意见 / `unaccounted` | 97 / 34 | 81 / 18 | 81 / 12 | **70 / 4（5.7%）** |
| 逐轮 | 16 / 18 | 0 / 18 | 0 / 12 | **0 / 4** |
| 整包 / 小调用 | 6 / — | 12 / — | 7 / 3 | **7 / 2** |
| 点名补上率 | — | 90% | 100% | **100%（13/13）** |

**主指标达成**（两轮 ≤6）。本轮"条目不存在"没真出现过（由 5 条单测钉着）；
轮 2 仍降级一次，原因换成"**提案文本为空**"（内容字段，本轮没动）。
**下一步候选（待拍板）**：把"提案文本为空"也降成"丢那条 + 留痕"——丢一条空内容不丢信息，
但会动到"内容字段一律判死"这条边界。

**数字**：后端 **945 passed** · `ruff` 全绿 · 前端未改 · 报告 `.codex/runs/2026-10-01/P25验证.md`。

## P26 · 空文本提案：`remove` 补正文、`add`/`replace` 丢一条（2026-10-01，**已交付**）

**拍板**：作者「可以，也降成，你先好好看看」。

**先看清再下手（本批最重要的动作）**：跨 **5 个真跑库**统计空文本提案 —— **22 处全是
`op=remove`**（`add`/`replace` 一处没有），全是"要撤下 s11 某条未决条目但没写文本"。
所以**没按字面"丢掉那一条"做**：丢掉一条 `remove` 等于让该撤下的内容留在文档里。

| 提交 | 内容 |
|---|---|
| `8ec86c9` | `assembly._entry_text()`（一次遍历，兼做 `_entry_exists`）+ 提案循环：`remove` 空文本**补上被撤下那条的正文**；`add`/`replace` 空文本**丢那一条 + 留痕**；**去重只对 `add`**（同族老 bug：remove 文本照抄被删那条会被当重复静默跳过） |
| `8ec86c9` | 提示词同步：`ProposalDraft.text` 字段描述 + `$limits` 写明"`remove` 的 text 可以留空" |

**领域不变量没动**（`Proposal`/`WriteInstruction` 仍要求非空文本）——装配层在构造之前就处理掉了。

**零成本回放**（`bin/replay_p26.py`）：P25 那两次**真降级**的报文（各含 2 条空文本 remove）
现在都装配通过，两条 `remove` 都保住并补上了正文（卡片显示「删除｜〔未决·阻塞〕…」）。

**真跑（同车道两轮、同模型）**：

| 口径 | P22b | P23 | P24 | P25 | **P26** |
|---|---|---|---|---|---|
| 意见 / `unaccounted` | 97 / 34 | 81 / 18 | 81 / 12 | 70 / 4 | **58 / 0** |
| 逐轮 | 16 / 18 | 0 / 18 | 0 / 12 | 0 / 4 | **0 / 0** |
| 整包 / 小调用 | 6 / — | 12 / — | 7 / 3 | 7 / 2 | **5 / 3** |
| 整轮降级 | — | — | — | 1 | **0** |

**`unaccounted` 归零、降级归零**；整轮模型调用 23 次。
**数字**：后端 **950 passed** · `ruff` 全绿 · 前端未改 · 报告 `.codex/runs/2026-10-01/P26验证.md`。

## P27 · C-2c-4：主要输入口——按网址抓网页 + Word / Excel（2026-10-02，**已交付**）

**拍板**：作者「都按照推荐的来」（切在哪 = `.docx` + 按网址抓、`.xlsx` 同批带上；
私网地址**默认拒绝**；同一个网址再抓 = **刷新**）。

**一句话口径**：材料这条链**一个字段都没长**——三个新输入口（`.docx` / `.xlsx` / 一个网址）
都走 `ParsedMaterial → MaterialStore.put → select_chunks → 只给选中的块发号 → render_materials →
账本 external`。端点 25 → **26**。

| 提交 | 内容 |
|---|---|
| `151d767` | 计划：`docs/plans/2026-10-01-P27-C2c4-按网址抓网页与Word.md` |
| `eab0f13` | `.docx` 真解析：按文档**真实顺序**切块（`body.iterchildren()` 保序）、表格按行渲染、锚点「第 N–M 段 / 表格 N（表头：…）」；顺手抽出 `tools/limits.py`（两边共用一个上限） |
| `7805971` | `.xlsx` 真解析：一个工作表一块、每行按 `a 竖线 b` 渲染、公式读缓存值（没算过的格子照实说一句） |
| `5a79e44` | `WebFetcher` 端口 + `HttpxWebFetcher`：只收 http(s)、**拒绝内网地址（每跳重判）**、≤3 跳、15s、5MB、Content-Type 必须是网页；与上传 `.html` 共用 `extract.html_text` |
| `684a468` | `FetchMaterial` 用例 + `MaterialStore.refresh`：同址再抓 = 刷新（没引用就地换 / 有编号另出一份） |
| `39f0429` | `POST /projects/{id}/attachments/fetch`（25→26）；`fetch_refused`(422) 与 `tool_call_failed`(502) 分开 |
| `e7bb200` | 前端「+ 粘贴网址」（抓取中禁用并写"正在抓…"） |
| `af47e89` | 提示词：非上传材料的头补一行「来源：URL」 |

**真跑（同车道一轮、真模型 `qwen3.7-flash`、真网抓 `https://example.com/`）**：
docx 3 块（段落 1–2 / 表格 1 / 段落 4–5）· xlsx 1 块 · example.com 1 块 → 起轮只给这些块发号
**E1–E5** → 提示词里三份材料都在（带编号、锚点与 URL）→ 席位意见真引用 **E2–E5** →
只引材料块的那条提案是 `proposed`（默认不勾，"材料不算权威"又验一遍）→ 同址再抓：
发编号前**同一个 material_id**、发编号后**另出一份**；内网地址 422 一句人话；
整轮跑到草案卡并确认写入（v2）。报告 `.codex/runs/2026-10-02/P27验证.md`。

**核验**：后端 **992 passed** · `ruff` 全绿 · 结构守卫 22 绿 · 端点 **26**（白名单显式 +1）·
前端 **151 passed** + `tsc` 干净 · 工作库 `backend/data/pmstudio.db` 没碰（真跑全在 `/tmp`）。

**两处与方案不同（都写进报告了）**：① 失败分两档——**"你给的地址不能用" = `fetch_refused` 422**、
**"抓的时候出了问题" = `tool_call_failed` 502**（方案原话是"一律 `ToolCallFailed` → 422"，
与既有 502 映射冲突，实现时按语义分开）；② `WebFetcher` 返回 `FetchedPage{parsed, content}`
（**原始字节必须一起带回来**，否则材料表存不了真 HTML、"抓来的网页可重解析"落不了地）。

**下一步（等拍板）**：① 换 `glm-5.3` 跑一轮同车道（看机制在另一个模型上是否同样稳）；
② 做一次**全量真跑审计**（P20 那套四车道）回答"现在能不能交付"；③ 讨论区（`04` F10，v1 之后）。
工具层到此**全部收口**：C-2c-1/2/4 已交付，C-2c-3（按主张取块）测量否决，`docling` 不做（端口留着）。

## P28 · 跨轮记忆：先测量，再收口两处（2026-10-02，**已交付**）

**拍板**：作者「按照推荐的来」——本批只做 A 组（跨轮事实层），且按 T0 的判据执行。

**先测量再动手**（零模型成本，31 轮真跑库）：三个数里**两个是 0**、一个是真缺口。

| 事 | 实测 | 处置 |
|---|---|---|
| 跨轮重问（前轮已答、这轮又问） | **0 条**（p11 那 4 条是"未确认轮次的欠账被重问"，正确行为） | **不做** T1–T3（注入 + 判重）：收益 0、代价是一节提示词 + 误杀风险 |
| 被拒提案回流 | **0 条** | 不做 |
| **s11 目标题的答案蒸发** | **9 条**（已确认轮次里文档一个字都没有） | **已修** |
| 问法只差标点被当成两道题 | 1 条真案例（audit-L2） | **已修** |

| 提交 | 内容 |
|---|---|
| `26c688e` | 判重键归一化**首尾标点**（`assembly._asked_how`）：`…规则？` 与 `…规则` 是同一道题；只剥首尾（中间的问号照旧算数） |
| （本批） | `landed_answer_proposals`：**s11 目标题的答案照落**，落成 `〔已定〕<答案>`；`readiness.SETTLED_MARK` 让"已定记录"**不算未决**（不挡交付） |

**证据**：零成本确定性回放 `bin/replay_p28.py`（只读真库 + 真仓储读回整轮）——那 **9 条**
以前全部蒸发，现在 **9/9** 落成 `〔已定〕…`；同一回放里原本正常的答案一条没少。
报告 `.codex/runs/2026-10-02/{P28-T0测量.md,P28验证.md}`，口径写进 `02` §11.0.39。

**核验**：后端 **996 passed** · `ruff` 全绿 · 结构守卫 22 绿 · **端点仍 26**（零新表、零新智能体、
零端点变更）· 前端未改 · 工作库没碰。

**这一批唯一被改的既有测试**：`test_an_answer_whose_target_is_s11_is_not_landed` → 改成
`…_lands_as_a_settled_record`（**口径变更**，被 T0 的真数据推翻，不是迁就实现）。

**触发条件（写下来免得凭印象开工）**：以后量到"跨轮重问 ≥3 条/项目"或"被拒回流 ≥2 条/项目"，
就做那套跨轮注入 + 判重；脚本 `.codex/runs/2026-10-02/bin/measure_cross_round.py` 改一行就能重跑。
**记忆本体（PROJECT.md / Memory Curator / 引用计数 / 归档）仍不做**——没有实证说缺它。

**下一步（等拍板）**：① 换 `glm-5.3` 跑一轮同车道；② 全量真跑审计（回答"能不能交付"）；
③ 前端"眼睛检查"（P20 §七第 1 条欠着）；④ 完整 eval（P16 之后没再跑过）；⑤ 讨论区（`04` F10）。

## P29 · 前端眼睛检查 + 收口（2026-10-02，**已交付**）

**拍板**：作者「按推荐来」——系统 Chrome + 零依赖 CDP（不加 playwright）；范围 A+B（看 + 修）；
报告放 `.codex/runs/`；断点仍在 1240 / 980 上修。

**这一批补的是 P20 审计自己写下的欠账**："没验前端界面…需要另立一轮眼睛检查
（浏览器 + 截图）"。做法：真跑库**副本**（L3 / eval / p14-dig / p6）+ 真后端与 vite +
系统 Chrome（headless）+ 零依赖 CDP 脚本，**15 屏**（主界面 / 功能树 / 问卷 / 草案 / 版本 /
材料库 / 搜索 / 轮次 / 就绪度 / 偏好区 / 1100 / 900 / 暗色 / Onboarding / 失败态），
每屏量 console 错误、容器溢出、顶栏折行、被挤成一列一个字的元素。

| 提交 | 内容 |
|---|---|
| （前端） | F1–F4：三处**拿文本当 React key** 的列表改成带序号（真库 39 条清单项里 9 条重复 → 54 条警告，行可能被丢） |
| （前端） | F3：草案卡告警**合并 + ×N**（`groupAdvisories`）——以前三条一样的话摆三遍 |
| （前端） | F5：材料卡编号**只列前 3 + 等 N 个**（`summarizeEids`，全量进 `title`）+ 卡片改堆叠布局（以前 31 个编号竖排、行高 500px） |
| （前端） | F6：**补上 `06` §2.2 承诺的"就绪度清单抽屉"**（`ReadinessDrawer`）——"就差 N 项"以前是死文本 |
| （前端） | F7：顶栏窄屏收纳 + 按钮 `nowrap`（1100/900 时 `导出`/`暗色` 被压成竖排） |
| （前端） | F8：`state/failure.ts` + 状态行——**"失败在席位轮；点重试会从席位轮继续"**（后端 P13 就给了） |
| （前端） | F9 + 底栏：左栏长名字 ellipsis（`min-width:0` + `align-self:stretch`）；底栏提示被挤成一列一个字 → 允许换行 |
| （前端） | F10 + 抽屉：后端掉线时不再只说"请求失败（HTTP 500）"，改说"**后端没有正常回话**——它可能在重启或没起来"；`.drawer` 写死 `minmax(0,1fr)` + 动作行换行（"确认写入"按钮曾被推到视口外） |

**工具**（不进仓库）：`bin/ui_probe.sh`（五种 flavor：真库副本 + 端口）/`bin/ui_capture.sh`（15 屏）/
`bin/ui_shot.mjs`（CDP：导航 / 点击 / 输入 / 暗色 / 截图 / 量测）。报告
`.codex/runs/2026-10-02/前端眼睛检查.md`；口径写进 `06` §8.1 与 §7。

**核验**：16 屏**console 全 0**、容器 `overflow` 全 `false`、`.topbar wrapped=no` 三档、`squeezed=[]`；
前端 **178 passed**（151 → +27）+ `tsc` 干净；后端未改（跑一遍确认没连带）；
真跑库只用副本（原库只读）。

**量具本身修过三次**（留档）：① 只看横向溢出 → 漏掉"文字竖排"；② 按子元素 top 分档 → 噪声大；
③ 定成"叶子元素文字盒的 top 有几档"；④ 失败态实拍发现"一列一个字"量具没抓到 → 新增 `squeezed` 扫描。

**下一步（等拍板）**：① 换 `glm-5.3` 跑一轮同车道；② 全量真跑审计；③ 完整 eval（P16 之后欠着）；
④ 讨论区（`04` F10）；⑤ 可选：在提示词里收一收"同一件事被记两遍"的清单项（数据现象，不是界面问题）。

## P31 · 导出附录 A 只列"本文引用过"的出处（2026-10-02，**已交付**）

**拍板**：作者指出"导出的 md 里证据栏占了整个文档一大半，完全不合理"。
方案 `docs/plans/2026-10-02-P31-导出附录只列本文引用.md`；验证 `.codex/runs/2026-10-02/P31验证.md`；
工具 `.codex/runs/2026-10-02/bin/p31_export.py`（只读，真跑库先 `.backup`）。

**根因不是"字太多"，是"附录在排谁"**：`_appendix_a` 排的是**项目账本全量**
（项目级、跨轮、只增），不是"这份文档的出处"。账本里还有未被采纳的提案、被跳过的题、
更早的轮次——那些编号在正文里根本没有落点。
**实测（59 份真跑导出）**：附录 A 占全文 **中位数 51%**、最大 73%；
附录里 **中位数 63%** 的条目从未被正文引用。最坏一份是
`2026-09-28/试点-设备点检与报修/文档-第六轮.md`（附录 A 70%，273 条里只引用了 107 条）。

| 提交 | 内容 |
|---|---|
| （本批） | `cited_evidence_ids(view)` + `iter_document_entries(view)`（与 `dto.used_by_entries` 共用一条遍历）；`_appendix_a` 按"本文引用过"过滤 + 末尾一行 `（另有 N 条…）` |
| （本批） | `_feature()`：功能**名称自己的编号**挂到标题上——对账抓到的另一处真问题（界面 `FeatureBlock` 一直挂着，导出把 `name.refs` 吞了，于是附录里出现正文看不见的编号：L3 的 E28、L2 的 E170，两条都在真跑日志 `pending.md` 里对上了 `s5/…name` 的提案） |

**真跑取证（4 条真车道、真库副本重导出、逐条对账）**：全文省 22–32%；
附录 A 占比 43–70% → **27–38%**；条目 211–251 → 94–114；
**被引用的编号一条不丢**；正文（除"导出时间"一行）与附录 B/C **逐字节一致**。
**如实记**：4 份真跑库都在 `/tmp`，验证中途 `/tmp` 被环境清空，
**名称修好后没能在真库上复跑**（残留 0 是推算 + 单测钉住，不是复跑）。

**口径**：`01` §7.3 / `02` §10 / `04` D5。**账本一个字没动**（不裁剪、不归档）；
端点 / schema / 提示词 / 前端未动。**核验**：后端 **1003 passed** · `ruff` 全绿 ·
前端 `tsc` 干净 + **178 passed**。

**没做（留给出题人）**：① s11 未决清单本身很长（L2 那轮 7 348 字符）——那是正文内容，另案；
② 功能名被写成整句话（L2 `### 5.7 用户已定：纳入本期范围：…`）——数据质量问题（答案机械落到 `feature.name`），
不是导出的问题；③ 附录摘要上限仍是 60 字——本批证明**多余的是条目数，不是每行长度**。

## P30 · 全量真跑审计 + 运行时接管（2026-10-02，**已交付**）

> **批次顺序说明**：P30 与 P31 是**同一天并行**的——P30（我）跑在 `a6354c1` 上、
> 五条车道 20:41 前全部收工；作者的 P31 改动落在 **20:50–20:52**，在车道收工之后。
> 所以 P30 的结论**只对 `a6354c1` 那个状态负责**，P31 之后要重跑才算数。

**拍板**：作者「可以，按照推荐的来」——5 条车道（含 L5"跑到交付态"）、主审计用当前 `.env`
（`qwen3.8-27b`）、扮 PM 用 `deepseek-v4-pro`、**只记账不修**、eval 跟本批一起跑、
监控 20 秒一次、不做 D 组截图。

**一句话结论**：**五条车道里两条到了「就绪度 0 阻塞」可交付**（L1 v4 · L5 v3）——
P20 那次「0/4 可交付」被推翻。另三条差 **2 / 1 / 8** 项，各卡在不同地方。

方案 `docs/plans/2026-10-02-P30-全量真跑审计与运行时接管.md`；
报告 `.codex/runs/2026-10-02/全量真跑审计/报告.md`（+ `汇总.json` + 五条车道 `probe.json`）；
工具 `bin/{gate,tap,monitor,run_audit.sh}`（新）+ `{drive,autopm,audit_probe}.py`（升版），**都不进仓库**。

**这一批的新东西是"接管运行时"**：四路流量都记下来——驱动↔后端（`transcript.jsonl` + `steps.jsonl`）、
后端→界面（**新** `tap.py` 长连 `/api/events` → `events.jsonl`，P20 一条事件都没记过）、
后端↔模型（`model_calls`）、后端↔库（`monitor.py` 只读采样）。监控八条规则，
`working_db_touched`（工作库被碰）与 `backend_down` 是**叫停级**。

| 车道 | 形态 | 写了几轮 | 导出字数 | 阻塞 | 调用 | 事件 |
|---|---|---|---|---|---|---|
| L1 | 叙述式（设备点检 / 报修 / 备件） | 3 | 15,580 | **0** | 38 | 35 |
| L2 | 逐条列举 + 补字段（连锁健身） | 3 | 17,634 | 2 | 35 | 46 |
| L3 | **带材料** docx+xlsx+网址（跨境内核） | 3 | 17,874 | **1** | 42 | 55 |
| L4 | 联网 + 加功能（科研 CRF） | 2 | 17,123 | 8 | 46 | 57 |
| L5 | 窄而完整（社区食堂） | 2 | 12,178 | **0** | 28 | 39 |

**新缺陷四条**（都记进 `04` G10）：

1. **F28（阻塞级）**：同一轮对同一条 s11 条目提了**两次 `remove`**（整合者那条路 + carried 清理那条路），
   装配时两条都合法，确认写入时第二条找不到条目 → **`POST /confirm` 422、45 条一条都没写**。
   L4 之所以只有 2 轮正文就是它。**证据**：`L4/rounds/r-1526d157.json`（p14/p34 同指 `r-a80daa26#open23`）、
   `L4/accept.json`（两条都收了）。**修法另起方案**：确认前按 `(entry_id, op)` 去重。
2. **F29**：起轮 502「模型把主张整包返回成数组」（L4/L5 各 1 次，重试即过）——F21/F22 换的新形态。
3. **F30**：`integration` 在 `qwen3.8-27b` 上平均 98–150s、**最慢 346.7s**，两次撞 180s 超时后重试成功
   （P20 旧模型最慢 103.5s）。**没改**，属另案（抬超时 or 换模型）。
4. **F31**：联网这次**没跑到**——五条车道 `web_queries` 全空（分析者没提名检索词）。

**旧账对账**：F16 / F17 / F18 / F20 ✅ 不再复发；**F19（unaccounted）从"每轮 4–26 条"降到
14 轮里 3 轮出现、合计 25 条**（L3 / L5 归零）；F21 / F22 🔁 换形态（= F29）。
能力覆盖：**`.docx`（6 块）/ `.xlsx`（2 块）/ 粘贴网址（1 块）三条路第一次进主闭环**（L3 的 48/48 功能字段全齐、导出里 9 处外部材料引用）；联网 ⚠️ 无证据。

**监控账**：八条规则里 5 条在真跑中被验证过——两条**真警**（热身车道没起 tap 被 `event_gap` 抓到；
修 bug 重启接管造成的 head 缺口），五条**误报全部来自工具自己的三个 bug**（已按 TDD 修）：
tap 的缺口检测按批算（每帧都报"开头缺一条"）、tap 认不出"接管起晚了"、
monitor 把"还没起来的车道"当掉线并写 `STOP`（把 L4 的驱动当场劝退）。
`working_db_touched` / `config_drift` / `rate_limited` / `stalled_step` 全程**一次都没触发**。

**工具自己在真跑里修的 6 个 bug**：见报告 §九（含 `run_audit.sh` 没给驱动带 `PMSTUDIO_BASE`、
`spawn` 用 `setsid`、`STOP` 文件是粘的）。

**纪律自检**：只往 `.codex/runs/` 与 `docs/plans/` 写；`backend/data/pmstudio.db` mtime 仍是
2026-09-28 14:31；真跑库全在 `/tmp/pmstudio-p30-*-b1.db`。

**收工后的意外（如实记）**：21:32 前后 **`/tmp` 被环境清空**——五个车道库没了，
`model_calls` 的逐次提示词/回包原文随之丢失。**结论不受影响**（取数在清空之前做完，
`probe.json` / `导出-vN.md` / 全部 `*.jsonl` / `rounds/*.json` 都在 `.codex/runs/` 里）。
**下一批必做**：真跑的库也放进 `.codex/runs/`（eval 已经这么改了，见 `eval/eval.db`）。

**完整 eval（C 组，欠了自 P16）**：**7/8 通过**，日志 `eval/eval.log`。
唯一一条红是 **`edge-contradiction` → `contradiction_is_labelled`**：真·逻辑互斥的输入
（"任何人都不许改" vs "点错了要能改"）两道题**都标成 `gap`**，没有 `conflict` / `decision`——
正是 P11 修过、P20 记 ✅ 的那条，**换模型之后退化**。**口径**：五条车道里 `conflict` 出现过 3 次
（L1/L4/L5 各 1）、`decision` 8 次，所以**不是整体失效**，是**最典型那一例退化**；
eval 属纯提示词通道，与五条车道结论不重叠。**修法另起方案**。

**下一步（等拍板）**：① F28（确认重复 remove）——阻塞交付，建议下一个做；
② F30 的 180 秒墙要不要处理；③ 第 6 章「业务流程与交互」五条车道全空；
④ 监控加 `code_drift`（看源码树，而不只是 `.env`）；⑤ eval 那条红（矛盾判据）要不要跟 F28 一起做。

## P32 · M1：确认写入的重复 `remove`（2026-10-03，**已交付**）

**拍板**：作者「给我按顺序来」——按里程碑 M1 → M2 → M3 → M4 的顺序，从 M1 开工。

**这一条修的是什么**：同一批确认里，对同一个 `entry_id` 的多条 `remove` 是**同一条指令**。
真跑现场（P30 · L4 第 3 轮）：整合者那条路（`p14`–`p21`）与"被回答过的未决项要撤下"那条机械路
（`p34`–`p39`）指向同一批 s11 条目，用户两条都勾 → `POST /confirm` 422 →
**整批接受的 45 条一条都没写**（F28）。

**改了什么（三个文件）**：

| 文件 | 改动 |
|---|---|
| `domain/round/draft.py` | **新增纯函数** `merge_duplicate_removes()`——规则的唯一出处（只合并 `remove`；`replace` 是顺序语义、`add` 天然不撞） |
| `application/assembly.py::draft_of()` | 调它：**新草案卡片上不再出现两行一样的"删除"**（这是 `use_cases.py` 那条路建草案的唯一构造点） |
| `domain/services/committer.py` | `_resolve()` 调它做**保底**（修好之前存下的**老草案**照样能确认写入）；`CommitResult` 加 `merged_removes`（带默认值，API 零变化）；**连带**：`taken` 改成按用户接受的 id 算，免得 C4 的 `proposed_accepted` 少计 |

**没改**：`document._apply_remove` 的报错语义（手改路径 `POST /edits` 依赖它）、`replace` 的顺序语义、
端点/schema/前端/提示词、`serialization.py`（老库原样读）。
**不进留痕通道**：合并掉的是重复指令不是内容（条目照样被撤下）。

**TDD（先红 → 再绿，+14 条用例，1017 passed）**：

| 层 | 用例 | 红是什么样 |
|---|---|---|
| 领域纯函数 | `tests/domain/round/test_merge_duplicate_removes.py`（6 条） | `ImportError`（函数不存在） |
| 写入端 | `tests/domain/services/test_committer.py`（+3 条） | **`UnknownEntry`**——就是真跑那个 422 的领域形态 |
| 装配端 | `tests/application/test_duplicate_removes.py`（+3 条） | `draft_of` 把两条都留下 |
| 接口层 | `tests/api/test_duplicate_removes.py`（+2 条） | 把写入端的合并临时摘掉 → 复现 **422**；装回去 → 200（**这条专门验过真能抓 bug**） |

**真跑复验（`.codex/runs/2026-10-02/M1验证/`）**：

- **live**：科研 CRF 输入第 1 轮跑通，`POST /confirm` **200**、写进 v2、就绪度 1 阻塞，**全程 0 次 422**；
  第 2 轮跑到一半被环境打断（机器夜里休眠，`answers` 那一步墙钟记成 23652 秒，进程随后消失）——
  **如实记**：`s11 条目被真的撤下` 这一条没有在 live 里看到收尾，它由上面三条确定性用例钉住。
- **真数据回放**（`replay_real_draft.py`）：拿 P30 那份真实草案（50 条）过新规则 →
  **6 个 s11 条目各被撤两次 → 合并掉 6 条（p34–p39）→ 50 变 44、重复归零**。
- **顺带量到 F30 更狠的一次**：这一轮 `integration` 6 次里 **3 次超时失败重试**，
  平均 **261.5s**、最慢 **545.3s**（P30 那次是平均 98–150s、最慢 346.7s）——M2 的输入。

**回写**：`02` §11.0.2a 补段（口径）· `04` G10 的 F28 标已修 · 本节。

**下一步**：M2（F30 的 180 秒墙）——先拍板走哪条路（抬超时 / 换模型 / 整合瘦身），
证据见 P30 报告 §三 F30 与本节最后一行。

## P33 · P32 的两条补验证（+ 一个挡路坑）（2026-10-03，**已交付**）

**拍板**：作者「可以按照推荐的来」——M0 加窄口径重试、① 续跑那条被打断的车道、
② 先 T0 再 live（零提名才另立提示词批）、顺序 M0 → ① → ②。

方案 `docs/plans/2026-10-03-P33-P32补验证-联网取证与s11撤下.md`；
证据 `.codex/runs/2026-10-03/P32补验证/报告.md`（+ `settled.json` / `probe.log` / `lane/`）。

| # | 结论 |
|---|---|
| **M0** | **已修**：`TRANSIENT_HINTS` 加一条**很窄的** `"response_format type is unavailable"`——供应商那句"暂时不可用"按抖动重试。真跑里生效（报错详情 `attempts: 3`，修前只试 1 次就判死）。用例钉两头：真报错判真、**别的 `invalid_parameter` 仍判假**（含 glm 那条 `enable_thinking` 互斥——闸门靠它拦"换模型忘改参数"） |
| **①** | **已收口**：续跑那条被机器休眠打断的车道 → 草案里 **6 条 `remove` 指向 s11 条目**（整合者那族 + 机械清理那族都在）→ **confirm 200** → 那 6 个 `entry_id` **一条不剩**。三条判据全 PASS（`settled.json`） |
| **②** | **部分达成**：分析者提名 2 条 → 复述卡勾 1 条 → 真 Tavily 抓 5 条 → 材料落库（`origin=web_search`）→ **账本出现 `external` 编号**（实测 `E5`/`E6`）；**但整合者一条都没引用**，导出里没有"外部材料"。不当缺陷报，记成观察 |

**我犯的两个错（都留档）**：

1. **碰了工作库**。T0 探针脚本没覆盖 `PMSTUDIO_DB`，而 `Settings.from_env` **不解析相对路径**
   （`.env` 里是 `data/pmstudio.db`，按 CWD 解析 → 正是工作库；`eval.py` 是靠 `replace(db_path=…)`
   才躲开的）。**主库文件一个字没变**（mtime/md5 都是 09-28），写入落在 WAL 里；
   已取证（WAL 里只有我那个项目）→ 把 `-wal`/`-shm` **移走**（不是删）→ 复原并核验。
   证据留在 `working-db-forensics/`。脚本已改成 `replace(db_path=…)` + **拒绝工作库**的闸。
2. **把两件事混成一件**。我起初把"供应商拒收 `json_schema`"一律判成**抖动**——只对一半：
   `qwen3.8-27b` 是**真抖动**（08:15 一次、08:17 起 8/8 过），而作者 08:05 换上的
   `deepseek-v4.1-flash` 是**稳定拒收**（400 ×2，同模型 `json_object` 正常）。教训：
   **换过的模型要一个个测**。这一半归 **M2b**（回落 `json_object`），本批没做。

**顺带核查（作者问的"断点续跑是不是产品能力"）**：**是产品能力**，证据四处——
`Round.resume()` 按 `failure.at_step` 恢复；`RetryRound` 注释原文"**继续这一轮**：失败就先
`resume`，没失败就直接接着跑（关页面 / 进程崩了以后，轮停在 `seats_running`…）"；
`tests/e2e/test_resume.py` 两条"**换一个进程**接着跑"e2e；`RESUME_ROUTES` 把
`seats_running → seat_round`。**缺的是审计驱动**：`drive.py` 原来只在 `step == failed` 时点
`/retry`，半途状态会被 park 成"未知等待态"。**本批补上**（工具改动，产品侧不动），
并在 live 里验过（驱动打印"轮停在 seats_running（不是失败，也没有等待卡片）→ 点一次「继续」"）。
真正**没做**的那条（`04` E3 明确不做）：进程崩在**单次调用内部**的续跑。

**里程碑更新**：M2 扩成"模型层：吞吐 **+ 兼容**"（新增 **M2b**）；
M3 扩成"提示词通道：该说的没说"（新增 **M3b** 联网提名太保守）。

**下一步**：M2（先拍板：抬超时 / 换模型 / 整合瘦身，以及 M2b 走不走回落）。

## P34 · M2：模型层（抬超时 · `json_schema` 回落 · 整合瘦身先调研）（2026-10-03，**已交付**）

**拍板**：作者「支持」——按推荐默认：`DEFAULT_MODEL_TIMEOUT` 保持 90、回落**记住**供应商、
瘦身阈值 15%；并要我自己把整合瘦身的实验做出来再决定。

方案 `docs/plans/2026-10-03-P34-M2-模型层-超时兼容与整合瘦身调研.md`；
调研证据 `.codex/runs/2026-10-03/M2调研/`（`报告.md` / `实验.json` / `实验.log`）。

| 组 | 结果 |
|---|---|
| **M2a** | `.env` 超时 **180 → 300**（真跑：integration 平均 148s、`seat_review` 撞过 181s）；`.env.example` 说明同步并附数据；**代码兜底 90 不动**。代价：最坏一次动作 9→15 分钟才报失败 |
| **M2b** | `json_schema` 被**稳定**拒收时**回落 `json_object`**（同一次立刻换写法重发，不退避）+ **记住这家供应商**（后续不再先撞 400）。**契约不变**：`response_format` 只管"怎么开口要 JSON"，"输出必须过 schema"仍由本地解析与校验强制。**别的 `invalid_parameter` 不回落**（glm 的 `enable_thinking` 互斥仍立刻失败） |
| **M2c** | **结论：不瘦**。实验（同一份 20,016 字符的真实整合提示词，只压 `## 输出 JSON Schema` 到 17,033 / -14.9%，各 3 次）：耗时均值 **153.9s → 126.0s（-18.1%）**，**但** ① 两组区间重叠（[146.5,161.4] vs [104.4,153.4]）；② **输出也同向短了 10.3%**（而"耗时跟输出走"早有数据）；③ **质量没测到**（6/6 回包是 JSON 数组 = F29 形态）。判据没被真正满足 → **不动提示词** |

**顺带挖到的一条**：6/6 直接把整合提示词发给模型都回**数组**——生产里那条"校验失败 → 带错误提示
重装"不是可有可无，它在这条路上**是常态**（也解释了整合为什么慢：第一次尝试常常白跑）。

**TDD**：M2b 新增 5 条用例（回落成功 · 记住之后不再先撞 · 别的 400 不许被吞 · 留样两行 ·
回落也失败仍按抖动退避）。**改了一条旧断言并说明错在哪**：M0 那条用例原本钉 `slept == [1.0]`
（钉的是**机制**：走抖动重试 + 退避）；M2b 之后这条错**先由回落接住**（立刻换写法、不退避、更快），
所以那一行不成立。**行为断言一个字没放松**（"一次拒收不判死、第二次要成功"照旧），
抖动退避的覆盖**挪到**新用例 `test_gateway_backs_off_when_the_refusal_keeps_coming`。
全量 **1025 passed** · `ruff` 全绿 · 结构守卫未动。

**下一步（等拍板）**：① M3（提示词通道：矛盾判据退化 + 联网提名太保守）；
② M4（第 6 章五条车道全空）；③ 可选：**输出那一侧**的口径（"一轮最多 60 条"）——真正影响耗时的那个。

## P35 · M3：判据的取样面 + 联网提名（2026-10-03，**已交付**）

**拍板**：作者「开始吧」——严格按前面给出的分析清单实现：M3a 走 A 案（补只读投影 + 判据看整轮，
**不动提示词**）；M3b **先量后改**（按预注册判据决定动不动 `analyzer.md`）；
顺带查出的 F33 先量不修。

方案与结果 `docs/plans/2026-10-03-P35-M3-判据取样面与联网提名.md`；
证据 `.codex/runs/2026-10-03/M3验证/`（`eval-full.db` / `eval-full.log` / `verify_p30_round.py`）
与 `.codex/runs/2026-10-03/M3调研/`（`nomination.json` / `probe.log` / `live2.log` / `max_tokens.log`）。

| 组 | 结果 |
|---|---|
| **T0（诊断）** | 那条红**不是提示词退化**：P30 的 eval 库还在（`r-8889fcf0`）——分析者判出互斥 `c1↔c3`、整合者**第 1 批**出了 `q4 kind=conflict`（依据 E1+E3），用户跳过之后当前批只剩两道 `gap`，而判据只读 `RoundView.questions`（当前批）→ **假红**。`analyzer.md` 的矛盾段与联网段从引入到 HEAD **一字未改**（逐提交切片哈希一致） |
| **M3a** | `RoundView.asked_questions`（`questionnaire_history` 逐批展开，与 `answer_sets` 对称）；`contradiction_is_labelled` 改看整轮。**端点仍 26、payload 未动、老库照读**。P30 payload 回放：旧取样面 FAIL、新取样面 PASS |
| **M3b** | 判据没坏：3 输入 × 2 次 → 正控 2/3 条、**L4 CRF 原文（P30 那次 0 提名的输入）3/3 条**、负控 2/0 条 → **不改提示词**，只补用例 `09-edge-external-facts` + 断言 `web_queries_at_least`（只挂这一条）。live 打通：提名 3 → 勾 1 → Tavily 5 条 → 材料 4 份带 `E21–E24` 与 URL → 走到草案卡 |
| **F33（新）** | 同轮跨批**近义**重问：q5「…审计日志的留存期限…」vs q7「…**关联**审计日志的留存期限…」→ 草案里 p10/p12 两条几乎一样。去重身份是 `(去处, 归一问法)`，措辞换了不认；§11.0.16 早写着要语义判重（归记忆本体）→ **先量不修** |
| **F34（新）** | **输出默认被截断在 8192 token**：整合回包 21.7K 字符断在半句上（`finish_reason=length`、`completion_tokens=8192`）→ 不是合法 JSON → 整轮失败、重试同样失败。杠杆是一行**配置**（`PMSTUDIO_MODEL_PARAMS` 加 `max_tokens`），不动代码；`.env.example` 已写清 |

**顺带补的两处工程配套**（都算"核验"的一部分，不是加戏）：
① 网关把**回包不是 JSON / 空回包**也留一行（原文头部 + `usage` + `finish_reason`）——
原来这两条路一行都不落库，出问题只能猜；
② eval 驱动失败时**读回视图并点一次继续**（最多 2 次，日志 `[重试]` 可见，不静默）——
与 P33 那条"产品支持续跑，缺的是驱动"同一个道理。

**TDD 与验收**：新增 7 条用例（装配/断言/网关），先红后绿；
后端 **1032 passed** · `ruff` 全绿 · 前端 `tsc --noEmit` + **178 passed** · **端点 26 不变** ·
工作库 mtime 仍是 `2026-09-28 14:31`。
**整轮 eval 9/9**（P30 那次 7/8；161 次调用 / 0 重试 / 0 崩溃 / 25 分 17 秒；
`integration` 平均 35.3s、最慢 60.8s）。

**下一步（等拍板）**：① **`.env` 要不要把 `max_tokens: 16384` 写进去**（不写的话长输入在这个模型上
每轮都会撞 F34）；② M4（第 6 章五条车道全空）；③ 输出那一侧的口径（`LIMITS_TEXT` 的"一轮最多 60 条"）。

## P36 · M4：第 6 章「业务流程与交互」（2026-10-03，**已交付**）

**拍板**：作者「按照推荐的来」——**口径 A**（给 s6 派活）、**不升 blocker**、
`.env` 的 `max_tokens` 作者自己加了。计划里的第 4 项（导出把 steps 按 `1.` 编号）**执行时被证据推翻、撤掉**。

方案与结果 `docs/plans/2026-10-03-P36-M4-第6章端到端流程.md`；
证据 `.codex/runs/2026-10-03/M4验证/`（`eval-e2e.*` / `two_rounds*.log` / `eval-full*.log` /
`导出-两轮后.md` / `probe_two_rounds.py`；R2-d 的整轮库是 `eval-final.db`）。

**三问的答案（都带证据）**：

| 问 | 答 |
|---|---|
| 谁该写它？ | **跨功能/端到端**流程 + 异常路径 → **s6**（章节级 `kind=steps`）；功能**内部**路径 → `s5.main_flow`。P30 五条车道 14 轮 449 条提案里，`kind=steps` **13 条全落在 `s5/*.main_flow`**，落 s6 的只有 1 条且是 `text` |
| 没人写还是留不住？ | **没人派活**。`_kind_for` / `_apply_add` / `_validate` / 前端 `EntryList` 都支持章节级 steps；归位表把"主流程"整行推给了 s5，`LIMITS` 从头到尾没提 s6 |
| 要不要升 blocker？ | **不升**（`01` §4 自己定的就是 warn；P30 里 L1/L5 刚到 0 阻塞；"流程走得通吗"是语义判断） |

**改了三处**：`analyzer.md` 归位表加 s6 行 + 分工说明；`integrator.LIMITS_TEXT` 加 s6 写法
（含"**kind 必须写 steps**"，见下）；`deliverable._empty_note` 只取**结构性**缺口的 detail
（原来把写给模型的"这类内容用 steps 块写；拿不准就出题问用户"漏进了交付物）。

**执行中的一次迭代**：第一个整轮 eval（收紧前）**10/10**，但落 s6 的 4 条**全是 `text`**
（真流程、kind 写错）→ 判据不认、前端也不按步骤排。收紧措辞（"kind 必须写 steps
（写成 text 就只是普通正文…）"）并加结构用例之后重跑。

**真跑结果**：

- 新用例 `edge-end-to-end-flow`：**PASS**（s6 收"端到端"+"断网异常"两条、`s5/*.main_flow` 收 5 条，不重复）。
- L1 两轮 + 导出（跑了两次）：**两次第 6 章都非空、warn 都消失**；落点在第 1 轮或第 2 轮不同——
  并列式输入要先有第一轮的文档，第二轮整合者才把跨功能那条链串进 s6（这正是"两轮"的由来）。
- 整轮 eval（跑了四次）：**10/10**（初版）→ **9/10**（提示词收紧后，那条新用例红了一次）
  → **9/10**（再收紧，仍写 text）→ **10/10**（加机械盖章后：190 次调用 / 0 重试 / 0 崩溃 /
  40 分 59 秒；落 s6 的 5 条**全是 `steps`**）。
- 新用例 `edge-end-to-end-flow` 在盖章之后**连跑 3 次 3/3 PASS**。

**TDD**：新增 6 条用例（提示词 2 · 导出 1 · 就绪度护栏 1 · 装配盖章 1（红→绿）· eval 断言 1）
+ 新用例 `10-edge-end-to-end-flow`；后端 **1038 passed** · `ruff` 全绿 · 端点 26 不变 · 工作库未碰。

**下一步（等拍板）**：① M4 之后再量一次 `main_flow` 的覆盖率（现在是非必填，设计说明书 §2.6 写的是必填）；
② 输出那一侧的口径（`LIMITS_TEXT` 的"一轮最多 60 条"——真跑一轮累计 93–103 条提案是
**两批各 ≤60 累加**的结果，不是超限）。

## P37 · 主流程覆盖率 + 输出侧口径（2026-10-03，**已交付**）

**拍板**：作者「一跟二都要做」。两件都**先量再决定**——量完都**不动结构**。

方案与结果 `docs/plans/2026-10-03-P37-主流程覆盖率与输出侧口径.md`；
测量脚本与证据 `.codex/runs/2026-10-03/P37调研/`（`measure_main_flow.py` /
`measure_main_flow_drafts.py` / `measure_output.py` / `eval-p37.log` / `two_rounds-L5-again.log`）。

| 项 | 量出来的 | 决定 |
|---|---|---|
| **① 主流程覆盖率** | 历史 45 份导出 / 281 个功能 **48%**；当前配置三条车道各两轮 **62%**（L1 4/4、L2 4/8、**L5 0/1**）；草稿层 eval **50–54%** | **不升必填**（升上去等于给每个缺主流程的功能加一条**阻塞**，P30 的 0 阻塞车道会破；"有没有流程可写"是语义判断）。改成**提示词派活**：每个功能的正常路径要有一条 `main_flow`，看不出步骤就出题、别硬编 |
| **② 输出侧（那 60 条）** | 201–204 次成功整合：条数中位 **31** / p90 65 / 最大 90；回包中位 **17.9K 字符**；耗时中位 **44.2s**；**17% 的调用 >60 条** → 先重装（59% 修好）、其余**真截断**（12 条留痕，最多丢 16 条） | **60 不动**（抬到 90 会撞 16384 输出上限；降到 40 把溢出面推到 ~40%）。只**把措辞改准**：一次整合最多 60 条 + 说清后果 + **正文只写文档里那句话，解释进 `rationale`** |

**真跑验证（同一套 10 条用例，前后对比）**：**10/10**；② 的回包字符中位 17,881 → **15,717（−12%）**、
整合耗时中位 53.0s → **48.1s（−9.2%）**、溢出 14% → **11%**（幅度小、样本有限，如实记）。
**① 没有可测提升**：草稿层 54% → **53%**、L5 两轮仍 **0/1**——诊断是那条输入的**分析者**
没切出流程主张（缺口在上游 analyzer），下一步与触发条件写在 `02` §11.0.43。

**TDD**：2 条结构用例先红后绿；后端 **1040 passed** · `ruff` 全绿 · 端点 26 不变 · 工作库未碰。

## P38 · 主流程：把"要写"送到模型眼前（2026-10-03，**已交付**）

**拍板**：作者「按照推荐来」——**只做 `main_flow`**（不顺手加 `priority/user_story/io`）、
**继续不升必填**、真跑**同时跑 L2**（多一个证伪点）。

方案与结果 `docs/plans/2026-10-03-P38-主流程派字段.md`；
证据 `.codex/runs/2026-10-03/P38验证/`（`two_rounds-L5.log` / `two_rounds-L2.log` / `eval-p38.log`）。

**改了两处（都只改提示词）**：① `context.FIELD_PRESSING` —— 字段表里 `main_flow` 那行
写"**每个功能都要写：这个功能怎么走一遍（1. …；2. …）**"；② `analyzer.md` 归位表加一行
（**功能怎么走一遍 → `s5 + feature_slug + field: main_flow`**）。`readiness` / `schema.required`
一个字没动（加了护栏用例钉住"缺主流程不挡交付"）。

**真跑结果**：功能级主流程覆盖率 **62% → 67%**（**L5 0/1 → 2/3**、L2 4/8 → 2/5、L1 4/4）；
**三条车道的第 6 章全部非空**（L5 收到 3 条流程：7 步 / 3 步 / 6 步）；整轮 eval **10/10**。
**诊断（重要）**：L5 第 1 轮的主张里**仍然 0 条 `main_flow`** —— 写出那两条的是**整合者**
（`FEATURE_FIELDS_TEXT` 两个智能体共用，字段表那句它也读得到）。

**遗留（等拍板）**：分析者仍不派字段。要么在归位表里要求"每个功能合成一条流程主张"
（**撞 `claims` 上限 20**，L2 那轮已经用满），要么认下"s6 就是流程的家"、把指标改成
"**文档里有没有 steps**"（`01` §4 第 6 章的原话正是"有功能却没有任何 `steps` 条目 → warn"）——
后者要改判据口径，得单独拍板。

## M6 · 跨轮记忆本体（2026-10-03，**已交付**）

**拍板**：作者「直接按照推荐来」——做，分三步走（M6a / M6b / M6c，每步可停）。
方案 `docs/plans/2026-10-03-M6-跨轮记忆本体.md`；T0′ 重测 `.codex/runs/2026-10-03/M6调研/`。

**先摆过的事实**（口径核对，改掉一条旧结论）：`04` G8 / `02` §11.0.39 原写"记忆本体仍不做"，
触发条件是"跨轮重问 ≥3 条/项目 或 被拒回流 ≥2 条/项目"。用 P28 那支脚本重测 **27 个库**：
跨轮重问（同问法）**0**、跨轮重问（跳过=欠账）37（正确行为）、被拒回流 **0**、
s11 答案蒸发 **0**（P28 已修）、近义上界 1 —— **触发条件仍未满足**。
但那一 1 条是真的：`m1.db` 第 1 轮问过答过「数据提交后冻结与录入纠错的矛盾如何调和？」，
第 2 轮又问「…调和机制」（同去处、同一件事，三字组 0.46/0.50，机械键抓不到）；
同一份 payload 里还有 4 对同轮近义重复的 s11 清单项。**所以做成能力投入，只做语义侧。**

| 批次 | 内容 | 结果 |
|---|---|---|
| **M6a**（`54c054f`） | `ProjectMemory` 聚合（60 条 / 每条 ≥1 个真 E / 三条不变量 / 只增 revisions）+ 新表 `project_memory` + `USER_VERSION 2→3` + 序列化 + 仓储 + `WorkspaceView.memory` | 零模型、零提示词、端点不动；老库自动补表 |
| **M6b**（`47aa3af` + 前端 `a3104d1`） | `MemoryCurator`（新 purpose `memory_curation`）+ `memory_curator.md` + 草案卡一档（默认不勾）+ `/confirm` 的 `accepted_memory` + `DELETE /projects/{id}/memory/{id}`（26 → **27**）+ 前端记忆抽屉 | 记忆与文档**同一个事务**；Curator 挂了只留一行、不判死 |
| **M6c**（`7734726`） | 注入席位与整合者（`$project_memory`，限量 20 行，空态一句实话，**冲突以正文为准**） | 分析者**不注入**（唯一没有 /retry 的路） |

**两处刻意的不一样（核验里改的）**：
① 记忆的失败/被筛留痕走 **`Draft.gaps`**，**不走 `Round.dropped`**——后者是"席位意见的交代账"，
是 `unaccounted` 指标的分子，混进去会污染那条指标（`test_dropped_trace.py` 当场抓到）；
② 删一条记忆给 **404**（新 code `unknown_memory`），不是 422——"资源不在"不是"形状不对"。

**动了三处测试（都是登记册，不是放宽判据）**：端点白名单 26 → 27、`RoundCommand` 白名单加
`accepted_memory`、`test_llm.py` 的提示词槽位表加 `memory_curator`；另加 `RecordingPrompts.last(name)`
（一轮里不止一个智能体在渲染，`calls[-1]` 不再等于整合者那次，两处断言改成按名字取）。

**核验**：后端 **1082 passed** · `ruff` 全绿 · 端点 **27**（`/schema` 不动）· 前端 **182 passed** + `tsc` 干净 ·
工作库 mtime 仍 `2026-09-28 14:31`。

**2026-10-03 真跑（已跑，M6d/M6e/M6f 三个根因都是它跑出来的）**：计划与预注册判据
`docs/plans/2026-10-03-M6验证-真跑计划.md`；证据 `.codex/runs/2026-10-03/M6验证/`。

| 根因 | 现场 | 处置 |
|---|---|---|
| `.env` 模型参数被 provider 拒 | `glm-5.3` 上 `enable_thinking:false` → 每次调用 400 | 用进程级 `PMSTUDIO_MODEL=openai/qwen-plus` 跑，作者 `.env` 未动 |
| 模型不写 `refs`（M6d 提示词+补问都无效） | 连提 4–5 条、内容全对、`refs` 整栏省掉 | **M6e**：出处由装配层按本轮账本机械回填（只用本轮真编号，绝不编）；"写歪了"才补问一次 |
| 记忆 id 泄漏（M6f） | 席位引 `mem-12565550` / `r-…#09` → 整趟席位失败 | 注入席位/整合者的那份**不带 id**；id 只给整理者（`render_memory_state`）；加"括号里的 E… 才是出处编号"硬规矩 |

**真跑数字（L1 两轮，M6f 代码）**：Curator **1 次/轮**（中位 6.9s）· 记忆 5+3 条 ·
第 2 轮**席位提示词命中第 1 轮那 5 条 = 5/5** · 两轮 **38 次调用 / 0 重试** ·
`integration` 中位 100.8s（⚠️ 与 P38 的 48.1s 不可比——不同模型）· 导出第 6 章空、
阻塞 9（L1 这条车道的老问题，与 M6 无关）。L2（改前代码）留着当对照：它那趟席位因
引用 `mem…` 触发过 1 次重试。
**eval 10 条**：跑到第 9 条时本轮结束（通过率在 `eval-m6.log`；跑完补回写）。
**探针自身两处判据缺陷**：留样只存 20k 头尾各半 → 整合者那一路的命中**测不了**（靠离线用例钉）；
"最终记忆集 ≠ 第 2 轮注入集"（第 2 轮 Curator 又会改），比对要用上一轮卡片原文。

## M7 · 附录 A 不再列"未决清单的回声" + 未决条目文本清理（2026-10-04，**已交付**）

**拍板**：作者「按推荐的来」——① B 的口径（附录只列非未决清单引用）② **B-i**：正文里 s11 条目
照样挂编号（系统里可查）③ A′ 只剥内部地址 + 台账话、**保留**席位倾向 ④ 接受"A′ 不减条数"
⑤ 老文本只在**新写入**时生效（不重写历史）。
方案 `docs/plans/2026-10-04-M7-附录A排除未决回声与未决条目清理.md`；
根因报告 `.codex/runs/2026-10-03/附录A调研/报告.md`；验证 `.codex/runs/2026-10-04/M7验证/报告.md`。

**为什么做**：根因调查（`附录A调研/`，5 个只读脚本）查出——**附录 A 47–95% 的行是"未决清单
（s11）的回声"**：正文里每条"还没定的事"都自带一个 `系统默认` 编号，附录再原样列一遍；
而 s11 本身占正文 **37–52%**（第 1 轮 33–45 条 → 第 2 轮 61–87 条，每轮净增 20–49 条）。
**主因不是重复**（机械可测只有 16–19%），是**产量 ≫ 容量**（一轮产 27 题、只有 10 个名额）。

| 提交 | 内容 |
|---|---|
| `e2468fd` | **B**：`iter_section_entries` + `cited_evidence_ids(skip_sections={s11})`；附录 A 排除"只被未决清单引用"的编号，末尾两行说明；顺带把"未决清单在第 11 章"收成 `sections.OPEN_QUESTION_SECTION` 一处（汇编/就绪度/导出共用） |
| `b4a0e1f` | **A′**：`assembly.strip_process_notes()`——`open_item_proposal` 落文本前剥、`open_item_keys` 与 `unlanded_open_questions` 算判重身份时也剥 |
| `0aed8a3` | **A′ 提示词**：`_render_open_items` 的地址写成「条目地址 <id>」+ 说清"只填 `entry_id`"；`integrator.md` 与 `LIMITS_TEXT` 写明"标题**只写问题本身**、地址不许进正文" |
| `9106912` | **修正**：台账话只认**两个结尾整形态**——真跑回放抓到第一版剥过头两处（`…留待下一轮。`、`（open71 留待下一轮），需在后续轮次补齐。`） |
| 本批 | 回写 `01` §7.3 / `02` §10 + §11.0.10（M7 补段） |

**真跑取证（4 条真车道、真库副本重导出、逐条对账）**：全文 **−12~−26%**；
附录 A **119→67 / 129→67 / 26→0 / 37→14 行**（−47%~−95%，M6-L1 那份 13 章全空、只剩说明）；
**被正文引用过的编号丢失 0**、**未决回声混进附录 0**、正文与附录 B/C 逐字节一致。
A′ 用真跑原文回放：L5 **14/82**、L2 **20/83** 条标题会被剥（都是台账话与地址），M6 L2 **0** 条。

**如实记两条**：① **A′ 不减未决条数**（实测：剥完后"完全相同"的重复仍是 0；能抓到的只有
"前 12 字相同"的 16–19%，而那会误合两个不同的问题）——它买到的是**可读性与纪律**；
② `（open71 留待下一轮）` 这类"地址掺在别的话里"的**旧写法仍会留在正文**（剥它要动括号里的真内容，
宁愿不剥；新写入不会再有）。

**核验**：后端 **1103 passed** · `ruff` 全绿 · 新增 **15 条**用例（含两条反例/护栏）·
端点仍 26 · `/schema` 与前端零改动 · 账本与落库结构一个字没动 · 工作库未碰。
**没做（下一轮候选）**：C（产量/容量，治本）、D（导出按去处聚合渲染）、B-ii（正文也不挂编号）、
语义判重（仓库口径"相似度不做"，要动得单独拍板）。
