"""这一轮想联网查什么：分析者提名的一条（带"为什么它需要**外部客观事实**"）。

它只是**提名**。真正去查要等用户在复述卡上勾——两道闸门见 `02-编排与数据.md` §11.0.29
与 `docs/plans/2026-09-30-P17b-C2b下半-联网检索闭环.md` §2.2。
"""

from dataclasses import dataclass

from pmstudio.domain.shared.errors import ShapeError

#: 一轮最多提名几条。提示词里写着这个数（"最多 3 条"），解析层丢掉多出来的那几条并留痕
#: （不判死整轮：真跑一次重装几十秒，为一条多写的检索词不值）。
MAX_WEB_QUERIES = 3


@dataclass(frozen=True, slots=True)
class WebQuery:
    """一条检索词 + 它为什么值一次联网。"""

    query: str
    why: str

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ShapeError("检索词不能为空")
        if not self.why.strip():
            # "为什么"不是客套：它是**不许联网清单**的落点（用户已说过的 / 产品取舍不查）
            raise ShapeError("检索词必须写清为什么要查外部事实", query=self.query)
