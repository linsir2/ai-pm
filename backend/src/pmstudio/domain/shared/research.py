"""研究（P3b-1）**跨上下文**共享的那一点词汇：产物上限与来源卡格式。

为什么单独放到 `shared/`：上限与卡格式被两个上下文同时用——`exploration` 负责
"一趟研究产出几条来源"，`round` 负责"一轮最多挂几张来源卡"。
让 round 去 import exploration 会破"领域上下文互不 import"那条规矩
（`tests/architecture/test_domain_imports.py` 钉着），所以两边都认这一份。

`source_card_text` 也在这里：它是**账本里那条 E 的正文**，而 E 是全局的；
渲染层与用例只是复用它，不各拼一遍（拼两遍必然有一处先漂）。
"""

#: 一趟研究最多留几条来源。**产物上限**，也是"一轮最多挂几张来源卡"的上限
#: （挂的是同一批东西，两个数不一致就会出现"研究出 5 张、只能挂 3 张"的怪事）。
MAX_RESEARCH_SOURCES = 5

#: 一趟研究最多几条结论。超过这个数的"结论"多半是复述来源，不是结论。
MAX_RESEARCH_FINDINGS = 12

#: 一趟研究最多几个开放问题（与席位的疑问同一用途：进 s11 之前的候选）。
MAX_RESEARCH_OPEN_QUESTIONS = 5

#: 研究主题的字数上限。500 是"一段话说清一个问题"的量级；
#: 再长就不是主题而是需求文档，应该走主闭环。
MAX_RESEARCH_TOPIC_CHARS = 500

#: **一张来源卡**（标题 + 网址 + 摘要，`source_card_text` 拼出来的那一整串）的上限。
#: 卡既是账本里那条 `external` 出处的正文，也是进提示词的东西，所以上限落在**整串**上
#: （只卡摘要的话，长标题 / 长网址会让单卡突破预算）。
#: **上下文预算是它的算术结果**：`MAX_RESEARCH_SOURCES × 本值` = 4000 字，
#: 不另立旋钮（两处写死必然漂）。
RESEARCH_SOURCE_CARD_CHARS = 800


def source_card_text(*, title: str, url: str, excerpt: str) -> str:
    """一张来源卡的正文：**账本里那条 E 的 text 与提示词里渲染的都是这一串**。

    两处必须逐字一致：用户点编号看到的是它，模型读到的也是它。
    """

    return f"{title}\n{url}\n{excerpt}"
