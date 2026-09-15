"""C1–C17 跨层契约对象。文件名与 CONTRACTS.md 的契约一一对应。

C4 不在这里：它是编排层的内部通用语，只跨 agent、不跨层（directory.md §3.1）。
"""

# 契约文件与编号的对应（给读代码的人一张地图，也是清点测试的依据）：
#   scope.py        C1   Scope
#   document.py     C2   Block / Document ・ C3 Document Version ・ 三种写入 payload
#   citation.py     C15  Citation
#   material.py     C5   Material Packet
#   idea.py         C6   Idea Candidate
#   card.py         C7   Card ・ CardOption ・ Proposal ・ CardAnswer
#   card_group.py   C8   Card Group
#   memory.py       C9   Memory
#   registry.py     C10  Registry Entry
#   trace.py        C11  Trace ・ ClaimDigest
#   discussion.py   C14  Discussion Round ・ Utterance
#   project.py      C16  Project
#   conversation.py C17  Message ・ Summary
#   prompt.py       —    PromptMessage（complete 的入参项）
#   results.py      —    层间接口的返回形状
