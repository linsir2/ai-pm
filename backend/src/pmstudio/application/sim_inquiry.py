"""模拟调研的装配件（M10）：窄词表 / 去重 / source_ref / 多块合成。

判断逻辑全在这里，一处说了算；调用方（RunSimInquiry / KeepSimInquiry）只负责
"拿账本与文档算集合、把丢条记进 `Round.dropped`"。
"""

import re

from pmstudio.application.dto import MaterialChunk, ParsedMaterial

ORIGIN_SIMULATION = "simulation"
PARSER_ID_SIMULATION = "simulation"
PARSER_VERSION_SIMULATION = "1"

#: 数字断言正则：与 `exploration._NUMERIC` 同形但各判各的包（inquiry 包判它的
#: 假设/盲区，sim 包判它的切片/假设/盲区；形状不同，合并不了。等第三个消费者
#: 出现再抽公共处——现在抽就是为通用而通用）。
SIM_NUMERIC = re.compile(r"\d+\s*(%|％|成|倍)|占比|百分比|比例|数据显示|调查显示|大多数|多数人")


def sim_numeric_hit(text: str) -> bool:
    """这段文本有没有数量断言（模拟推演不许出数字，D1 筛#4）。"""

    return SIM_NUMERIC.search(text) is not None

#: 身份词窄表：命中即"这条在装人"。故意窄——单字"男/女/岁"误杀太大不收，
#: 数字断言是 `_NUMERIC`（exploration 那边）的活，这里只抓身份。
_SIM_IDENTITY_HINTS = frozenset(
    {
        "年龄",
        "性别",
        "女性",
        "男性",
        "地域",
        "学历",
        "收入",
        "民族",
        "一线城市",
        "白领",
        "宝妈",
        "农民工",
        "中老年",
        "年轻人",
    }
)

#: 剥首尾标点（与 assembly 的 `_asked_how` 同形——跨模块 import 私有函数是骗人的约定，
#: 3 行通用文本操作，自己写一份）。
_EDGE_PUNCTUATION = "？?！!。.，,、；;：: \t\n\u3000“”\"'‘’（）()《》<>【】[]"


def sim_demographic_hits(text: str) -> tuple[str, ...]:
    """文本里命中了哪些身份词（空 = 干净）。只做子串匹配：中文没有词边界。"""

    return tuple(sorted(hint for hint in _SIM_IDENTITY_HINTS if hint in text))


def normalize_statement(text: str) -> str:
    """陈述归一化：压空白 + 剥首尾标点（去重比的是"同一句话"，不是同一串字节）。"""

    return " ".join(text.split()).strip(_EDGE_PUNCTUATION)


def duplicate_of(
    statement: str,
    *,
    doc_keys: frozenset[str],
    open_keys: frozenset[str],
) -> str | None:
    """这句话是不是已有内容的复述：先比文档条目，再比 s11 活跃条目；都不是返回 None。

    两个集合由调用方算好传进来（文档 `iter_document_entries` + `open_item_keys` 的归一化），
    这里只做"归一化后相等"的判定——机械判据，不是语义去重（近义改写判不住，F33 同款）。
    """

    normalized = normalize_statement(statement)
    if normalized in doc_keys:
        return "document"
    if normalized in open_keys:
        return "open_item"
    return None


def sim_source_ref(round_id: str, chosen: tuple[str, ...]) -> str:
    """收下材料的去重键：同选择同 ref（重复收下幂等），换勾选即新材料。"""

    return f"simulation:inquiry:{round_id}#{'+'.join(sorted(chosen))}"


def synthesize_sim_material(
    *,
    round_id: str,
    chosen: tuple[str, ...],
    hypotheses: tuple[dict, ...],
    unknowns: tuple[dict, ...],
    now: str,
) -> tuple[ParsedMaterial, bytes]:
    """勾中的假设/盲区合成一份多块材料（单 chunk 信封塞不下多条，这个函数是唯一入口）。

    顺序按 artifact 原序（稳定阅读序），不按勾选顺序；chunk 文固定三段
    （陈述 + 从哪推的 + 怎么推翻/去哪验证）——渲染只有这一处，材料库与抽屉看到同一份。
    """

    wanted = set(chosen)
    texts: list[str] = []
    for item in hypotheses:
        if item["local_id"] in wanted:
            basis = "、".join(str(ref) for ref in item.get("based_on") or ())
            texts.append(
                f"待验证假设（模拟推演）{item['local_id']}：{item['statement']}\n"
                f"从哪推的：{basis or '凭空推演（无据）'}\n"
                f"怎么被推翻：{item['falsification']}\n"
                f"需要什么真实证据：{item['evidence_needed']}"
            )
    for item in unknowns:
        if item["local_id"] in wanted:
            texts.append(
                f"待验证假设（模拟推演）{item['local_id']}：{item['statement']}\n"
                f"为什么值得问：{item['why_it_matters']}\n"
                f"怎么去验证：{item['how_to_find_out']}"
            )
    source_ref = sim_source_ref(round_id, chosen)
    chunks = tuple(
        MaterialChunk(
            chunk_id=f"{source_ref}#c{index:02d}",
            ordinal=index,
            text=text,
            heading_path=(),
            anchor="模拟推演",
        )
        for index, text in enumerate(texts)
    )
    body = "\n\n".join(texts)
    content = body.encode("utf-8")
    parsed = ParsedMaterial(
        origin=ORIGIN_SIMULATION,
        title=f"模拟调研 · {now[:10]} · {len(texts)} 条推演",
        source_ref=source_ref,
        media_type="text/plain",
        bytes_len=len(content),
        chunks=chunks,
        parser_id=PARSER_ID_SIMULATION,
        parser_version=PARSER_VERSION_SIMULATION,
    )
    return parsed, content
