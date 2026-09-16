# PM Studio

把 PM 的自然语言构思，逐步收口成双方可确认的**产品共识文档**。单人使用，本地运行，无登录、无多租户。

## 现在到哪了

**R0（契约与骨架）已完成，R1（最小生成闭环）未开工。** 所以当前**还不能作为应用运行**，能跑的是测试。

- 30 个模块里只有 M11（黑板）与 M12（事件流）有实现，外加 L7 存储底座；其余在
  `backend/pmstudio/bootstrap/missing.py` 的缺失清单里——这是"不建桩、不建空壳"的纪律，不是烂尾。
- 17 个契约（C1–C17）已冻结：形状锁与变更记录在 `backend/pmstudio/contracts/frozen.py`，
  改契约必须先写变更记录（`CR-xxx`），由测试执行这条规矩。

## 怎么跑

```bash
cd backend
.venv/bin/python -m pytest                     # 全部测试
.venv/bin/ruff check .                         # 静态检查
.venv/bin/python -m pmstudio.contracts.frozen  # 打印契约冻结表并核对形状锁
```

要求 Python 3.13+（见 `backend/.python-version`），唯一的运行时依赖是 pydantic。

## 文档在哪

`.codex/docs/`：`PRD.md`（做什么、为什么）→ `CONTRACTS.md`（**字段级唯一真相源**）→
`directory.md`（代码怎么摆、里程碑）→ `DECISIONS.md`（裁决背景与未决欠账）。冲突时以 `CONTRACTS.md` 为准。

记号约定：**`M1–M30` 是模块，`R0–R7` 是里程碑**（步骤写 `R0.1–R0.6`），两者不混用。
