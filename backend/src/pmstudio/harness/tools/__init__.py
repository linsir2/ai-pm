"""工具层：工具**怎么被声明与登记**。

住在这里的只有"声明与登记"这件事：数据类（`ToolDefinition`）、协议（`ToolCatalog`）、
TOML 实现（`TomlToolCatalog`）。**四个工具的应用实现不在这里**——它们是应用工具
（联网检索 / 抓页 / 解析文档），950 行，在 `infrastructure/tools/`。
"""
