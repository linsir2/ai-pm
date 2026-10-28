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

**v1 全闭环 + 外延能力都已交付**（截至 2026-10-08，里程碑 P1–P38、M1–M13）：
主闭环（输入 → 复述 → 席位 → 问卷 → 草案 → 确认写入 → 版本）之外还有：

- **工具层**：上传 / 联网检索 / 材料检索 / 删除重解析 / `.docx`·`.xlsx`·按网址抓网页（P17–P27）
- **交付闭环**：就绪度六判据 + Markdown 导出（P5a）；版本历史 / diff / 回退（P5b）
- **索引层**：`GET /projects/{id}/search`（条目 / 功能 / 编号 / 席位发言 / 材料块，P16/P18）
- **跨轮记忆**：只存"确认过的那一块"，用户逐条确认（M6）
- **探索（M8）**：调研一趟 → 过目台 → 收下为种子（下一轮输入）或材料（`simulated` 出处）
- **讨论（M9）**：自由主题的 project 级线程 → 3 固定灵感角色或任一领域席位 → 总结成卡 → 收下为材料
- **模拟调研（M10）**：草案卡上的轮内动作——推演处境切片/假设/盲区 → 勾选收下为材料
  （`origin=simulation`，下一轮带上，引用它的提案默认不勾；`02` §11.0.47）
- **模拟评测（M11）**：草案卡"减"动作——5 个固定 persona 走查已确认文档的功能 →
  R1–R3 机械减建议（remove/replace）**追加进本轮草案**（默认不勾）→ 确认写入（`02` §11.0.48）
- **灵感箱（M12）**：第二页签从「过目台」升级为统一过目箱——讨论卡与调研候选同一处过目
  （收下为下一轮输入/材料、忽略、聊聊），未处理数徽标；箱是视图不是存储（`02` §11.0.49）
- **把手与调研入口（M13）**：选区把手「就这段聊聊」+ 条目工具行「讨论」（`02` §11.0.51）；
  开聊卡「先跑一轮调研」（假设当开题底稿注入讨论，`02` §11.0.50）；灵感卡「拿去验证」。
  同批收口技术债：记忆抽屉 `.prop` 布局、turn 上限进 `/schema.limits`、问卷题快照断线上线、
  G10① 销账、03 满轮 ⬜ 真跑补记
- **P3a 联网申请（2026-10-26）**：分析者/席位/整合者的联网申请统一进 `Round.web_requests`，
  逐条授权、批准即执行；旧 `web_queries*` 只读迁移。`RoundRunner` 的进程内串行仍在，
  跨 worker 的 in-flight / 条件 claim 留 **P3b-1**；P3b-1 同时收历史趟 TOOL 留痕可见性、
  stale id 无工具 422、`used>limit` 恢复口径。
- **PG 全量切换（T10，2026-10-28）**：运行时/测试/脚本**只连 PostgreSQL 18 +
  pgvector + pg_search**，backend 走 PgBouncer transaction mode，init/maintenance/backup
  直连。SQLite 包、`sqlite-vec` 依赖、旧 SQLite 脚本、一次性 ETL 脚本与其测试**已删**；
  no-sqlite 守卫（AST import + 非 docstring SQL 字面量）**无白名单**。4 workers 负载门
  通过（100 HTTP + 20 轮内写，0 错误/超时/55P03/57014，HTTP p95 88ms；DB 连接池查询
  p95 43ms 仅诊断）。
- **pg_search BM25（2026-10-28）**：官方 ParadeDB 0.26.3-pg18 镜像；`0002_pg_search.sql`
  建 `search_text` / generated `search_tokens` / `pmstudio_tokens`（jieba）/ bm25 索引；
  检索是 200 个 pg_search 候选 → 全语料 Robertson BM25 重排（IDF 钳 0）→ 50 进融合，
  再与 pgvector KNN 按 α=.4、每材料 2 槽融合。冻结语料验收入口
  `backend/scripts/retrieval_quality_bench.py`（fixture 在 `tests/fixtures/retrieval/`）：
  300/791 跨块 `4/15=.267`、400/646 跨块 `7/15=.467`；词面 r5 `.844`、改述 r5 `.857`。
  **排序债仍在**：IDF 钳制后 top1/MRR 低于旧 300 目标，见 `04` §0.1 R15，不改阈值。

- 后端：测试全绿、`ruff` 无告警；结构性守卫在 `tests/architecture/` 与 `tests/contracts/`，
  含"守卫自己的判据有没有效"那一层。守卫条数与测试条数**都会过期**，这里不写数字——
  **要查就跑一次 pytest**。
  里程碑计划与执行记录见 `docs/plans/`（最新：`2026-10-08-M13-技术债与把手.md`）。
- 前端：`frontend/`（React 18 + Vite 6 + TS strict），vitest 全绿、`tsc --noEmit` 无输出；
  界面设计见 `.codex/docs/设计/06-前端.md`。
- 真模型证据与逐项交付记录在 `.codex/docs/设计/04-未决.md`（各条都有日期与出处）。

**下一步**：M14（判据拆分 + 三处文档债）与 M15（项目列表界面）已完成（2026-10-09）。当前未收口的按依赖排在 `04-未决.md` §C/E 与 `07` §7.11 的后续里；
**其中 `07` §7.9 的第②层（删字段级 description）已被撤回**——`$schema_json` 把整份 schema
当正文内联进提示词，字段描述是模型正在读的填表说明，删掉是净损失。
再往后的候选：`02` §11.0.45 拓展点的另一支（卡片结构化引用过目台候选）、
MCP 读/写侧。

当前状态与下一步看：`.codex/docs/上下文/checkpoint.md`
可复用的经验与坑看：`.codex/docs/上下文/learnings.md`

设计文档七份，别在别处再写一份：
`01-规格.md`（要做什么）/ `02-编排与数据.md`（怎么跑）/ `03-案例与验收.md`（怎么算对）/
`04-未决.md`（还没定的）/ `05-DDD架构.md`（怎么实现）/ `06-前端.md`（界面与怎么接后端）/
`07-记忆层.md`（记忆层与上下文组装的总览与重构基线）。

## 环境（一次装好，之后不用再 export 一堆变量）

后端配置只从环境变量读（`bootstrap/settings.py` 明令：缺 key、模型名或 app URL 必须起不来）。
写法是**文件**：`backend/.env`（app 侧：模型/key/PgBouncer URL；本地、不进库，模板
`.env.example` 进库）与 `backend/.env.ops`（运维侧：direct/bootstrap URL、角色口令、
首管理员；模板 `.env.ops.example` 进库）。`backend/scripts/dev.py` 只读 `.env`；
前端那条代理目标放 `frontend/.env`（同样有 `.env.example`）。

| 用途 | 命令 |
|------|------|
| 后端依赖 | `cd backend && uv sync`（建 `.venv` 并锁版本；`uv.lock` 进库，`.venv` 不进库） |
| 后端配置 | `cd backend && cp .env.example .env && cp .env.ops.example .env.ops` 然后填 key/口令 |
| **后端起服务** | 先起 PG/PgBouncer（或 `docker compose up -d db pgbouncer`），再 `cd backend && uv run python scripts/dev.py` |
| 后端测试 | 先导出 `PMSTUDIO_TEST_DATABASE_URL` / `PMSTUDIO_TEST_DIRECT_DATABASE_URL`，再 `cd backend && uv run pytest -m "not slow"` |
| 后端 lint | `cd backend && uv run ruff check --no-cache .` |
| 前端依赖 | `npm install`（在 `frontend/`） |
| **前端开发** | `cd frontend && npm run dev`（5173，`/api` 代理到 `frontend/.env` 里的后端地址） |
| 前端类型检查 | `npx tsc --noEmit` |
| 前端单测（纯函数） | `npx vitest run`（在 `frontend/`） |
| **Docker 一键起** | `docker compose up -d`（后端 + PG + PgBouncer + Caddy + Redis；app 读 `backend/.env`，ops 读 `backend/.env.ops`） |
| **镜像构建** | `docker compose build`（CI 也跑这一步——Dockerfile 的错**不构建一次发现不了**） |
| **无 Docker 时起** | `scripts/dev_stack.sh`（原生进程复现 compose 的启动序列；需已有可达 PG/PgBouncer；`--check` 只核对配置键名、`--stop` 收尾） |
| **检索验收** | `cd backend && uv run python scripts/retrieval_quality_bench.py --chunk-chars 300`（真 PG/pg_search；400 复现旧口径） |
| **MCP 真跑验证** | `cd backend && uv run python scripts/mcp_probe.py --builtin`（真模型 + 真 MCP server） |
| **MCP stdio 用例** | `cd backend && uv run pytest tests/infrastructure/test_mcp_client.py -k stdio`（**真子进程**，不是进程内替身） |
| 混沌矩阵 | `cd backend && uv run python scripts/chaos_matrix.py`（真注入 5 类故障） |

**部署形态**（两套，一份 Caddyfile）：

| 形态 | 怎么起 | 反代目标 | 库 |
|---|---|---|---|
| 本机 | `scripts/dev_stack.sh` | `127.0.0.1:8000` | 已有可达 PG + PgBouncer |
| Docker | `docker compose up -d` | `backend:8000`（**服务名**，容器里的 127.0.0.1 是 Caddy 自己） | compose 的 `pgdata` 卷 + PgBouncer |

⚠️ **跨容器反代必须给 `PMSTUDIO_FORWARDED_ALLOW_IPS`**——它在 `scripts/dev.py` 里
（**不在 `settings.py`**，照着 settings 抄会漏）。不给 → Caddy 后面所有请求的源 IP
都是 Caddy 的 →「每 IP 限流」退化成「全站限流」，**正常用户互相锁死**。

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
infrastructure/  LangGraph 图 · LiteLLM 网关 · PostgreSQL/PgBouncer 仓储 · 席位定义 · MCP 客户端
application/     用例（StartRound / RunSeatRound / Integrate / SubmitAnswers / ConfirmDraft）
domain/          authoring（文档与版本）· provenance（出处账本）· round（回合）
harness/         运行时控制平面（最内层）：统一信封 · 结构化输出归一化 · 失败分类 ·
                 熔断/隔离舱/重试预算 · **agentic 循环**（模型自主调工具）
```

**MCP 是只读旁路**（2026-10-22）：模型可以自主调外部 MCP server 的工具，
但它**不进主闭环、不进 API 层、不写库**。SDK 只允许出现在 `infrastructure/mcp/`，
`harness` 那边只认 `ToolInvoker` **协议**（`harness` 只许依赖
pydantic/litellm/pybreaker，见 `tests/architecture/test_harness_imports.py`）。
写侧要动"写入路径唯一"那条红线，明确不做。详见 `02-编排与数据.md §4.9`。

**三条容易踩的**（2026-10-22 审查）：
- **生产路径没有消费者**：`PMSTUDIO_MCP_SERVERS` 目前**只有 `scripts/mcp_probe.py` 读**。
  主服务起起来**不连 MCP**——`scripts/dev.py` 只打一行配置状态，不建连接。
- **`tools` 与 `response_format` 互斥**：agentic 路径**永不带** `response_format`
  （降级重试分支里也一样，见 `test_gateway_resilience.py`）。
- **调用数有上限**：`MAX_TOOL_CALLS_PER_TURN=5` / `MAX_TOOL_CALLS_PER_LOOP=12`，
  超限**照实说**（不静默截断）；`MCP_CALL_TIMEOUT` 是**每次 `tools/call`** 的上限。

DDD 分层，详细设计见 `.codex/docs/设计/05-DDD架构.md`。三条硬规矩（违反即设计错误）：

1. **`domain/` 不 import 任何外部包**（不 import fastapi / langgraph / litellm / psycopg），
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
- **别直接 `cp` 正在跑的 PG 数据目录**：备份只用 PG18 的 `pg_dump -Fc`（恢复走
  `pg_restore --no-owner`，恢复后 `TRUNCATE sessions`）；拷文件系统会拿到不一致的快照。

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
