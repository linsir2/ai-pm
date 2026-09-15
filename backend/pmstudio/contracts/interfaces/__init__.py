"""层间接口：每层对外暴露什么（CONTRACTS §5.3）。

三条规矩：

1. **层与层之间不 import 实现**：调用方依赖这里的 Protocol，被调方实现它，两边在
   `bootstrap/` 接起来（directory.md §8）。
2. **入参与返回只能是契约对象或它们的集合**——"传一段自然语言给下一个模块"就是设计漏了。
3. **IO 一律 async**（P3）。纯计算（`contracts/invariants.py` 里的函数）是同步的。

`L7 存储底座` **故意没有 Protocol**：文档里只写了"各实体的读写"，没有一条签名。谁先当消费者，
谁就定自己需要的那一小块（M0.3 定流水、M0.4 定工作台、M1 定业务读写），见 directory.md §11。

**当前没有调用方的方法**（先声明、M1 评审时删）：`MemoryPort.retrieve`、`MemoryPort.invalidate`。
"""
