"""harness 的模型能力层。

住在这里的八件事，判据都是"只因为模型会胡说八道而存在"：

- `contracts`：调用契约（请求 / 回复 / 网关协议 / 留样协议 / 提示词库协议）
- `errors`（在上一级 `harness/errors.py`）：运行时错误
- `failure`：失败分类的单点权威表（什么算抖动、什么该停）
- `jsonio`：回包前置判定（剥围栏 / 是不是 JSON）
- `normalize`：输出归一化引擎 + 三档 parse + 溢出与丢弃报告
- `envelope`：`02-编排与数据.md §4.4` 的六字段信封
- `prompt_log`：留样的提示词预算与截断策略
- `structured`：结构化调用模板（`call_structured`，只做一次尝试）

**为什么环境变量写在这里**（2026-10-04 · M4b）：本包里 `failure` 与 `providers/litellm`
都要 import litellm，而 litellm 在导入时会去**网上拉价目表**（实测 9.3 秒，且依赖网络）。
关掉它降到 3.4 秒。写在本包的 `__init__` 里，是因为 Python 保证子模块导入前先跑它——
放哪个具体模块里都不够（谁先被导入是不确定的）。
"""

import os

#: 别在导入时去网上拉价目表。`setdefault` 而不是赋值：外部显式设了就算外部的。
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

