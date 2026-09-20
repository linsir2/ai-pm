# 01 · 功能清单（按限界上下文重组）

功能点沿用原 PRD 附录 A 的模块编号（M1–M30），但按 DDD 限界上下文重新归组，
每项标注：模块编号、能力描述、所属聚合、依赖的契约、里程碑归属。

## BC-PROJECT · 项目与文档

| 模块 | 功能 | 描述 | 里程碑 |
|---|---|---|---|
| M1 | 项目容器（前端） | 建项目、项目列表、按模板实例化 9 字段空骨架 | P1 |
| M2 | 文档编辑器（前端） | 文档树渲染、只读视图、手动编辑（一次保存 = 一版） | P1 / P2 |
| M3 | 范围选择器（前端） | 目录树圈选 schema 字段 → 本轮写范围 | P1 |
| M20 | 文档写入 | `submit` / `manual` / `rollback` 三路径；版本快照冲突检测；写入即发 `doc.changed` | P0 已有 / 对齐 |
| M21 | 导出 | 导出格式与返回形状（真做时再定） | P6 |

聚合根：`Project`（拥有 Document + Blocks + Versions + Scope）。
写入唯一入口：`DocumentWriterPort.write_document(trigger, payload)` —— 两参，内部从黑板读 group。

## BC-ROUND · 协作轮

| 模块 | 功能 | 描述 | 里程碑 |
|---|---|---|---|
| M5 | 卡片界面（前端） | 卡片渲染、回应（按钮 + 打字）、整组冻结 | P1 |
| M8 | 共识生成器 | 理解卡 / 分歧卡 / 信息卡内容生成（调 Harness） | P0 已有 |
| M9 | 卡片组装 | 卡片 ≤5 张一组、填充卡单独成批、proposal_states 校验 | P0 已有 |
| M11 | 黑板 | round / context / claims / card_group / confirmed 五区块 | P0 已有 |
| M12 | 事件流 | 8 事件、窄发布白名单、流水落库 | P0 已有 |
| M28 | 成稿器 | 把 kept 提案编排成 `writeDocument` 调用 | P0 已有 |

聚合根：`Round`（拥有 CardGroup；黑板 round / card_group 区块）。
状态机：`assembling → restating → working → awaiting_user → drafting → writing → done/failed`。

## BC-DISCUSSION · 讨论区

| 模块 | 功能 | 描述 | 里程碑 |
|---|---|---|---|
| M4 | 讨论区（前端） | 发言流、角色提示、「整理成提案」按钮（唯一按钮） | P3 |
| M6 | 角色注册表 | 角色定义与可用角色（≤4 人） | P3 |
| M7 | 讨论编排器 | 讨论轮状态机、逐条发言、`allowed_entries` 过滤 | P3 |
| M29 | 思路整理器 | 发言 → IdeaCandidate（pending/selected/discarded/consumed） | P3 |

聚合根：`DiscussionRound`（拥有发言流；产物 IdeaCandidate 经端口交主闭环）。

## BC-MEMORY · 记忆与上下文

| 模块 | 功能 | 描述 | 里程碑 |
|---|---|---|---|
| M13 | 上下文组装器 | 多来源组装（文档 / 简报 / 记忆 / 材料 / 讨论 / 用户输入），优先级排序 | P0 已有 / P4 增强 |
| M14 | 项目简报 | 四段式简报、逐条确认、doc.changed 驱动失效 | P4 |
| M15 | 长对话管理 | 消息 + 滚动摘要 | P4 |
| M16 | 检索 | 按块 / 全文检索，材料并入上下文 | P4 |
| M17 | 反馈与画像 | 候选记忆 → 晋升 / 失效 / 退役 | P4 |

聚合根：`Memory` / `Conversation`。`doc.changed` 事件驱动失效链。

## BC-TOOLS · 工具

| 模块 | 功能 | 描述 | 里程碑 |
|---|---|---|---|
| M10 | 工具调用决策 | 是否需要工具、挑哪个（经 Registry + 权限） | P5 |
| M18 | 联网搜索 | 搜索结果 → Material Packet（带 credibility + ref） | P5 |
| M19 | 材料上传与解析 | 上传文件 → 解析 → Material Packet | P5 |

聚合根：无独立聚合，工具作为 `HarnessPort` / `RegistryPort` 的适配器存在。

## BC-HARNESS · 模型接入

| 模块 | 功能 | 描述 | 里程碑 |
|---|---|---|---|
| M22 | 模型接入 | `complete(model_ref, messages, response_format)` | P0 已有 |
| M23 | 角色权限 | `can_use(role_id, tool_id)` / `can_enter(role_id, entry)`，两档 allow/deny | P5 |
| M24 | 预算与裁剪 | `estimate_tokens` 启发式、上下文裁剪 | P0 已有 |
| M25 | 观测 | Trace、主张留痕（ClaimDigest） | P6 |
| M26 | 重试与降级 | 重试、模型不可用降级（Fake/Local Harness） | P0 已有 |

## BC-REGISTRY · 注册中心

| 模块 | 功能 | 描述 | 里程碑 |
|---|---|---|---|
| M27 | 资产注册中心 | 角色 / 模型 / 模板 / 工具 / Prompt 条目，kind 校验，状态变更发 `registry.updated` | P0 已有 |
| M30 | 复利验证器 | 候选记忆验证 → 晋升 / 退役 | P6 |

## 功能组合（跨上下文，原 PRD §7.3 保留）

- 组合 A（R1）：建项目 → 开轮 → 理解卡 → 填充卡 → 写入 → 版本可见（**P0/P1 目标**）
- 组合 B（R2）：分歧 / 信息卡 → 纠正路径 → 版本冲突（P2）
- 组合 C（R5）：联网 / 上传 → 工具调用（P5）
- 组合 D/E（R6–R7）：复利验证 / 观测复盘（P6）
