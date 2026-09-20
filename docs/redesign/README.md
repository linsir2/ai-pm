# PM Studio · 重新设计规格（DDD + TDD）

> 分支：`refactor/ddd-redesign`
> 状态：设计稿 v1 · 待评审
> 原则：**功能点与模块编号保留**（原 PRD 附录 A 的功能列表没问题），
> 重构的是「行为的家」（聚合）、「契约的可执行性」（端口签名锁）与「可运行性」（无密钥降级）。

## 设计文档索引

| 文档 | 内容 |
|---|---|
| `01-features.md` | 功能清单（按限界上下文重组，保留 M1–M30 编号） |
| `02-contracts.md` | 数据契约模型（聚合 / 实体 / 值对象 / 端口，v2） |
| `03-dataflow.md` | 数据流（主闭环 / 讨论区 / 事件流） |
| `04-milestones.md` | 里程碑路线图（P0–P6，DDD + TDD） |

## 核心设计决策（相对旧实现的变更）

1. **保留**：17 个契约（C1–C17）的语义、8 层架构、30 个模块编号、黑板 + 事件流通信、SQLite 存储。
2. **D1 修复**：`write_document(trigger, payload)` 收敛为两参；M20 内部从黑板读 group、按 payload 取提案——协议、实现、契约文档三方对齐。
3. **D4 修复**：删除 `object | None` 装配，应用服务构造注入具体端口类型；pyright 严格模式。
4. **D8 修复**：领域逻辑收进聚合根（Project / Round / DiscussionRound / Memory / RegistryEntry），不变量有唯一「家」。
5. **新增「端口签名锁」**：`inspect.signature` 比对协议与实现，不一致测试即失败；配合 `frozen.py` 形状锁 + `CR-xxx` 变更记录。
6. **可运行性（D6/D7）**：模型不可用 → 503 + Fake/Local Harness 降级路径，无 `DASHSCOPE_API_KEY` 也能全绿跑通。
