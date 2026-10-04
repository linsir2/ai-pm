"""harness 的模型能力层。

住在这里的七件事，判据都是"只因为模型会胡说八道而存在"：

- `contracts`：调用契约（请求 / 回复 / 网关协议 / 留样协议 / 提示词库协议）
- `errors`（在上一级 `harness/errors.py`）：运行时错误
- `failure`：失败分类的单点权威表（什么算抖动、什么该停）
- `jsonio`：回包前置判定（剥围栏 / 是不是 JSON）
- `normalize`：输出归一化引擎 + 三档 parse + 溢出与丢弃报告
- `envelope`：`02-编排与数据.md §4.4` 的六字段信封
- `structured`：结构化调用模板（`call_structured`，只做一次尝试）
"""
