"""四个 agent 的结构化调用声明不许漂移。

`schema_name` 现在由 `StructuredCall.schema_name`（= 报文类名）派生，不再各处硬编码。
今天 5 处硬编码是 `SeatPacket` / `ClaimsPacket` / `IntegrationPacket` / `DispositionsPacket` /
`MemoryPacket`——**全等于类名**，所以派生是安全的；这个文件把那条等价关系钉住，
免得将来改了类名而 `response_format` 里的 `schema_name` 悄悄对不上供应商。
"""

from pmstudio.application.agents import (
    analyzer,
    inquirer,
    integrator,
    memory_curator,
    schemas,
    seat,
)

#: (模块名, definition 或 CALL, purpose, 今天硬编码的 schema_name)
# 已重构的 agent 用 definition；未重构的暂时用 CALL
CALLS = [
    ("analyzer", analyzer.ClaimAnalyzer.definition, "claims", "ClaimsPacket"),
    ("seat", seat.SeatReviewer.definition, "seat_review", "SeatPacket"),
    ("inquirer", inquirer.Inquirer.definition, "inquiry", "InquiryPacket"),
    ("integrator", integrator.Integrator.definition, "integration", "IntegrationPacket"),
    (
        "integrator.dispositions",
        integrator.Integrator.DISPOSITIONS_CALL,
        "opinion_dispositions",
        "DispositionsPacket",
    ),
    ("memory_curator", memory_curator.MemoryCurator.definition, "memory_curation", "MemoryPacket"),
]


def test_schema_name_still_equals_what_the_hardcoded_table_used_to_say():
    for name, call, _, expected in CALLS:
        if hasattr(call, "schema_name"):
            assert call.schema_name == expected, name
        else:
            # AgentDefinition: schema_name 由 packet.__name__ 派生
            assert call.packet.__name__ == expected, name


def test_purpose_matches_the_declared_purpose_constant():
    """`purpose` 同时是留样归堆的键与 `PacketParseError.purpose`——打错一个字母
    就会让留样归错堆、重装指引对不上，所以它必须等于该调用自己声明的那个常量。"""

    for name, call, declared, _ in CALLS:
        assert call.purpose == declared, name


def test_every_call_uses_the_rules_the_product_declares():
    """所有报文共用一张规则表（`schemas._RULES`）——补壳与黑名单不许各报文各一份。"""

    for name, call, _, _ in CALLS:
        if hasattr(call, "rules"):
            assert call.rules is schemas.RULES, name
        # AgentDefinition 不含 rules（rules 是 StructuredCall 的构造细节）


def test_packet_classes_are_the_ones_the_modules_used_before():
    for name, call, _, expected in CALLS:
        assert call.packet.__name__ == expected, name


def test_dispositions_is_a_separate_call_not_a_flag():
    """归因补交代是**另一次调用**（P23 证明重写整包会"补一批又漏一批"），
    所以它有自己的 purpose / 报文 / 提示词——不许被合回整合那一趟。"""

    main, small = integrator.Integrator.definition, integrator.Integrator.DISPOSITIONS_CALL
    assert main.purpose != small.purpose
    assert main.packet is not small.packet
    assert main.prompt != small.prompt


def test_call_prompt_is_the_module_prompt_constant_not_a_repeated_literal():
    """`CALL.prompt` 与 `PROMPT` 曾经各写一遍字面量（`memory_curator` 里甚至声明在
    `PROMPT` 之前，只能重复）——改名 `.md` 文件时那种重复会让人漏改一处。"""

    assert analyzer.ClaimAnalyzer.definition.prompt == "analyzer"
    assert seat.SeatReviewer.definition.prompt == "seat_review"
    assert inquirer.Inquirer.definition.prompt == "inquiry"
    assert integrator.Integrator.definition.prompt == "integrator"
    assert integrator.Integrator.DISPOSITIONS_CALL.prompt == "opinion_dispositions"
    assert memory_curator.MemoryCurator.definition.prompt == "memory_curator"


def test_kind_is_pinned_per_call():
    """`kind` 是 §4.4 的信封字段，今天与 `purpose` 同值但**概念不同**（一个是"这一包是什么"，
    一个是"留样归堆的键"）。钉住它，防止将来有人以为它只是 purpose 的别名就随手改。"""

    assert analyzer.ClaimAnalyzer.definition.kind == "claims"
    assert seat.SeatReviewer.definition.kind == "seat_review"
    assert inquirer.Inquirer.definition.kind == "inquiry"
    assert integrator.Integrator.definition.kind == "integration"
    assert integrator.Integrator.DISPOSITIONS_CALL.kind == "opinion_dispositions"
    assert memory_curator.MemoryCurator.definition.kind == "memory_curation"


def test_every_agent_returns_the_envelope_as_the_carrier_of_its_arrival_report():
    """`overflow` / `discarded` / `basis` **只有信封一处存储**。

    领域投影上的同名字段必须是转发属性，不能是第二份字段——两份会漂移，而漂移之后
    "留痕里写的溢出条数"与"信封里报的溢出条数"对不上，没人查得出来。

    查法要看**真实结构**（`dataclasses.fields`），不能看 `__dict__`：`slots=True` 的
    dataclass 根本没有 `__dict__`，那种写法恒真、等于没测。
    """

    from dataclasses import fields

    from pmstudio.harness.model.normalize import Discarded, Overflow

    carriers = [
        seat.SeatOutcome,
        inquirer.InquiryOutcome,
        integrator.IntegrationOutcome,
        integrator.DispositionOutcome,
        memory_curator.CuratorOutcome,
    ]
    for outcome in carriers:
        names = {item.name for item in fields(outcome)}
        assert "envelope" in names, f"{outcome.__name__} 没有 envelope 字段"
        for duplicated in ("overflow", "discarded", "basis"):
            assert duplicated not in names, (
                f"{outcome.__name__} 自己存了一份 {duplicated}——信封之外不许有第二处"
            )

    # 转发属性真的指向信封：属性读的是 `self.envelope.overflow`，所以换一个只带信封的替身。
    class Envelope_:
        overflow = (Overflow(container="a", kept=1, dropped=1),)
        discarded = (Discarded(container="a", index=1),)

    class Carrier:
        envelope = Envelope_()

    assert seat.SeatOutcome.overflow.fget(Carrier()) == Envelope_.overflow
    assert seat.SeatOutcome.discarded.fget(Carrier()) == Envelope_.discarded

