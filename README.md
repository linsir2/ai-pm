# PM Studio

把 PM 的自然语言构思，逐步收口成双方可确认的**产品共识文档**。单人使用，本地运行，无登录、无多租户。

## 现在到哪了

**R0（契约与骨架）＋ R1（最小生成闭环）已完成**：R1.1–R1.5.1 已落地——建项目 → 开轮 →
理解卡 → 填充卡 → 事务写入 → 文档多一版，全程 SSE；没有 API Key 时走 `FakeHarness`
（`pmstudio/harness/fake.py`）的同一条链路。

- 已实现（11 个模块）：M8 / M9 / M11 / M12 / M13 / M20 / M22 / M24 / M26 / M27 / M28
- 未实现（19 个模块）：M1 / M2 / M3 / M4 / M5 / M6 / M7 / M10 / M14 / M15 / M16 / M17 / M18 / M19 / M21 / M23 / M25 / M29 / M30

两条纪律钉着这份清单：**不建桩、不建空壳**（未实现就是不存在，见 `pmstudio/bootstrap/missing.py`），
且清单与实现两向对齐——`tests/bootstrap/test_missing_modules.py` 对着代码核，
`tests/bootstrap/test_repo_docs.py` 对着本文件的这两行核。

R2 起接手的是卡片机制补全（纠正路径、分歧卡、信息卡、`manual` / `rollback` 写入）与讨论区；
缺口与它们的触发条件记在 `DECISIONS.md` 每轮的"账"里。

## 怎么跑

```bash
cd backend
uv sync                                      # 运行时依赖 + dev 组（pytest / pytest-asyncio / ruff）
uv run pytest                                # 全部测试
uv run ruff check .                          # 静态检查
uv run python -m pmstudio.contracts.frozen   # 打印契约冻结表并核对形状锁
```

要求 Python 3.13+（见 `backend/.python-version`）。运行时依赖只有五个：
pydantic / litellm / fastapi / uvicorn / python-dotenv。

**没有 API Key 也能全绿**：测试与本地 demo 注入 `FakeHarness`，同一套链路不打网络。
要接真模型：把 `backend/.env.example` 复制成 `backend/.env` 填上密钥——**条目里只写变量名
（`api_key_env`），密钥本身既不进仓库也不进注册条目**；HTTP 入口启动时会读一次 `backend/.env`。
真链路冒烟（不给密钥就自动跳过）：

```bash
DEEPSEEK_API_KEY=sk-... uv run pytest tests/bootstrap/test_live_model_smoke.py -v
```

HTTP 接口（R1.5）在 `backend/web_api/`：建项目、开轮、提交卡片、读文档与版本、`/events/stream` 推 SSE。

## 文档在哪

**唯一真相源**，冲突时以 `CONTRACTS.md` 为准：

| 文档 | 回答什么 |
|---|---|
| `.codex/docs/PRD.md` | 做什么、为什么这么做、验收条件 |
| `.codex/docs/CONTRACTS.md` | **字段级唯一真相源**：每个字段谁写谁读、有哪些不变量 |
| `.codex/docs/directory.md` | 代码怎么摆、13 条依赖边、里程碑 R0–R7 |
| `.codex/docs/DECISIONS.md` | 每条裁决怎么拍的、为什么、还欠哪些账 |

`docs/redesign/`：DDD + TDD 重设计（聚合、端口签名锁、可运行性、P0–P6 路线）。
**状态：设计稿，"与现行目录结构的取舍"待拍板**（见 `DECISIONS.md` 的待拍板一节）。

契约有三层真相源——**文档面**（`CONTRACTS.md`）、**机器面**（`contracts/frozen.py` 的形状锁与
`CR-xxx` 变更记录）、**实现面**（`contracts/` 里的模型）。三层之间由测试机器钉住：
`tests/contracts/test_freeze.py`（改契约不写文档 → 红）。

记号约定：**`M1–M30` 是模块，`R0–R7` 是里程碑**（步骤写 `R0.1–R0.6`），两者不混用。
