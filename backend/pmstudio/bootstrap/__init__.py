"""装配根：**唯一允许 import 具体实现的地方**（directory.md §3）。

装配在这里做完之后，交出去的只有接口：调用方拿 `contracts/interfaces/` 里的 Protocol，
不需要知道背后是谁。
"""
