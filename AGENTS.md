# PM Studio · Agent 上下文

> Codex 自动加载本文件。**新会话先读这里，再读 `.codex/docs/设计/`。**
> 保持精简：只写会改变判断的东西。细节放设计文档，别抄进来。

## 任务（Mission）

单用户本地 Web 应用。产品经理把自己零散的想法倒进来，系统用**领域专家席位**整理成
结构化产品文档；AI 只出草案，用户确认后才写入，每次改动产生一个版本。
第一个用户就是作者本人。

**当前范围：只打通主闭环**——输入 → 问 → 答 → 写 → 版本。
其他能力（讨论区、工具层、记忆维护、完整门禁、导出）都在设计里留位，**这一版不做**。

**非目标**：多用户与登录、AI 自动改文档、面向工程的排期管理。

**参考设计**：`main` / `redesign` 分支上有另一套完整设计（PRD / CONTRACTS / DECISIONS）
与后端实现，**只借鉴思路，不搬契约、不复用代码**。设计前先扫一眼，能省掉重复推导。

## 现在到哪一步了

**P2 主闭环已完成，前后端都跑通真模型**（2026-09-23）：
输入 → 复述 → 席位 → 问卷 → 草案 → 确认写入 → 文档多一版，三个面 + SSE 都在。

- 后端：306 条测试全绿、`ruff` 无告警、四道结构性守卫全绿；
  计划与执行记录见 `docs/plans/2026-09-22-P2-单轮闭环.md`。
- 前端：`frontend/`（React 18 + Vite 6 + TS strict），纯函数 69 条 vitest 全绿、
  `tsc --noEmit` 无输出；界面设计见 `.codex/docs/设计/06-前端.md`。
- 真模型证据（qwen3.8-flash via DashScope）：`p-3b0ad2b4` 一轮里出 10 条主张 → 6 个席位
  → 两轮问卷 → 第三轮席位失败（模型编了 `E34`）→ 点"接着跑"只补没交回的席位。

**下一步：P3**——恢复与循环（缩编规则、多问卷、`PROJECT.md` 投影、按需索引），
以及 `04-未决.md` 里排到 P3/P4 的那些"要新端点才能做"的界面能力。

当前状态与下一步看：`.codex/docs/上下文/checkpoint.md`
可复用的经验与坑看：`.codex/docs/上下文/learnings.md`

设计文档六份，别在别处再写一份：
`01-规格.md`（要做什么）/ `02-编排与数据.md`（怎么跑）/ `03-案例与验收.md`（怎么算对）/
`04-未决.md`（还没定的）/ `05-DDD架构.md`（怎么实现）/ `06-前端.md`（界面与怎么接后端）。

## 环境（一次装好，之后不用再 export 一堆变量）

后端配置只从环境变量读（`bootstrap/settings.py` 明令：缺 key 或模型名必须起不来）。
写法是**文件**：`backend/.env`（本地、不进库，模板 `.env.example` 进库），
由 `backend/scripts/dev.py` 读；前端那条代理目标放 `frontend/.env`（同样有 `.env.example`）。

| 用途 | 命令 |
|------|------|
| 后端依赖 | `cd backend && uv sync`（建 `.venv` 并锁版本；`uv.lock` 进库，`.venv` 不进库） |
| 后端配置 | `cd backend && cp .env.example .env` 然后填 key |
| **后端起服务** | `cd backend && uv run python scripts/dev.py`（端口读 `.env`；`--port`、`--no-reload` 可选） |
| 后端测试 | `cd backend && uv run pytest`（`src` 在路径上，pytest 配置里已经配好） |
| 后端 lint | `cd backend && uv run ruff check --no-cache .` |
| 前端依赖 | `npm install`（在 `frontend/`） |
| **前端开发** | `cd frontend && npm run dev`（5173，`/api` 代理到 `frontend/.env` 里的后端地址） |
| 前端类型检查 | `npx tsc --noEmit` |
| 前端单测（纯函数） | `npx vitest run`（在 `frontend/`） |

`scripts/dev.py` 起服务前会打一行"模型 / key / 库在哪"，配置缺了直接退出码 2——
省得等到第一次调模型才发现 key 没填；它还会提醒你"现在用的是系统 Python 还是 `.venv`"。
后端换端口时记得两边一起改：
`backend/.env` 的 `PMSTUDIO_PORT` 与 `frontend/.env` 的 `PMSTUDIO_API_TARGET`。

**不留痕跑法（作者要求：仓库里只出现源码与文档，不出现生成物）**：

| 用途 | 命令 |
|------|------|
| 后端测试 | `cd backend && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider` |
| 后端 lint | `cd backend && python3 -m ruff check --no-cache .` |

`__pycache__` / `.pytest_cache` / `.ruff_cache` 都在 `.gitignore` 里，但**忽略不等于不生成**：
用上面这两条跑才不会在磁盘上留下它们。发现又冒出来时清一次：

```bash
cd /home/linsir365/projects/ai-pm
find . -path ./.git -prune -o -type d \
  \( -name "__pycache__" -o -name ".pytest_cache" -o -name ".ruff_cache" \) -exec rm -rf {} +
```

> 目前这些命令还没有对应的代码，属于**先约定后实现**。实现时如与约定不同，
> 以实际为准并回来改这一节。

## 架构地图（详见 `.codex/docs/设计/02-编排与数据.md`）

```
frontend/  React + Vite：文档区（约 70%）+ 闭环区（约 30%）+ 弹出层（问卷 / 草案）
api/       FastAPI：HTTP 动作入口 + SSE 推送口
infrastructure/  LangGraph 图 · LiteLLM 网关 · SQLite 仓储 · 席位定义
application/     用例（StartRound / RunSeatRound / Integrate / SubmitAnswers / ConfirmDraft）
domain/          authoring（文档与版本）· provenance（出处账本）· round（回合）
```

DDD 分层，详细设计见 `.codex/docs/设计/05-DDD架构.md`。三条硬规矩（违反即设计错误）：

1. **`domain/` 不 import 任何外部包**（不 import fastapi / langgraph / litellm / sqlite3），
   用测试扫 import 来守。
2. **写入路径唯一**：只有确认端点能写文档，编排层拿不到写库能力。
3. **LangGraph 的 state 只放引用（round_id / step），不放业务状态**：真相源是 Round 聚合，
   否则图与聚合会变成两个真相源。

界面侧同样有两条（详见 `06-前端.md` §3.2）：

4. **事件只推进进度与"该重读了"**：文档与轮次的真身一律从后端读回来，reducer 不拼数据。
5. **组件不做业务判断**：能不能提交、该弹什么、默认勾什么，全在 `payload.ts` /
   `status.ts` / `progress.ts` 这些纯函数里。

## 四条不变式

1. **AI 不写文档**：编排层只产出待写入清单。
2. **统一入口不变式**：进文档的信息（首轮输入、问卷答案、将来的外部导入）都走
   切分 → 定性（七种标签）→ 人确认。
3. **先有编号再有内容**：每条内容带出处编号，无编号只能进开放问题。
4. **可追溯不变式**：顺着 `refs → derived_from` 回溯必须至少到达一条用户输入或问卷答案；
   到不了的只能作为"席位提议"出现，默认不勾选。

## LLM 契约

- 智能体之间只传**信息包**（统一信封），不传自然语言；输出必须过 schema。
- **席位只出一件事：专业意见**（疑问 或 判断，各带依据与理由）。把意见组装成 schema
  形状的内容候选与题目，是 **Integrator** 的活。职责边界：席位提供内容，整合者提供形状。
- **空意见不等于没意见**：必须给 `basis`。席位在 v1 **不能新增 feature 子树**。
- **问卷不是必然环节**：没有必须问用户的东西就整段跳过，直接出草案。
- 答案回来后**重新组装上下文、再跑一次缩编的席位轮**（不是把选项消费掉）。
- 内容分三种来源：用户说的 / 它判断的（必须带 derived_from + 理由）/ 它提议的（无依据，
  默认不勾选）。
- 详细字段与流程见 `.codex/docs/设计/02-编排与数据.md`。

## 已知陷阱

- **假实现只许活在测试里**。生产路径（bootstrap / api）不提供"用假模型"的开关，
  不写"无密钥降级"的分支。汇报"跑通了"必须给出**真实模型**的证据；
  只有假模型跑通不算完成。这条是作者明确提的要求。
- 不引入额外黑板：Round 聚合就是有类型的黑板，再放一份就是两个真相源。
- 事件只做通知与投影：**摘掉所有订阅者，流程照样能跑完**；否则流程逻辑被藏进了事件里。
- 通用角色编制（分析/质疑/写作/审阅）面对任何领域都会问同一批问题，**席位必须领域化**。
- 门禁条款别写成形容词（"认真考虑过"），机器判不了的条款最终会退化成永远通过。
- 席位失败静默降级，会让文档悄悄缺一整个专业面；失败必须是失败。
- 卡片不能编辑内容（编辑归文档），否则要回答"卡片改的和文档改的谁优先"。
- 一次改动 = 一版；版本只增不改。
- **界面不造后端给不出的东西**：没有的端点就不做那个功能，别用假数据填上（`06-前端.md` §1 与 §5
  是逐项核对的账）。
- **别用 `cp` 拷正在跑的 SQLite 库**：数据可能还在 `-wal` 里，拷出来会少东西。要备份就用
  `sqlite3 库文件 ".backup 目标文件"`（这条是验证时真踩过的：拷出来的库少了整个项目）。

## 验证阶梯（从便宜到贵）

1. 领域层纯函数单测（无网络、无模型）
2. 编排层用假模型跑：给定输入 → 断言出哪几张卡 → 给定回答 → 断言待写入清单
3. 端到端：一次完整迭代（输入 → 复述 → 问卷 → 草案 → 确认 → 文档多一版）
4. 界面手测：三个面 + SSE 推送 + 失败可见

## 边界

- 不提交密钥；模型配置只从环境变量读。
- 未实现的模块**不建桩、不建空壳**，用显式缺失清单表示。
- 改动设计文档时同步改 `.codex/docs/设计/`，不要在别处另写一份（双真相源是本项目明令禁止的）。

## 技能路由

| 场景 | 用什么 |
|------|--------|
| 设计评审 / 架构与数据流评审 | `gstack-plan-eng-review` |
| 界面与视觉决策 | `design-consultation`（本仓已有 `DESIGN.md`） |
| 上下文整理 / 交接 / 存档 | `context-engineering` |
| 实现前写计划 | `writing-plans`；执行用 `executing-plans` |
| 缺陷定位 | `systematic-debugging` |
| 声称完成前 | `verification-before-completion` |
