"""供应商实现：把 `model/contracts.py` 的协议落到具体 SDK 上。

**为什么实现在 harness 里而不在 infrastructure**：控制平面知道"调谁"是它的职责
（把 JSON 递出去、把错误分类、把回包归一化），不知道"产品有哪些章节"。
不搬进来，`ModelGateway` 就只是个协议，实现仍在仓外——"模型层可独立抽出"
这个目标等于没达成。换 provider 要改 harness 是真的代价，但与"harness 长出业务词汇"
是完全不同性质的问题。
"""
